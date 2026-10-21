/**
 * pi-tutor engine: one DeepTutor agent turn as one pi agent session, with DeepTutor's round rules as a pi extension.
 *
 * This file IS the engine (it replaced DeepTutor's Python AgentLoop). It decides every round:
 * exploration budget -> bounded settlement (3 rounds) -> one tool-less forced finish; continuation after a
 * token-limit cut; nudges for reasoning-only rounds; capability finish redirects / final-text overrides;
 * tool dispatch with ask_user pauses, terminator tools and context-checkpoint folding; retries before output.
 *
 * DeepTutor (Python) is only asked for what lives in its modes and tools, over the JSON-lines pipe:
 *   round        render this round to the UI and summarise it (text, tool calls, finish reason, ...)
 *   finish_check capability hooks on a tool-less answer -> {redirect, override}
 *   dispatch     run the round's DeepTutor tools -> tool messages, pause/terminate/checkpoint/override
 *   notice       (one-way) show a localized warning by key
 * and gets the outcome at the end: {rid, end, outcome}.
 */
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { AGENT_DIR, HERE, buildModel, chunkWriter, connectionOf, convert, text, THINKING, toolSpecs, withCacheKey } from "./convert.ts";

export type Host = { write(o: any): void; ask(kind: string, data?: any): Promise<any> };
type Mode = "explore" | "settle" | "recover" | "forced"; // recover: the one restricted-tool round a capability may ask for after settlement

const MAX_SETTLEMENT_ROUNDS = 3; // register state -> ask/resume -> record result -> reply
const MAX_REASONING_ONLY_RECOVERIES = 2;
const RETRY_DELAYS_MS = [500, 1500]; // only before any output is visible: a replay would duplicate prose
const IDLE_TIMEOUT_MS = 90_000; // a connected provider that never produces an event must settle
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function runLoop(first: any, host: Host): Promise<any> {
	const cfg = first.cfg ?? {};
	const req = { ...first.req };
	let model = buildModel(req);
	// A request that brings its own endpoint runs on a provider registered for this turn only.
	const conn = connectionOf(req);
	let modelRuntime: ModelRuntime | undefined;
	const connProvider = `conn-${randomUUID().slice(0, 8)}`;
	if (conn) {
		modelRuntime = await ModelRuntime.create({ authPath: join(AGENT_DIR, "auth.json"), modelsPath: join(AGENT_DIR, "models.json"), refreshOnCreate: false });
		modelRuntime.registerProvider(connProvider, {
			baseUrl: conn.baseUrl, api: conn.api as any, headers: conn.headers ?? undefined,
			models: [{ id: model.id, name: model.id, reasoning: model.reasoning, input: model.input, cost: model.cost, contextWindow: model.contextWindow, maxTokens: model.maxTokens }],
		});
		// A runtime key is used as given; a key in the provider config would be run if it began with "!".
		if (conn.apiKey) await modelRuntime.setRuntimeApiKey(connProvider, conn.apiKey);
		model = modelRuntime.getModel(connProvider, model.id) ?? model;
	}
	const budget = Math.max(1, cfg.budget ?? 1);
	const T = (k: string) => cfg.texts?.[k] ?? "";
	let transcript: any[] = [...req.messages];
	let tools: any[] = req.tools ?? [];
	let boundary = transcript.length; // context checkpoints fold everything after this
	const s = { rounds: 0, exploration: 0, settlement: 0, toolSteps: 0, budgetRecoveries: 0, reasoningOnly: 0, attempt: 0 };
	let settlementStarted = false, redirectUsed = false;
	let recovery: Set<string> | undefined; // tools the restricted final round may call
	const parts: string[] = [];
	let mode: Mode = "explore";
	let outcome: any = null; // set once the turn is decided
	let restart = false; // pi ended its run (error round) but the turn goes on

	const notice = (key: string, vars: any = {}) => host.write({ notice: key, vars });
	const append = (m: any) => transcript.push(m);
	const instruct = (instruction: string) => { // a loop directive, without two user turns in a row
		const last = transcript[transcript.length - 1];
		if (last?.role === "user" && typeof last.content === "string") last.content = last.content.trimEnd() ? `${last.content.trimEnd()}\n\n${instruction}` : instruction;
		else if (last?.role === "user" && Array.isArray(last.content)) {
			// content parts (a PDF's embedded images beside its text): extend the text, never fold image bytes into prose
			const parts = [...last.content];
			const at = parts.map((p: any) => typeof p?.text === "string").lastIndexOf(true);
			if (at >= 0) parts[at] = { ...parts[at], text: parts[at].text.trimEnd() ? `${parts[at].text.trimEnd()}\n\n${instruction}` : instruction };
			else parts.push({ type: "text", text: instruction });
			last.content = parts;
		} else append({ role: "user", content: instruction });
	};
	const finish = (raw: string, o: any = {}) => { outcome = { raw, parts: [...parts], completed: true, ...o }; };

	/** Pick the next round's mode; may end the turn (capability refuses a tool-less salvage). */
	async function forced(reason: "budget" | "error", error = "") {
		if ((await host.ask("finish_check", { text: "" })).redirect) {
			notice("capability_finish_rejected");
			outcome = { raw: "", parts: [], completed: false };
			return;
		}
		if (reason === "error") notice("loop_error_finish", { error }); else notice("loop_budget_exhausted");
		instruct(T("finish_exhausted"));
		mode = "forced";
	}
	/** A turn whose required interaction could not be completed fails instead of passing as an answer. */
	async function rejectFinish() {
		await host.ask("reject_finish", {});
		notice("capability_finish_rejected");
		outcome = { raw: "", parts: [], completed: false };
	}
	async function planNext() {
		if (s.exploration < budget) { mode = "explore"; return; }
		if (s.settlement >= MAX_SETTLEMENT_ROUNDS) {
			// The tool-less salvage and a restricted capability delivery share the same final slot; neither reopens exploration.
			if (recovery) return rejectFinish();
			const r = await host.ask("settlement_recovery", {});
			const names = new Set<string>((r?.names ?? []).filter((n: string) => dtNames().has(n)));
			if (r?.instruction && (r?.names ?? []).length) {
				if (!names.size) return rejectFinish();
				recovery = names;
				instruct(r.instruction);
				mode = "recover";
				return;
			}
			return forced("budget");
		}
		if (!settlementStarted) { notice("loop_settlement"); instruct(T("settle")); settlementStarted = true; }
		mode = "settle";
	}

	/** The round rules: DeepTutor's summary of one model round in, the next step out. */
	async function decide(sum: any) {
		if (sum.error) {
			if (!sum.output_emitted && s.attempt < RETRY_DELAYS_MS.length) {
				notice("provider_retry");
				await sleep(RETRY_DELAYS_MS[s.attempt++]);
				restart = true;
				return;
			}
			if (mode === "forced" || s.rounds === 0 || sum.output_emitted) {
				outcome = { error: sum.error, error_kind: "transport", partial: Boolean(sum.output_emitted) };
				return;
			}
			s.attempt = 0;
			await forced("error", sum.error); // salvage a turn that already gathered work
			restart = true;
			return;
		}
		s.attempt = 0;
		s.rounds++;
		if (mode === "settle" || mode === "recover") s.settlement++; else if (mode === "explore") s.exploration++;
		const diag = { rounds: s.rounds, reasoning_chars: sum.reasoning_chars, completion_tokens: sum.completion_tokens, reasoning_tokens: sum.reasoning_tokens };

		if (mode === "forced") {
			if (sum.reasoning_exhausted) { outcome = { error_kind: "reasoning_budget", diagnostics: diag }; return; }
			append(sum.assistant);
			finish(sum.text, { visible: sum.visible, empty_kind: sum.reasoning_without_answer ? "reasoning_only" : null, provider_state: sum.provider_state });
			return;
		}

		if (!sum.tool_calls.length) {
			if (sum.truncated || sum.reasoning_exhausted) {
				host.write({ release: true });
				if (sum.reasoning_exhausted) {
					if (s.budgetRecoveries >= 1) { outcome = { error_kind: "reasoning_budget", repeated: true, diagnostics: diag }; return; }
					s.budgetRecoveries++;
				}
				// a cut-off generation is not the model's decision to finish: keep its prefix and ask it to go on
				notice("output_truncated");
				if (sum.visible) parts.push(sum.visible);
				if (sum.replayable) append(sum.assistant);
				instruct(T(sum.visible ? "continue_truncated" : "continue_truncated_reasoning"));
				return planNext();
			}
			if (!sum.clean) { // only internal reasoning: the model planned but never acted
				s.reasoningOnly++;
				notice("empty_finish_nudged");
				if (sum.replayable) append(sum.assistant);
				if (s.reasoningOnly >= MAX_REASONING_ONLY_RECOVERIES) { instruct(T("repeat_reasoning_only_nudge")); return forced("budget"); }
				instruct(T("finish_empty_nudge"));
				return planNext();
			}
			const chk = await host.ask("finish_check", { text: sum.clean }); // releases or retracts the round's prose
			if (chk.redirect) {
				if (redirectUsed) { notice("capability_finish_rejected"); outcome = { raw: "", parts: [], completed: false }; return; }
				redirectUsed = true;
				if (sum.text) append(sum.assistant);
				instruct(chk.redirect);
				return planNext();
			}
			if (chk.override != null) { finish(chk.override, { allow_empty: true }); return; }
			append(sum.assistant);
			finish(sum.text, { visible: sum.visible, provider_state: sum.provider_state });
			return;
		}

		if (mode === "recover" && sum.tool_calls.some((c: any) => !recovery?.has(c.name))) {
			// A schema hint is not an execution permission: reject the whole batch before any rogue tool runs.
			return rejectFinish();
		}
		// tool round: DeepTutor runs its tools (plugin tools are run by pi and added at turn_end)
		const plugins = new Set((api.getActiveTools?.() ?? []).filter((n: string) => !registered.has(n)));
		const d = await host.ask("dispatch", { tool_calls: sum.tool_calls.filter((c: any) => !plugins.has(c.name)), clean: sum.clean, truncated: sum.truncated, step: s.toolSteps });
		s.toolSteps++;
		append(sum.assistant);
		transcript.push(...d.tool_messages);
		if (d.tools) tools = d.tools; // deferred tools loaded this round
		if (d.system != null) transcript[0] = { ...transcript[0], content: d.system };
		if (d.pause) {
			if (!d.resumed) { outcome = { raw: "", parts: [], completed: false }; return; }
			return planNext(); // the user's answer replaced the ask_user tool message
		}
		if (d.checkpoint) {
			const folded = [...transcript.slice(0, boundary), { role: "system", content: `[Context checkpoint]\n${d.checkpoint}` }];
			boundary = folded.length;
			// current state sits past the boundary, so the next fold replaces it instead of stacking a stale copy
			transcript = d.checkpoint_state ? [...folded, { role: "system", content: d.checkpoint_state }] : folded;
		}
		if (d.override != null) { finish(d.override, { allow_empty: true }); return; }
		if (d.terminate) { outcome = { raw: "", terminal: d.terminate_text ?? "", parts: [...parts], completed: true }; return; }
		return planNext();
	}

	// ---- the pi session ----
	// A provider that connects but never produces another event must settle, not park a durable turn.
	const idleMs = (cfg.idle_timeout_s ?? IDLE_TIMEOUT_MS / 1000) * 1000;
	let awaitingModel = false, lastEvent = Date.now(), idled = false;
	const touch = () => { lastEvent = Date.now(); };
	let writer: ReturnType<typeof chunkWriter> | undefined;
	let roundError = "";
	let decision: Promise<void> = Promise.resolve();
	const dtNames = () => new Set(tools.map((t: any) => t.function.name));
	const registered = new Set<string>();
	let api: any;

	const register = () => {
		for (const t of toolSpecs({ tools })) {
			if (registered.has(t.name)) continue;
			registered.add(t.name);
			api.registerTool({
				...t, label: t.name, executionMode: "sequential",
				// DeepTutor already ran it in `dispatch`; pi gets the same result text for its own record.
				execute: async (toolCallId: string) => {
					await decision;
					const m = transcript.find((x: any) => x.role === "tool" && x.tool_call_id === toolCallId);
					return { content: [{ type: "text", text: text(m?.content) }], details: undefined };
				},
			});
		}
	};
	const activate = () => {
		const plugins = (api.getActiveTools?.() ?? []).filter((n: string) => !registered.has(n));
		const active = mode === "forced" ? [] : mode === "recover" ? [...(recovery ?? [])] : [...dtNames(), ...plugins];
		if (process.env.PI_TUTOR_DEBUG) process.stderr.write(`[activate] ${mode}: ${active.join(",")}\n`);
		api.setActiveTools?.(active);
	};

	const deeptutor = (pi: any) => {
		api = pi;
		register();
		pi.on("context_with_system", async (ev: any) => {
			// DeepTutor's transcript as this loop keeps it (its system prompt + rounds), pi's tool declarations kept.
			const r = await host.ask("round_start", { mode, messages: transcript, max_tokens: req.max_tokens ?? req.max_completion_tokens });
			if (r?.messages) transcript = r.messages; // context-window guard snipped old tool results
			// the request may carry what the transcript must not keep (fresh tool pixels, a lesson page)
			const { system, messages } = convert({ messages: r?.request_messages ?? transcript }, model.id);
			return { messages: [{ ...ev.messages[0], sections: { preamble: system } }, ...messages] };
		});
		pi.on("before_provider_request", (ev: any) => {
			// DeepTutor forces a named tool on the first round (Ask Questions -> ask_user); pi-ai has only auto/none.
			const name = cfg.initial_tool_choice;
			awaitingModel = true; touch();
			let p = withCacheKey(ev.payload, req);
			// The request is the authority on which tools a round may call, whatever pi had active when it was built.
			if (p && Array.isArray(p.tools) && (mode === "recover" || mode === "forced")) {
				const keep = mode === "recover" ? p.tools.filter((t: any) => recovery?.has(t.function?.name ?? t.name)) : [];
				const { tools: _drop, tool_choice: _choice, ...rest } = p;
				p = keep.length ? { ...p, tools: keep } : rest;
			}
			if (s.rounds === 0 && name && p && Array.isArray(p.tools) && p.tools.some((t: any) => t.function?.name === name)) {
				if (process.env.PI_TUTOR_DEBUG) process.stderr.write(`[tool_choice] forced ${name}\n`);
				return { ...p, tool_choice: { type: "function", function: { name } } };
			}
			return p === ev.payload ? undefined : p;
		});
		pi.on("message_start", (ev: any) => { touch(); if (ev.message?.role === "assistant") { roundError = ""; writer = chunkWriter(model.id, host.write); } });
		pi.on("message_update", (ev: any) => { touch(); if (ev.message?.role === "assistant") writer?.event(ev.assistantMessageEvent); });
		pi.on("message_end", (ev: any) => {
			if (ev.message?.role !== "assistant") return;
			const msg = ev.message;
			awaitingModel = false;
			if (msg.stopReason === "error" || msg.stopReason === "aborted") roundError = idled ? "The model provider stream timed out." : `pi model error: ${msg.errorMessage ?? msg.stopReason}`;
			idled = false;
			writer?.finish(msg);
			decision = host.ask("round", { error: roundError || undefined }).then(decide);
		});
		// a DeepTutor call is never run twice; once the turn is decided nothing else runs (plugin tools included)
		pi.on("tool_call", async () => { await decision; if (outcome) return { block: true, reason: "turn finished", terminate: true }; });
		pi.on("turn_end", async (ev: any) => {
			await decision;
			const names = dtNames();
			for (const r of ev.toolResults ?? []) // pi plugin tools: their results join DeepTutor's transcript
				if (!names.has(r.toolName) && r.toolName && !transcript.some((m: any) => m.tool_call_id === r.toolCallId))
					append({ role: "tool", tool_call_id: r.toolCallId, content: text(r.content) });
			if (outcome || restart) return;
			register();
			activate();
			return { continue: true };
		});
	};

	const loader = new DefaultResourceLoader({
		cwd: HERE, agentDir: AGENT_DIR, extensionFactories: [{ name: "deeptutor", factory: deeptutor }],
		noContextFiles: true, systemPrompt: convert({ messages: transcript }, model.id).system,
	});
	await loader.reload();
	const settings = SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } });
	if (conn?.transport) settings.setTransport(conn.transport as any);
	const { session } = await createAgentSession({
		cwd: HERE, agentDir: AGENT_DIR, model, thinkingLevel: (THINKING[req.reasoning_effort] ?? "off") as any,
		resourceLoader: loader, settingsManager: settings, sessionManager: SessionManager.inMemory(HERE, req.cache_key ? { id: req.cache_key } : undefined), noTools: "builtin",
		...(modelRuntime ? { modelRuntime } : {}),
	});
	const watchdog = setInterval(() => {
		if (awaitingModel && !idled && Date.now() - lastEvent > idleMs) { idled = true; void session.abort(); }
	}, Math.min(5000, Math.max(50, idleMs / 4)));
	try {
		await session.bindExtensions({});
		activate();
		const lastUser = [...transcript].reverse().find((m: any) => m.role === "user");
		let prompt = text(lastUser?.content) || "Continue.";
		while (!outcome) {
			restart = false;
			const roundsBefore = s.rounds;
			await session.prompt(prompt);
			await decision;
			if (outcome) break;
			// pi may stop on its own after a continue (e.g. it will not extend a cut-off round): prompt again,
			// unless that prompt produced no new round at all.
			if (!restart && s.rounds === roundsBefore) { outcome = { error: roundError || "pi agent run ended without an answer", error_kind: "transport", partial: true }; break; }
			activate();
			prompt = "Continue.";
		}
	} catch (e: any) {
		outcome = outcome ?? { error: String(e?.message ?? e), error_kind: "transport", partial: s.rounds > 0 };
	} finally {
		clearInterval(watchdog);
		try { session.dispose(); } catch {}
		if (modelRuntime) { try { modelRuntime.unregisterProvider(connProvider); } catch {} }
	}
	return { ...outcome, messages: transcript, tools, rounds: s.rounds, settlement_rounds: s.settlement, tool_steps: s.toolSteps };
}
