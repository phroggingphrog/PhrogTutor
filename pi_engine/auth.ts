/**
 * ChatGPT (Codex) sign-in on pi's own OAuth flow, so the protocol lives upstream and not in DeepTutor.
 *
 * One request per process: stdin = one JSON line, stdout = JSON lines.
 *   in  {op:"device_login"}              out {event:"device_code", userCode, verificationUri, intervalSeconds,
 *                                              expiresInSeconds}, then {credential} or {error}
 *   in  {op:"refresh", refresh:"..."}    out {credential} or {error}
 * A credential is pi's: {access, refresh, expires (ms), accountId}. Killing the process cancels a login.
 * Tokens only ever travel over these pipes; they are never logged.
 */
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";
import { createInterface } from "node:readline";

type Emit = (o: any) => void;
const oauth = () => (openaiCodexProvider() as any).auth.oauth;
const clean = (c: any) => ({ access: c.access, refresh: c.refresh, expires: c.expires, accountId: c.accountId });

export async function deviceLogin(emit: Emit, signal: AbortSignal) {
	return oauth().login({
		signal,
		prompt: async (p: any) => {
			if (p.type === "select") return "device_code"; // the only method a server can finish
			throw new Error("this sign-in cannot ask for input");
		},
		notify: (e: any) => {
			if (e.type === "device_code") emit({ event: "device_code", userCode: e.userCode, verificationUri: e.verificationUri, intervalSeconds: e.intervalSeconds, expiresInSeconds: e.expiresInSeconds });
		},
	});
}

export const refresh = (refreshToken: string, signal: AbortSignal) => oauth().refresh({ type: "oauth", access: "", refresh: refreshToken, expires: 0 }, signal);

if (import.meta.main) {
	const emit: Emit = (o) => process.stdout.write(JSON.stringify(o) + "\n");
	const abort = new AbortController();
	process.on("SIGTERM", () => abort.abort());
	const lines = createInterface({ input: process.stdin });
	for await (const line of lines) {
		if (!line.trim()) continue;
		try {
			const req = JSON.parse(line);
			if (req.op === "device_login") emit({ credential: clean(await deviceLogin(emit, abort.signal)) });
			else if (req.op === "refresh") emit({ credential: clean(await refresh(String(req.refresh ?? ""), abort.signal)) });
			else emit({ error: "unknown op" });
		} catch (e: any) {
			emit({ error: String(e?.message ?? e) });
		}
		break;
	}
	process.exit(0);
}
