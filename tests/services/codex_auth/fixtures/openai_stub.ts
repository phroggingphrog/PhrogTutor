// Preloaded into pi_engine/auth.ts by the tests: answers the auth.openai.com device-code endpoints.
const jwt = (o: unknown) => `x.${btoa(JSON.stringify(o))}.y`;
const access = jwt({ "https://api.openai.com/auth": { chatgpt_account_id: "acct-9" } });
let polls = 0;
const real = globalThis.fetch;
globalThis.fetch = (async (input: any, init: any) => {
	const url = String(input);
	if (!url.startsWith("https://auth.openai.com")) return real(input, init);
	if (url.endsWith("/deviceauth/usercode")) return Response.json({ device_auth_id: "dev1", user_code: "ABCD-1234", interval: 0 });
	if (url.endsWith("/deviceauth/token")) return ++polls < 2 ? new Response("", { status: 403 }) : Response.json({ authorization_code: "code1", code_verifier: "ver1" });
	if (url.endsWith("/oauth/token")) return Response.json({ access_token: access, refresh_token: "r1", expires_in: 3600 });
	return new Response("unexpected", { status: 404 });
}) as typeof fetch;
