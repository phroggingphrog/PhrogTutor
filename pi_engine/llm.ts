/**
 * pi-tutor: DeepTutor on the pi SDK.
 *
 * Model calls (pi-ai `stream`): DeepTutor's single-shot calls (BaseAgent.call_llm / stream_llm, co-writer, book...).
 * Agent turns (loop.ts): every DeepTutor agent loop (chat, solve, research, question, visualize, partners...) is one
 *   pi agent session whose round rules are the built-in "deeptutor" extension; DeepTutor (Python) only runs its
 *   tools and capability hooks when loop.ts asks.
 *
 * One-shot:  stdin = one OpenAI chat-completions request; stdout = one `chat.completion.chunk` JSON per line.
 * --serve:   one long-lived process; JSON lines tagged with "rid":
 *   in  {rid, req}                    single model call -> chunks, then {rid, end}
 *   in  {rid, kind:"loop", req, cfg}  one agent turn    -> chunks, {rid, ask, seq, ...}, {rid, notice}, {rid, release},
 *                                                          then {rid, end, outcome}
 *   in  {rid, kind:"reply", seq, data} DeepTutor's answer to an ask
 * Model access: the "omni" provider in agent-dir/models.json (OmniRoute; key never printed).
 */
import { stream } from "@earendil-works/pi-ai/compat";
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { buildModel, chunkWriter, convert, out, streamOptions, tagged, toolSpecs } from "./convert.ts";
import { runLoop } from "./loop.ts";

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

// ---------- agent turn: loop.ts asks DeepTutor over the pipe ----------

const pending = new Map<string, (data: any) => void>();
let seq = 0;
const host = (rid: string) => ({
	write: tagged(rid),
	ask: (ask: string, data: any = {}) => new Promise<any>((res, rej) => {
		const n = ++seq;
		pending.set(`${rid}:${n}`, (data) => (data?._error ? rej(new Error(data._error)) : res(data)));
		out({ ...data, rid, ask, seq: n });
	}),
});

if (process.argv.includes("--serve")) {
	for await (const line of createInterface({ input: process.stdin })) {
		if (!line.trim()) continue;
		const msg = JSON.parse(line);
		const { rid } = msg;
		if (msg.kind === "reply") {
			const key = `${rid}:${msg.seq}`;
			pending.get(key)?.(msg.data ?? {});
			pending.delete(key);
			continue;
		}
		if (msg.kind === "loop") {
			runLoop(msg, host(rid))
				.then((outcome) => out({ rid, end: true, outcome }))
				.catch((e) => out({ rid, end: true, outcome: { error: String(e?.message ?? e), error_kind: "transport", partial: true } }));
			continue;
		}
		handle(msg.req, tagged(rid))
			.then((error) => out({ rid, end: true, ...(error ? { error } : {}) }))
			.catch((e) => out({ rid, end: true, error: String(e?.message ?? e) }));
	}
} else {
	const error = await handle(JSON.parse(readFileSync(0, "utf8")), out);
	if (error) { process.stderr.write(error + "\n"); process.exitCode = 1; }
}
