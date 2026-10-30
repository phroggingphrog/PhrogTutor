"""Optional FastMCP server for local Pi and DSH harnesses."""

from __future__ import annotations

import json
import os


def build_server():
    """Create a server only when FastMCP is installed in the harness environment."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the DeepTutor server or MCP dependency to run this bridge.") from exc

    if not os.environ.get("PI_TUTOR_LEARNER_CONTEXT_URL") or not os.environ.get("PI_TUTOR_LEARNER_CONTEXT_TOKEN"):
        raise RuntimeError("Set PI_TUTOR_LEARNER_CONTEXT_URL and PI_TUTOR_LEARNER_CONTEXT_TOKEN.")

    from urllib.parse import urlsplit

    origin = os.environ["PI_TUTOR_LEARNER_CONTEXT_URL"].rstrip("/")
    parsed_origin = urlsplit(origin)
    loopback_http = parsed_origin.scheme == "http" and parsed_origin.hostname in {"127.0.0.1", "localhost", "::1"}
    if not loopback_http and parsed_origin.scheme != "https":
        raise RuntimeError("The learner-context service must use loopback HTTP or HTTPS.")
    if not parsed_origin.hostname or parsed_origin.username or parsed_origin.password:
        raise RuntimeError("The learner-context URL must not include credentials.")
    if parsed_origin.query or parsed_origin.fragment:
        raise RuntimeError("The learner-context URL must not include a query or fragment.")

    mcp = FastMCP(
        "Pi Tutor learner context",
        instructions=(
            "Read the owner's shared learner style context and optionally record completed interactions. "
            "Combine this context with the active task mode. Never replace the user's selected mode. "
            "Learner style can be disabled or cleared through Pi Tutor's authenticated API or CLI."
        ),
    )

    @mcp.tool()
    def learner_style_profile() -> str:
        """Read the shared profile. Use its instruction as context, not as a mode selector."""
        return json.dumps(_request("GET", "/profile"), ensure_ascii=False, indent=2)

    @mcp.tool()
    def learner_style_prompt_context() -> str:
        """Return a short prompt instruction compatible with the current task mode."""
        payload = _request("GET", "/prompt-context")
        return str(payload.get("prompt_context") or "")

    @mcp.tool()
    def learner_record_interaction(event_id: str, source: str, user_message: str, assistant_response: str = "") -> str:
        """Classify and record a completed interaction. Transcripts are not stored."""
        payload = _request(
            "POST", "/interactions", {
                "event_id": event_id,
                "source": source,
                "user_message": user_message,
                "assistant_response": assistant_response,
            }
        )
        return json.dumps(payload, ensure_ascii=False)

    return mcp


def _request(method: str, path: str, payload: dict | None = None):
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("Install httpx in the harness environment.") from exc
    base = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_URL", "").rstrip("/")
    token = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Set PI_TUTOR_LEARNER_CONTEXT_URL and PI_TUTOR_LEARNER_CONTEXT_TOKEN.")
    with httpx.Client(timeout=65, follow_redirects=False) as client:
        response = client.request(
            method,
            f"{base}/api/learner-context/v1/integrations{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def main() -> None:
    mcp = build_server()
    transport = os.environ.get("PI_TUTOR_LEARNER_CONTEXT_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "http":
        mcp.run(transport="http", host="127.0.0.1", port=int(os.environ.get("PI_TUTOR_LEARNER_CONTEXT_MCP_PORT", "8920")))
    else:
        raise RuntimeError("PI_TUTOR_LEARNER_CONTEXT_MCP_TRANSPORT must be 'stdio' or 'http'.")
