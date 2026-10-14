"""HTTP client for the DSH handoff bridge. Never falls back to headless execution."""
from __future__ import annotations

import json
import os
import urllib.request

DEEP_LINK = "https://deepseek.phroggingphrog.com/?session={sid}"


class BridgeBlocked(Exception):
    pass


def _call(op, body, url=None, token=None, timeout=30):
    url = url or os.environ.get("DSH_BRIDGE_URL", "")
    token = token or os.environ.get("DSH_BRIDGE_TOKEN", "")
    if not url or not token:
        raise BridgeBlocked("DSH bridge not configured (DSH_BRIDGE_URL / DSH_BRIDGE_TOKEN)")
    req = urllib.request.Request(f"{url.rstrip('/')}/{op}", json.dumps(body).encode(),
                                 {"content-type": "application/json", "authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
