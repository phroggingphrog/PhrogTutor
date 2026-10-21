/** pi-tutor: OpenAI chat-completions <-> pi message conversions, shared by llm.ts and loop.ts. */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const HERE = dirname(fileURLToPath(import.meta.url));
export const AGENT_DIR = join(HERE, "agent-dir");
// Model gateway: agent-dir/models.json (copy models.example.json), overridable by env.
// A fresh checkout has no models.json: requests that bring their own connection do not need one.
const _omni = (() => {
	try { return JSON.parse(readFileSync(join(AGENT_DIR, "models.json"), "utf8")).providers.omni; }
	catch { return { baseUrl: "https://api.openai.com/v1", api: "openai-completions", models: [] }; }
})();
export const omni = {
	..._omni,
	baseUrl: process.env.PI_TUTOR_LLM_BASE_URL || _omni.baseUrl,
	apiKey: process.env.PI_TUTOR_LLM_API_KEY || _omni.apiKey,
};
export const out = (o: any) => process.stdout.write(JSON.stringify(o) + "\n");
export const tagged = (rid: string) => (c: any) => out({ ...c, rid });
const finishMap: Record<string, string> = { stop: "stop", length: "length", toolUse: "tool_calls", error: "stop", aborted: "stop" };
const ZERO = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

// ---------- OpenAI <-> pi conversions ----------

/** A request may bring its own endpoint (see deeptutor/services/llm/pi_connection.py); otherwise the gateway is used. */
export type Connection = { api: string; provider?: string; baseUrl: string; apiKey?: string; headers?: Record<string, string> | null };
export function connectionOf(req: any): Connection | undefined {
	const c = req?.connection;
	if (!c || typeof c.api !== "string" || typeof c.baseUrl !== "string") return undefined;
	// models.json-style interpolation would run a value that starts with "!" or "$": refuse those outright.
	for (const v of [c.apiKey, ...Object.values(c.headers ?? {})])
		if (typeof v === "string" && /^[!$]/.test(v)) throw new Error("connection values may not start with ! or $");
	return c;
}

export function buildModel(req: any): any {
	// Any OmniRoute model or combo name passes through (DeepTutor's own catalog picks it); known ones get their limits/costs.
	const id: string = req.model || process.env.PI_TUTOR_MODEL || "openai/luna";
	const conn = connectionOf(req);
	const spec = (conn ? undefined : omni.models.find((m: any) => m.id === id)) ?? {};
	return {
		id, name: spec.name ?? id, api: conn?.api ?? "openai-completions", provider: conn ? (conn.provider ?? "custom") : "omni", baseUrl: conn?.baseUrl ?? omni.baseUrl,
		reasoning: Boolean(req.reasoning_effort), input: spec.input ?? ["text"],
		cost: spec.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: spec.contextWindow ?? 128000, maxTokens: req.max_tokens ?? req.max_completion_tokens ?? 16000,
	};
}

export const text = (c: any): string =>
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
export function convert(req: any, modelId: string): { system: string; messages: any[] } {
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

export const toolSpecs = (req: any) =>
	(req.tools ?? []).map((t: any) => ({ name: t.function.name, description: t.function.description ?? "", parameters: t.function.parameters ?? { type: "object", properties: {} } }));

export function streamOptions(req: any): any {
	const conn = connectionOf(req);
	const o: any = { apiKey: conn ? (conn.apiKey || undefined) : omni.apiKey };
	// Gateways that route on a header (JSON object in PI_TUTOR_LLM_HEADERS, else the provider's "headers").
	const headers = conn ? conn.headers : process.env.PI_TUTOR_LLM_HEADERS ? JSON.parse(process.env.PI_TUTOR_LLM_HEADERS) : omni.headers;
	if (headers) o.headers = headers;
	if (req.temperature != null) o.temperature = req.temperature;
	if (req.reasoning_effort) o.reasoningEffort = req.reasoning_effort;
	return o;
}

/** pi assistant-message events -> OpenAI chunks for one model call. */
export function chunkWriter(modelId: string, write: (c: any) => void) {
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

export const THINKING: Record<string, string> = { minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max" };
