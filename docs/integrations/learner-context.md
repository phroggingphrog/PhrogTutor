# Shared learner context for external harnesses

Pi Tutor stores learner interaction style in the signed-in account's existing `chat_mastery.sqlite3`. The shared service uses this same store. It does not create a second style database.

A style profile is context, not a mode. A harness can combine it with Brainstorm, Learning, or another active mode. The selected mode remains in control when instructions conflict.

## Stored data and privacy

Pi Tutor stores the style label, a source name, a stable event ID, a timestamp, and a learner quote of up to 200 characters. It does not store the full interaction transcript in the style table.

When an external harness submits an interaction, Pi Tutor sends up to 4,000 characters of learner text and 3,000 characters of assistant text to its configured LLM provider for classification. The selected provider and its data-retention terms determine how that transient text is handled. Do not submit sensitive content unless those terms are acceptable. This API has an explicit write credential, but it cannot prove that a separate harness obtained per-account consent. Set that harness to classify only after the account owner opts in, or use read-only access and submit no interactions.

The labels are `tried_first`, `asked_for_answer`, `pushed_back`, `accepted`, and `own_idea`. Pi Tutor uses a 30-day half-life. It adds a learned instruction after at least 8 valid signals and a leading share of 40% or more.

## Copy a profile to any harness

```sh
deeptutor learner-context show --format text
deeptutor learner-context show --format json
```

Copy the text output into the other harness as learner context. The text form contains the instruction only. The JSON form includes profile data and short learner quotes. Treat this output as private data.

Control learning and records:

```sh
deeptutor learner-context set-enabled false
deeptutor learner-context set-enabled true
deeptutor learner-context delete-record 42
deeptutor learner-context clear --yes
deeptutor learner-context backup
```

The separate `set-automatic-collection false` command pauses Pi Tutor's automatic turn classification. Its default is off for every account. The authenticated account endpoint also exposes this control. The ordinary `set-enabled` setting controls prompt-context injection and external interaction storage. These controls do not delete existing records. `clear --yes` deletes all style records. `backup` creates an online SQLite backup with owner-only permissions. Make a backup before a schema migration or release.

## Connect a harness through the API

Multi-account credential management requires a signed-in account. Single-user mode creates an owner token without registration or a password. Pi Tutor stores the token in its private local secret store. The API returns credential metadata, not the integration token value. The login endpoint returns a signed-in owner bearer for remote API and MCP use. Store that bearer only in the process environment.

Sign in to Pi Tutor as the profile owner. Create a scoped credential with the CLI:

```sh
deeptutor learner-context credentials-create "Pi harness" --expires-in-days 30 --read-only
deeptutor learner-context credentials-create "DSH" --expires-in-days 30
```

In multi-account mode, the CLI prompts for sign-in unless `PI_TUTOR_AUTH_TOKEN` contains a signed-in bearer token. In single-user mode, the local CLI creates the token directly. Set `PI_TUTOR_LEARNER_CONTEXT_TOKEN_ID` to the credential ID for the local MCP bridge. The bridge loads the token value from the private owner secret store. For multi-account MCP, set `PI_TUTOR_AUTH_TOKEN` only in the process environment. Use a token for the signed-in account. Single-user requests accept loopback sources only. They reject forwarding and Cloudflare Access identity headers. Write tokens can submit interactions for classification. Read-only tokens can read profiles and prompt context.

The CLI does not print token values. Login returns an owner bearer token for the CLI and MCP process only. The MCP bridge can load a local token from the owner secret store in single-user mode. Do not add token values to prompts, MCP descriptions, URLs, or committed configuration. Tokens expire within 90 days and have separate read/write scopes. A write scope does not replace the owner's opt-in. Before an external harness sends interaction text, the owner must enable `deeptutor learner-context set-automatic-collection true` in the signed-in account or call the authenticated `PUT /api/learner-context/v1/automatic-collection` endpoint with `{"enabled":true}`. A write request returns `classification_not_enabled` and does not call the provider while this setting is off.

List and revoke credentials:

```sh
deeptutor learner-context credentials-list
deeptutor learner-context credentials-revoke <credential-id>
deeptutor learner-context credentials-revoke-all
```

Revoke a token when a harness stops using it. Revocation applies to that token. It does not revoke other credentials.

## Read profile and prompt context

```http
GET /api/learner-context/v1/integrations/profile
Authorization: Bearer <integration-token>
```

```http
GET /api/learner-context/v1/integrations/prompt-context
Authorization: Bearer <integration-token>
```

Integration profile responses omit record quotes. Prompt context contains only the instruction. Add it after the selected task preset. It states that learned style is advisory and cannot replace the active mode. An empty instruction means that the profile has not met the threshold, or the owner opted out.

## Record a completed interaction

```http
POST /api/learner-context/v1/integrations/interactions
Authorization: Bearer <integration-token>
Content-Type: application/json

{
  "event_id": "<stable-unique-event-id>",
  "source": "pi",
  "user_message": "<learner message>",
  "assistant_response": "<assistant response>"
}
```

Use a stable event ID for retries. Duplicate submissions from the same source do not add another signal. The server sends bounded exchange text to its configured classifier, then stores the result label and at most 200 characters of learner text. The assistant reply is not written to the learner style database.

The source has at most 55 characters. The event ID has at most 128 characters. Both accept letters, numbers, dot, underscore, colon, and hyphen. The source limit leaves room for Pi Tutor's `harness-` prefix.

## Pi and DSH MCP bridge

The optional bridge uses FastMCP from the existing `mcp` dependency and `httpx`. The bridge is included in the Pi Tutor source checkout. It is not exposed to the public internet by default.

Set its process environment through the harness configuration:

```sh
PI_TUTOR_LEARNER_CONTEXT_URL=http://127.0.0.1:18051
# Single-user local mode only:
PI_TUTOR_LEARNER_CONTEXT_TOKEN_ID=<credential-id>
# Multi-account remote service: use the signed-in owner bearer.
PI_TUTOR_LEARNER_CONTEXT_URL=https://<verified-private-service-origin>
PI_TUTOR_AUTH_TOKEN=<signed-in-owner-bearer>
```

In single-user mode, the bridge reads its token value from Pi Tutor's private owner secret store. In multi-account mode, set `PI_TUTOR_AUTH_TOKEN` in the bridge process environment to a signed-in owner bearer. The Python client and CLI also accept this environment variable. Use HTTPS only when you have verified the remote identity boundary.

The bridge supports `stdio`. Its HTTP client accepts loopback HTTP or HTTPS URLs. Start it with:

```sh
deeptutor learner-context serve-mcp
```

The tools read the profile, read prompt context, and submit completed interactions. Keep the token out of MCP configuration text. Use HTTPS for a remote service. HTTP is accepted only for a loopback service.

## ChatGPT and other hosted harnesses

A hosted harness cannot read the SQLite file or local MCP process by itself. It needs a secure custom action or connector that can call the HTTPS API and store a secret token. If that setup cannot protect a token, use the text export instead. Do not expose an unauthenticated learner-context endpoint.

## Versioned response

The schema version is `1`. Profile responses include `enabled`, `signals`, `shares`, `dominant`, `instruction`, and record fields when the account API permits them. Integration profile responses omit records and quotes. Prompt-context responses contain only the version and instruction. Future contract changes must increment the schema version.
