from __future__ import annotations

from packaging.version import InvalidVersion, Version

from agentic_template_ops.config import VLLM_CONFIG
from agentic_template_ops.server_agents.base_server_agent import BaseServerAgent

DEVIMAGES_REPO = "redhat-ai-dev/developer-images"


class VllmAgent(BaseServerAgent):
    def __init__(self, **kwargs):
        super().__init__(config=VLLM_CONFIG, **kwargs)

    def _fetch_version_from_known_source(self) -> str | None:
        return self._find_latest_version_dir(
            DEVIMAGES_REPO, "model-servers/vllm"
        )

    def get_upstream_latest_version(self) -> str:
        data = self._gh_api("repos/vllm-project/vllm/releases/latest")
        return data["tag_name"]

    def check_compatibility(self, current: str, new: str) -> str:
        notes = []
        try:
            cur_v = Version(current.lstrip("v"))
            new_v = Version(new.lstrip("v"))

            if cur_v < Version("0.8") and new_v >= Version("0.8"):
                notes.append(
                    "BREAKING: Major version jump; review CLI arg changes"
                )
            if cur_v.minor != new_v.minor:
                notes.append(
                    "Verify --max-model-len compatibility with target model"
                )
        except InvalidVersion:
            notes.append("Unable to parse versions for compatibility check")
        return "; ".join(notes)

    def _release_url(self, version: str) -> str:
        tag = version if version.startswith("v") else f"v{version}"
        return f"https://github.com/vllm-project/vllm/releases/tag/{tag}"
