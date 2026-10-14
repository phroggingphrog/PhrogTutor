"""Chat capability: saves the assistant's plan as a *proposed* version. It cannot approve or execute."""
from __future__ import annotations

from deeptutor.core.capability_protocol import CapabilityManifest, TurnCapability
from deeptutor.core.stream import StreamEvent, StreamEventType

import json
import os

from .plan_records import Store


class DshPlanCapability(TurnCapability):
    manifest = CapabilityManifest(
        name="dsh_plan",
        description="Draft a versioned plan for owner approval; approved plans run in a persistent DSH session.",
        stages=["draft"],
    )

    async def run(self, context, stream):
        async with stream.stage("draft", source=self.manifest.name):
            p = Store().draft(project_id="deeptutor", content=context.user_message,
                              workspace_key=(getattr(context, "metadata", {}) or {}).get("workspace_key") or os.environ.get("DSH_DEFAULT_WORKSPACE", "throwaway"),
                              source_session_id=getattr(context, "session_id", "") or "")
            card = {k: p[k] for k in ("plan_id", "version", "sha256", "workspace_key", "content")}
            text = ("Here is a plan. Choose how DSH can work, then approve it when you're ready. "
                    "Nothing runs until you click Approve.\n\n```dsh-plan\n" + json.dumps(card) + "\n```")
            await stream.emit(StreamEvent(type=StreamEventType.CONTENT, source=self.manifest.name, content=text))
