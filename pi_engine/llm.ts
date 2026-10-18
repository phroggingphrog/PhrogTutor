/**
 * pi-tutor: DeepTutor on the pi SDK.
 *
 * Model calls (pi-ai `stream`): DeepTutor's single-shot calls (BaseAgent.call_llm / stream_llm, co-writer, book...).
 * Agent runs (pi-coding-agent `createAgentSession`): each DeepTutor agent loop (chat, solve, research, question,
 *   visualize, partners...) is one pi agent session. pi calls the model, runs DeepTutor's tools as pi custom tools,
 *   and loads pi plugins (extensions, skills, packages) from agent-dir like the pi CLI does. DeepTutor's Python loop
 *   still decides every next step (its tool dispatch, step notes, settlement, ask-user pauses): the built-in
 *   "deeptutor" extension feeds DeepTutor's exact transcript to each request (context_with_system) and continues or
 *   ends the run as DeepTutor says (turn_end).
 *
 * One-shot:  stdin = one OpenAI chat-completions request; stdout = one `chat.completion.chunk` JSON per line.
 * --serve:   one long-lived process; JSON lines tagged with "rid":
 *   in  {rid, req}                single model call   -> chunks, then {rid, end}
 *   in  {rid, kind:"agent", req}  start an agent run  -> chunks + {rid, round_end} per model call
 *   in  {rid, kind:"next", req}   DeepTutor's next context (with its tool results) -> next round
 *   in  {rid, kind:"end"}         DeepTutor's loop finished -> {rid, end}
 * Model access: the "omni" provider in agent-dir/models.json (OmniRoute; key never printed).
 */
import { stream } from "@earendil-works/pi-ai/compat";
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = join(HERE, "agent-dir");
const omni = JSON.parse(readFileSync(join(AGENT_DIR, "models.json"), "utf8")).providers.omni;
const out = (o: any) => process.stdout.write(JSON.stringify(o) + "\n");
const tagged = (rid: string) => (c: any) => out({ ...c, rid });
const finishMap: Record<string, string> = { stop: "stop", length: "length", toolUse: "tool_calls", error: "stop", aborted: "stop" };
const ZERO = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

// ---------- OpenAI <-> pi conversions ----------

function buildModel(req: any): any {
	// Any OmniRoute model or combo name passes through (DeepTutor's own catalog picks it); known ones get their limits/costs.
	const id: string = req.model || process.env.PI_TUTOR_MODEL || "openai/luna";
	const spec = omni.models.find((m: any) => m.id === id) ?? {};
	return {
		id, name: spec.name ?? id, api: "openai-completions", provider: "omni", baseUrl: omni.baseUrl,
		reasoning: Boolean(req.reasoning_effort), input: spec.input ?? ["text"],
		cost: spec.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: spec.contextWindow ?? 128000, maxTokens: req.max_tokens ?? req.max_completion_tokens ?? 16000,
	};
}

const text = (c: any): string =>
	typeof c === "string" ? c : Array.isArray(c) ? c.map((p: any) => (p.type === "text" ? p.text : "")).join("") : "";
const userContent = (c: any): any =>
	typeof c === "string"
		? c
		: (c ?? []).map((p: any) => {
				if (p.type !== "image_url") return { type: "text", text: p.text ?? "" };
				const m = /^data:([^;]+);base64,(.*)$/.exec(p.image_url?.url ?? "");
				return m ? { type: "image", mimeType: m[1], data: m[2] } : { type: "text", text: p.image_url?.url ?? "" };
			});

/** OpenAI messages -> pi system prompt + messages. Leading system messages form the prompt; a later one
 * (DeepTutor's step notes) stays in place as a user turn, because pi has a single system prompt. */
function convert(req: any, modelId: string): { system: string; messages: any[] } {
	const system: string[] = [];
	const messages: any[] = [];
	const toolNames = new Map<string, string>();
	for (const m of req.messages ?? []) {
		const isSystem = m.role === "system" || m.role === "developer";
		if (isSystem && messages.length === 0) system.push(text(m.content));
		else if (isSystem) messages.push({ role: "user", content: `[System note]\n${text(m.content)}`, timestamp: Date.now() });
		else if (m.role === "user") messages.push({ role: "user", content: userContent(m.content), timestamp: Date.now() });
		else if (m.role === "assistant") {
			const content: any[] = [];
			const t = text(m.content);
			if (t) content.push({ type: "text", text: t });
			for (const tc of m.tool_calls ?? []) {
				toolNames.set(tc.id, tc.function.name);
				let args = {};
				try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
				content.push({ type: "toolCall", id: tc.id, name: tc.function.name, arguments: args });
			}
			messages.push({ role: "assistant", content, api: "openai-completions", provider: "omni", model: modelId, usage: ZERO,
				stopReason: m.tool_calls?.length ? "toolUse" : "stop", timestamp: Date.now() });
		} else if (m.role === "tool") {
			messages.push({ role: "toolResult", toolCallId: m.tool_call_id, toolName: toolNames.get(m.tool_call_id) ?? "tool",
				content: [{ type: "text", text: text(m.content) }], isError: false, timestamp: Date.now() });
		}
	}
	return { system: system.join("\n\n"), messages };
}

const toolSpecs = (req: any) =>
	(req.tools ?? []).map((t: any) => ({ name: t.function.name, description: t.function.description ?? "", parameters: t.function.parameters ?? { type: "object", properties: {} } }));

function streamOptions(req: any): any {
	const o: any = { apiKey: omni.apiKey };
	if (req.temperature != null) o.temperature = req.temperature;
	if (req.reasoning_effort) o.reasoningEffort = req.reasoning_effort;
	return o;
}

/** pi assistant-message events -> OpenAI chunks for one model call. */
function chunkWriter(modelId: string, write: (c: any) => void) {
	const id = `pi-${Date.now()}`;
	const created = Math.floor(Date.now() / 1000);
	let toolIndex = 0;
	const emit = (delta: any, finish: string | null = null, usage: any = undefined) =>
		write({ id, object: "chat.completion.chunk", created, model: modelId, choices: [{ index: 0, delta, finish_reason: finish }], ...(usage ? { usage } : {}) });
	emit({ role: "assistant", content: "" });
	return {
		event(ev: any) {
			if (ev.type === "text_delta") emit({ content: ev.delta });
			else if (ev.type === "thinking_delta") emit({ reasoning_content: ev.delta });
			else if (ev.type === "toolcall_end") {
				const tc = ev.toolCall;
				emit({ tool_calls: [{ index: toolIndex++, id: tc.id, type: "function", function: { name: tc.name, arguments: JSON.stringify(tc.arguments ?? {}) } }] });
			}
		},
		finish(msg: any) {
			// OpenAI shape: prompt_tokens includes cached ones; cached_tokens is what DeepTutor's "Cache hit rate" reads.
			const u = msg?.usage ?? {};
			const cached = u.cacheRead ?? 0;
			const prompt = (u.input ?? 0) + cached + (u.cacheWrite ?? 0);
			emit({}, finishMap[msg?.stopReason] ?? "stop", { prompt_tokens: prompt, completion_tokens: u.output ?? 0,
				total_tokens: prompt + (u.output ?? 0), prompt_tokens_details: { cached_tokens: cached } });
		},
	};
}

// ---------- single model call ----------

async function handle(req: any, write: (chunk: any) => void): Promise<string> {
	const model = buildModel(req);
	const { system, messages } = convert(req, model.id);
	const tools = toolSpecs(req);
	const w = chunkWriter(model.id, write);
	let error = "";
	const events = stream(model, { systemPrompt: system || undefined, messages, tools: tools.length ? tools : undefined }, streamOptions(req));
	for await (const ev of events as any) {
		w.event(ev);
		if (ev.type === "done" || ev.type === "error") {
			const msg = ev.type === "done" ? ev.message : ev.error;
			if (ev.type === "error") error = `pi model error: ${msg?.errorMessage ?? "unknown"}`;
			w.finish(msg);
		}
	}
	return error;
}

// ---------- agent run: one pi agent session per DeepTutor agent loop ----------

type Ctl = { kind: "next"; req: any } | { kind: "end" };
const controls = new Map<string, { queue: Ctl[]; waiter?: (c: Ctl) => void }>();
const nextCtl = (rid: string): Promise<Ctl> => {
	const box = controls.get(rid)!;
	const c = box.queue.shift();
	return c ? Promise.resolve(c) : new Promise((res) => (box.waiter = res));
};
const pushCtl = (rid: string, c: Ctl) => {
	const box = controls.get(rid);
	if (!box) return;
	if (box.waiter) { const w = box.waiter; box.waiter = undefined; w(c); } else box.queue.push(c);
};
const THINKING: Record<string, string> = { minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max" };

const T0 = Date.now();
const dbg = (m: string) => { if (process.env.PI_TUTOR_DEBUG) process.stderr.write(`[t+${Date.now() - T0}ms] ${m}\n`); };

async function handleAgent(rid: string, first: any): Promise<string> {
	dbg(`agent start ${rid.slice(0, 6)}`);
	controls.set(rid, { queue: [] });
	let req = first;
	const model = buildModel(req);
	const write = tagged(rid);
	let ctlPromise: Promise<Ctl> = Promise.resolve({ kind: "next", req });
	let toolResults = new Map<string, string>();
	let writer: ReturnType<typeof chunkWriter> | undefined;
	let error = "";

	// DeepTutor's tools as pi custom tools: pi validates and runs them; the result is DeepTutor's own dispatch output,
	// which arrives with DeepTutor's next context.
	const customTools = toolSpecs(req).map((t: any) => ({
		...t, label: t.name, executionMode: "sequential",
		execute: async (toolCallId: string) => {
			const c = await ctlPromise;
			return { content: [{ type: "text", text: toolResults.get(toolCallId) ?? "" }], details: undefined, ...(c.kind === "end" ? { terminate: true } : {}) };
		},
	}));

	// Built-in extension: DeepTutor stays in charge of the transcript and of when the run ends.
	const deeptutor = (pi: any) => {
		pi.on("context_with_system", (ev: any) => {
			// DeepTutor rebuilds its system prompt every round (solve keeps its plan state there): send the current one,
			// keeping pi's tool declarations on the system message (plugin tools included).
			const { system, messages } = convert(req, model.id);
			const sys = { ...ev.messages[0], sections: { preamble: system } };
			dbg(`request out (${messages.length} msgs)`);
			return { messages: [sys, ...messages] };
		});
		pi.on("message_start", (ev: any) => { if (ev.message?.role === "assistant") { writer = chunkWriter(model.id, write); dbg("first model event"); } });
		pi.on("message_update", (ev: any) => { if (ev.message?.role === "assistant") writer?.event(ev.assistantMessageEvent); });
		pi.on("message_end", (ev: any) => {
			if (ev.message?.role !== "assistant") return;
			const msg = ev.message;
			if (msg.stopReason === "error") error = `pi model error: ${msg.errorMessage ?? "unknown"}`;
			writer?.finish(msg);
			dbg(`message_end cacheRead=${msg.usage?.cacheRead} input=${msg.usage?.input}`);
			write({ round_end: true, ...(error ? { error } : {}) });
			ctlPromise = nextCtl(rid).then((c) => { // DeepTutor's answer to this round
				toolResults = new Map();
				dbg(`ctl ${c.kind} from DeepTutor`);
				if (c.kind === "next") {
					req = c.req;
					for (const m of req.messages ?? []) if (m.role === "tool") toolResults.set(m.tool_call_id, text(m.content));
				}
				return c;
			});
		});
		pi.on("turn_end", async (ev: any) => {
			const c = await ctlPromise;
			if (c.kind === "end") return;
			const names = toolSpecs(req).map((t: any) => t.name);
			const plugins = pi.getActiveTools?.().filter((n: string) => !customTools.some((t: any) => t.name === n)) ?? [];
			pi.setActiveTools?.([...names, ...plugins]); // DeepTutor narrows tools when it settles
			return ev.toolResults?.length ? undefined : { continue: true }; // a no-tool round DeepTutor wants continued
		});
	};

	const loader = new DefaultResourceLoader({
		cwd: HERE, agentDir: AGENT_DIR, extensionFactories: [{ name: "deeptutor", factory: deeptutor }],
		noContextFiles: true, systemPrompt: convert(req, model.id).system,
	});
	await loader.reload();
	dbg("loader ready");
	const settings = SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } });
	const { session } = await createAgentSession({
		cwd: HERE, agentDir: AGENT_DIR, model, thinkingLevel: (THINKING[req.reasoning_effort] ?? "off") as any,
		resourceLoader: loader, settingsManager: settings, sessionManager: SessionManager.inMemory(HERE),
		noTools: "builtin", customTools: customTools as any,
	});
	dbg("session created");
	try {
		await session.bindExtensions({});
		dbg("extensions bound");
		const lastUser = [...(req.messages ?? [])].reverse().find((m: any) => m.role === "user");
		await session.prompt(text(lastUser?.content) || "Continue.");
		// pi settled (DeepTutor said end, or an error round stopped it): finish the handshake if DeepTutor has not.
		// Any further "next" gets an error round, so DeepTutor's loop takes its own error path instead of waiting.
		while ((await ctlPromise).kind === "next") {
			write({ round_end: true, error: error || "pi agent run already ended" });
			ctlPromise = nextCtl(rid);
		}
	} catch (e: any) {
		error = error || String(e?.message ?? e);
	} finally {
		controls.delete(rid);
		try { session.dispose(); } catch {}
	}
	return error;
}

if (process.argv.includes("--serve")) {
	for await (const line of createInterface({ input: process.stdin })) {
		if (!line.trim()) continue;
		const msg = JSON.parse(line);
		const { rid } = msg;
		if (msg.kind === "next" || msg.kind === "end") { pushCtl(rid, msg.kind === "next" ? { kind: "next", req: msg.req } : { kind: "end" }); continue; }
		const job = msg.kind === "agent" ? handleAgent(rid, msg.req) : handle(msg.req, tagged(rid));
		job.then((error) => out({ rid, end: true, ...(error ? { error } : {}) }))
			.catch((e) => out({ rid, end: true, error: String(e?.message ?? e) }));
	}
} else {
	const error = await handle(JSON.parse(readFileSync(0, "utf8")), out);
	if (error) { process.stderr.write(error + "\n"); process.exitCode = 1; }
}
