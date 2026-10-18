/**
 * pi-tutor turn engine (TASK-71 step B): ONE pi SDK session per chat turn, modelled on pi-study/src/study.ts.
 * Input  (stdin, JSON): {"message": str, "history": [{"role","content"}], "system": str?, "model": str?}
 * Output (stdout, one JSON per line): {"type": "content"|"tool_call"|"tool_result"|"done"|"error", ...}
 * The Python side (deeptutor/runtime/pi_turn_engine.py) maps these lines to StreamEvents.
 */
import { createAgentSession, createMcpExtension, DefaultResourceLoader, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = join(HERE, "agent-dir");
process.env.PI_CODING_AGENT_DIR = AGENT_DIR; // the MCP extension finds mcp.json through this
const LIMIT_MS = Number(process.env.PI_TUTOR_TIMEOUT_MS ?? 300_000); // wiki query ~45s warm, >120s cold
const MCP_WAIT_MS = Number(process.env.PI_TUTOR_MCP_WAIT_MS ?? 90_000);

// Wiki query/get + Canvas read-only. No file or shell tools.
const TOOLS = [
	"mcp__llm_wiki__query",
	"mcp__llm_wiki__get",
	"mcp__my_accounts__canvas_courses",
	"mcp__my_accounts__canvas_upcoming_assignments",
	"mcp__my_accounts__canvas_assignment",
];

// Every declared tool must be registered before the prompt, so a cold MCP server shows up as a real
// error instead of a turn that quietly answers without the owner's notes.
const REQUIRED = TOOLS;

const out = (o: object) => process.stdout.write(JSON.stringify(o) + "\n");
const input = JSON.parse(readFileSync(0, "utf8"));
const model: string = input.model ?? process.env.PI_TUTOR_MODEL ?? "openai/luna";
const history = (input.history ?? []).map((m: any) => `${m.role}: ${typeof m.content === "string" ? m.content : JSON.stringify(m.content)}`).join("\n");
const system = [readFileSync(join(HERE, "TUTOR.md"), "utf8"), input.system ?? "", history ? `Conversation so far:\n${history}` : ""].filter(Boolean).join("\n\n");

const loader = new DefaultResourceLoader({
	cwd: HERE,
	agentDir: AGENT_DIR,
	extensionFactories: [createMcpExtension()],
	noContextFiles: true,
	noSkills: true,
	systemPrompt: system,
});
await loader.reload();
const settings = SettingsManager.create(HERE);
settings.applyOverrides({ defaultProvider: "omni", defaultModel: model });

const { session } = await createAgentSession({
	cwd: HERE,
	agentDir: AGENT_DIR,
	resourceLoader: loader,
	settingsManager: settings,
	sessionManager: SessionManager.inMemory(),
	tools: TOOLS,
	thinkingLevel: "low",
});

let finished = false;
const finish = (s: string) => { // one JSON `done` per turn, then leave (the prompt turn can hold the event loop)
	if (finished) return;
	finished = true;
	out({ type: "done", status: s });
	try { session.dispose(); } catch {}
	process.exit(s === "completed" ? 0 : 1);
};

const timer = setTimeout(() => {
	out({ type: "error", content: `turn timed out after ${LIMIT_MS / 1000}s` });
	finish("failed");
}, LIMIT_MS);

let status = "completed";
try {
	await session.bindExtensions({}); // starts the MCP connections; they finish registering asynchronously
	// A cold MCP server registers its tools after bindExtensions resolves (measured: ~0.5s warm). Wait for the
	// declared tools, and fail the turn loudly rather than let the model answer "I cannot reach your notes".
	const waitStart = Date.now();
	let missing = REQUIRED.filter((n) => !session.getActiveToolNames().includes(n));
	while (missing.length && Date.now() - waitStart < MCP_WAIT_MS) {
		await new Promise((r) => setTimeout(r, 250));
		missing = REQUIRED.filter((n) => !session.getActiveToolNames().includes(n));
	}
	if (process.env.PI_TUTOR_DEBUG) console.error(`[tools] after ${Date.now() - waitStart}ms: ` + session.getActiveToolNames().join(", "));
	if (missing.length) {
		out({ type: "error", content: `MCP tools not registered after ${MCP_WAIT_MS / 1000}s: ${missing.join(", ")}; registered: ${session.getActiveToolNames().join(", ") || "none"}` });
		finish("failed");
	}
	session.subscribe((e: any) => {
		if (e.type === "message_update" && e.assistantMessageEvent?.type === "text_delta") {
			out({ type: "content", content: e.assistantMessageEvent.delta });
		} else if (e.type === "tool_execution_start") {
			out({ type: "tool_call", tool: String(e.toolName).replace(/^mcp__/, ""), args: e.args });
		} else if (e.type === "agent_end") {
			finish(e.willRetry ? "failed" : "completed");
		} else if (e.type === "tool_execution_end") {
			const text = (e.result?.content ?? []).map((c: any) => c.text ?? "").join("");
			out({ type: "tool_result", tool: String(e.toolName).replace(/^mcp__/, ""), content: text, is_error: !!e.isError });
		}
	});
	await session.prompt(String(input.message ?? "")).catch((err: any) => {
		status = "failed";
		out({ type: "error", content: String(err?.message ?? err) });
		finish("failed");
	});
} catch (err: any) {
	status = "failed";
	out({ type: "error", content: String(err?.message ?? err) });
} finally {
	clearTimeout(timer);
	finish(status); // normally already finished on agent_end
}
