/**
 * pi-tutor model bridge: every DeepTutor model call (BaseAgent.call_llm / stream_llm and the agent
 * loop) runs through the pi SDK's model layer (@earendil-works/pi-ai `stream`).
 * Input  (stdin, JSON): OpenAI chat-completions request {model, messages, tools?, temperature?, max_tokens?, reasoning_effort?}
 * Output (stdout): one OpenAI `chat.completion.chunk` JSON object per line, so DeepTutor's code reads it unchanged.
 * Model access: the "omni" provider in agent-dir/models.json (OmniRoute; key never printed).
 */
import { stream } from "@earendil-works/pi-ai/compat";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const req = JSON.parse(readFileSync(0, "utf8"));
const omni = JSON.parse(readFileSync(join(HERE, "agent-dir", "models.json"), "utf8")).providers.omni;
const known = new Set<string>(omni.models.map((m: any) => m.id));
const modelId: string = known.has(req.model) ? req.model : (process.env.PI_TUTOR_MODEL ?? "openai/luna");
const spec = omni.models.find((m: any) => m.id === modelId) ?? {};

const model: any = {
	id: modelId,
	name: spec.name ?? modelId,
	api: "openai-completions",
	provider: "omni",
	baseUrl: omni.baseUrl,
	reasoning: Boolean(req.reasoning_effort),
	input: spec.input ?? ["text"],
	cost: spec.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
	contextWindow: spec.contextWindow ?? 128000,
	maxTokens: req.max_tokens ?? req.max_completion_tokens ?? 16000,
};

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

// OpenAI messages -> pi Context
const system: string[] = [];
const messages: any[] = [];
const toolNames = new Map<string, string>();
for (const m of req.messages ?? []) {
	if (m.role === "system" || m.role === "developer") system.push(text(m.content));
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
		messages.push({ role: "assistant", content, api: model.api, provider: "omni", model: modelId,
			usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
			stopReason: m.tool_calls?.length ? "toolUse" : "stop", timestamp: Date.now() });
	} else if (m.role === "tool") {
		messages.push({ role: "toolResult", toolCallId: m.tool_call_id, toolName: toolNames.get(m.tool_call_id) ?? "tool",
			content: [{ type: "text", text: text(m.content) }], isError: false, timestamp: Date.now() });
	}
}
const tools = (req.tools ?? []).map((t: any) => ({ name: t.function.name, description: t.function.description ?? "", parameters: t.function.parameters ?? { type: "object", properties: {} } }));

const id = `pi-${Date.now()}`;
const created = Math.floor(Date.now() / 1000);
const emit = (delta: any, finish: string | null = null, usage: any = undefined) =>
	process.stdout.write(JSON.stringify({ id, object: "chat.completion.chunk", created, model: modelId,
		choices: [{ index: 0, delta, finish_reason: finish }], ...(usage ? { usage } : {}) }) + "\n");

const options: any = { apiKey: omni.apiKey };
if (req.temperature != null) options.temperature = req.temperature;
if (req.reasoning_effort) options.reasoningEffort = req.reasoning_effort;

let toolIndex = 0;
const finishMap: Record<string, string> = { stop: "stop", length: "length", toolUse: "tool_calls", error: "stop", aborted: "stop" };
const events = stream(model, { systemPrompt: system.join("\n\n") || undefined, messages, tools: tools.length ? tools : undefined }, options);
emit({ role: "assistant", content: "" });
for await (const ev of events as any) {
	if (ev.type === "text_delta") emit({ content: ev.delta });
	else if (ev.type === "thinking_delta") emit({ reasoning_content: ev.delta });
	else if (ev.type === "toolcall_end") {
		const tc = ev.toolCall;
		emit({ tool_calls: [{ index: toolIndex++, id: tc.id, type: "function", function: { name: tc.name, arguments: JSON.stringify(tc.arguments ?? {}) } }] });
	} else if (ev.type === "done" || ev.type === "error") {
		const msg = ev.type === "done" ? ev.message : ev.error;
		if (ev.type === "error") process.stderr.write(`pi model error: ${msg?.errorMessage ?? "unknown"}\n`);
		const u = msg?.usage ?? {};
		emit({}, finishMap[msg?.stopReason] ?? "stop", { prompt_tokens: u.input ?? 0, completion_tokens: u.output ?? 0, total_tokens: u.totalTokens ?? 0 });
		process.exitCode = ev.type === "error" ? 1 : 0;
	}
}
