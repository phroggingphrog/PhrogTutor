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

External harness credentials require Pi Tutor authentication. Do not enable authentication only for this feature without reviewing the full account and deployment security settings.

Sign in to Pi Tutor as the profile owner. Create a scoped credential with the CLI:

```sh
deeptutor learner-context credentials-create "Pi harness" --expires-in-days 30 --read-only
deeptutor learner-context credentials-create "DSH" --expires-in-days 30
```

The CLI prompts for sign-in unless `PI_TUTOR_AUTH_TOKEN` contains a signed-in bearer token. Set `PI_TUTOR_API_URL` to the API base when it does not use `http://127.0.0.1:18051`. The write-capable token can send interactions for classification. The read-only token can only read a profile and prompt context.

The API returns the new `access_token` once. Save it in the target harness secret manager. Do not add it to a prompt, MCP description, URL, or committed configuration. Tokens expire within 90 days and have separate read/write scopes. A write scope does not replace the owner's opt-in. Before an external harness sends interaction text, the owner must enable `deeptutor learner-context set-automatic-collection true` in the signed-in account or call the authenticated `PUT /api/learner-context/v1/automatic-collection` endpoint with `{"enabled":true}`. A write request returns `classification_not_enabled` and does not call the provider while this setting is off.

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

Set its process environment through the harness secret manager:

```sh
PI_TUTOR_LEARNER_CONTEXT_URL=https://<authenticated-pi-tutor-host>
PI_TUTOR_LEARNER_CONTEXT_TOKEN=<integration-token>
```

The bridge supports `stdio`, or loopback HTTP when explicitly configured. Start it with:

```sh
deeptutor learner-context serve-mcp
```

The tools read the profile, read prompt context, and submit completed interactions. Keep the token out of MCP configuration text. Use HTTPS for a remote service. HTTP is accepted only for a loopback service.

## ChatGPT and other hosted harnesses

A hosted harness cannot read the SQLite file or local MCP process by itself. It needs a secure custom action or connector that can call the HTTPS API and store a secret token. If that setup cannot protect a token, use the text export instead. Do not expose an unauthenticated learner-context endpoint.

## Versioned response

The schema version is `1`. Profile responses include `enabled`, `signals`, `shares`, `dominant`, `instruction`, and record fields when the account API permits them. Integration profile responses omit records and quotes. Prompt-context responses contain only the version and instruction. Future contract changes must increment the schema version.
