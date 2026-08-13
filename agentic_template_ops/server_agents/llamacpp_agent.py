from __future__ import annotations

import re
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

from agentic_template_ops.config import LLAMACPP_CONFIG, VersionCheckResult
from agentic_template_ops.server_agents.base_server_agent import BaseServerAgent

if TYPE_CHECKING:
    from agentic_template_ops.config import TemplateCandidate

DEVIMAGES_REPO = "redhat-ai-dev/developer-images"


class LlamacppAgent(BaseServerAgent):
    def __init__(self, **kwargs):
        super().__init__(config=LLAMACPP_CONFIG, **kwargs)

    def _fetch_version_from_github(self, url: str) -> str | None:
        parsed = self._github_url_to_raw(url)
        if not parsed:
            return None
        owner_repo, ref, path = parsed
        # Navigate to src/requirements.txt from any link in the llamacpp dir
        base = path.split("model_servers/llamacpp_python")[0] if "llamacpp_python" in path else ""
        req_path = f"{base}model_servers/llamacpp_python/src/requirements.txt".lstrip("/")
        content = self._fetch_github_file(owner_repo, ref, req_path)
        if not content:
            return None
        # Parse: llama-cpp-python[server]==0.2.90
        match = re.search(r"llama-cpp-python\S*==([\d.]+)", content)
        if match:
            return match.group(1)
        return None

    def _fetch_version_from_known_source(self) -> str | None:
        latest_dir = self._find_latest_version_dir(
            DEVIMAGES_REPO, "model-servers/llamacpp_python"
        )
        if not latest_dir:
            return None
        content = self._fetch_github_file(
            DEVIMAGES_REPO,
            "main",
            f"model-servers/llamacpp_python/{latest_dir}/src/requirements.txt",
        )
        if not content:
            return None
        match = re.search(r"llama-cpp-python\S*==([\d.]+)", content)
        return match.group(1) if match else None

    def get_upstream_latest_version(self) -> str:
        resp = self.session.get(
            "https://pypi.org/pypi/llama-cpp-python/json",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["info"]["version"]

    def check_compatibility(self, current: str, new: str) -> str:
        notes = []
        try:
            cur_v = Version(current)
            new_v = Version(new)
            if cur_v.minor != new_v.minor:
                notes.append(
                    "Check GGUF format compatibility with new version"
                )
        except InvalidVersion:
            pass
        notes.append("Verify model file format still supported")
        return "; ".join(notes)

    def check_for_updates(
        self, candidate: TemplateCandidate
    ) -> VersionCheckResult:
        return super().check_for_updates(candidate)

    def _release_url(self, version: str) -> str:
        return f"https://pypi.org/project/llama-cpp-python/{version}/"
