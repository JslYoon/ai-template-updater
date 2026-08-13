from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_template_ops.config import (
    OBJECT_DETECTION_CONFIG,
    VersionCheckResult,
)
from agentic_template_ops.server_agents.base_server_agent import BaseServerAgent

if TYPE_CHECKING:
    from agentic_template_ops.config import TemplateCandidate


class ObjectDetectionAgent(BaseServerAgent):
    def __init__(self, known_digest: str | None = None, **kwargs):
        super().__init__(config=OBJECT_DETECTION_CONFIG, **kwargs)
        self._known_digest = known_digest

    def get_upstream_latest_version(self) -> str:
        tags = self.get_quay_tags()
        for tag in tags:
            if isinstance(tag, dict) and tag["name"] == "latest":
                return tag.get("manifest_digest", "unknown")[:16]
        return "unknown"

    def check_for_updates(
        self, candidate: TemplateCandidate
    ) -> VersionCheckResult:
        try:
            current_digest = self._known_digest or "unknown"
            latest_digest = self.get_upstream_latest_version()

            update_available = (
                current_digest != "unknown"
                and latest_digest != "unknown"
                and current_digest != latest_digest
            )

            return VersionCheckResult(
                component="server",
                current_version=f"latest ({current_digest})",
                latest_version=f"latest ({latest_digest})",
                update_available=update_available,
                source_url=(
                    f"https://quay.io/repository/"
                    f"{self.config.quay_repo}?tab=tags"
                ),
                notes=(
                    "Uses :latest tag; digest-based change detection"
                    if update_available
                    else "Uses :latest tag; no version pinning"
                ),
            )
        except Exception as e:
            return VersionCheckResult(
                component="server",
                current_version="latest",
                latest_version="UNKNOWN",
                update_available=False,
                source_url="",
                notes="",
                error=str(e),
            )

    def check_compatibility(self, current: str, new: str) -> str:
        return "FastAPI/uvicorn server; verify endpoint contract unchanged"

    def _release_url(self, version: str) -> str:
        return (
            f"https://quay.io/repository/"
            f"{self.config.quay_repo}?tab=tags"
        )
