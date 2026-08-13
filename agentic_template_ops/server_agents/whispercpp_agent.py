from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

from agentic_template_ops.config import WHISPERCPP_CONFIG
from agentic_template_ops.server_agents.base_server_agent import BaseServerAgent

DEVIMAGES_REPO = "redhat-ai-dev/developer-images"


class WhispercppAgent(BaseServerAgent):
    def __init__(self, **kwargs):
        super().__init__(config=WHISPERCPP_CONFIG, **kwargs)

    def _fetch_version_from_github(self, url: str) -> str | None:
        parsed = self._github_url_to_raw(url)
        if not parsed:
            return None
        owner_repo, ref, path = parsed
        # If URL points to directory, look for Containerfile inside
        if not path.endswith("Containerfile"):
            path = path.rstrip("/") + "/Containerfile"
        content = self._fetch_github_file(owner_repo, ref, path)
        if not content:
            return None
        # Parse: git checkout tags/v1.5.4
        match = re.search(r"git checkout tags/v?([\d.]+)", content)
        if match:
            return match.group(1)
        return None

    def _fetch_version_from_known_source(self) -> str | None:
        latest_dir = self._find_latest_version_dir(
            DEVIMAGES_REPO, "model-servers/whispercpp"
        )
        if not latest_dir:
            return None
        content = self._fetch_github_file(
            DEVIMAGES_REPO,
            "main",
            f"model-servers/whispercpp/{latest_dir}/Containerfile",
        )
        if not content:
            return None
        match = re.search(r"git checkout tags/v?([\d.]+)", content)
        return match.group(1) if match else None

    def get_upstream_latest_version(self) -> str:
        data = self._gh_api("repos/ggml-org/whisper.cpp/releases/latest")
        return data["tag_name"]

    def check_compatibility(self, current: str, new: str) -> str:
        notes = []
        try:
            cur_v = Version(current.lstrip("v"))
            new_v = Version(new.lstrip("v"))
            if cur_v.major != new_v.major:
                notes.append(
                    "BREAKING: Major version change;"
                    " verify model format compatibility"
                )
        except InvalidVersion:
            pass
        notes.append("Verify ffmpeg bundling still compatible")
        return "; ".join(notes)

    def _release_url(self, version: str) -> str:
        tag = version if version.startswith("v") else f"v{version}"
        return (
            f"https://github.com/ggml-org/whisper.cpp/releases/tag/{tag}"
        )
