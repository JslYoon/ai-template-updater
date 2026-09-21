from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

from agentic_template_ops.config import VLLM_CONFIG
from agentic_template_ops.server_agents.base_server_agent import BaseServerAgent

DEVIMAGES_REPO = "redhat-developer/rhdh-ai-developer-images"
VLLM_REPO = "vllm-project/vllm"

# torch >= this version defaults to CUDA 13 wheels (libcudart.so.13); older
# torch is CUDA 12 (libcudart.so.12). Used as a fallback when the requirements
# file carries no explicit cuNNN / [cuNN] marker.
TORCH_CUDA13_MIN = Version("2.9")


class VllmAgent(BaseServerAgent):
    def __init__(self, **kwargs):
        super().__init__(config=VLLM_CONFIG, **kwargs)
        self._cuda_major_cache: dict[str, int | None] = {}

    def _fetch_version_from_known_source(self) -> str | None:
        return self._find_latest_version_dir(
            DEVIMAGES_REPO, "model-servers/vllm"
        )

    def get_upstream_latest_version(self) -> str:
        data = self._gh_api("repos/vllm-project/vllm/releases/latest")
        return data["tag_name"]

    def _detect_cuda_major(self, version: str) -> int | None:
        """Return the CUDA major (12, 13, ...) that vLLM `version` targets.

        Determined from the pinned torch build in the upstream tag's
        requirements/cuda.txt: explicit cuNNN / [cuNN] markers first, else
        inferred from the torch version. Returns None if it can't be resolved
        (e.g. network failure or an unparseable file) so callers can stay
        silent rather than emit a false warning.
        """
        if version in self._cuda_major_cache:
            return self._cuda_major_cache[version]

        result: int | None = None
        tag = version if version.startswith("v") else f"v{version}"
        content = self._fetch_github_file(
            VLLM_REPO, tag, "requirements/cuda.txt"
        )
        if content:
            # Explicit build markers, e.g. "[cu13]" or ".../whl/cu130".
            marker = re.search(r"\[cu(\d{2})\]|/whl/cu(\d{2})\d?", content)
            if marker:
                # marker group is a two-digit CUDA major, e.g. "13" or "12".
                result = int(marker.group(1) or marker.group(2))
            if result is None:
                m = re.search(r"^torch==(\d+\.\d+(?:\.\d+)?)", content, re.M)
                if m:
                    try:
                        result = (
                            13 if Version(m.group(1)) >= TORCH_CUDA13_MIN else 12
                        )
                    except InvalidVersion:
                        result = None

        self._cuda_major_cache[version] = result
        return result

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

        # CUDA runtime boundary: if the new vLLM targets a different CUDA major
        # than the current one, the rhdh-ai-developer-images Containerfile system CUDA
        # layer (cuda-cudart-<major>-*) AND the torch wheel index (cu<major>0)
        # must be bumped too, or the image crashes at import with
        # "libcudart.so.<major>: cannot open shared object file".
        cur_cuda = self._detect_cuda_major(current)
        new_cuda = self._detect_cuda_major(new)
        if cur_cuda and new_cuda and new_cuda != cur_cuda:
            notes.append(
                f"BREAKING: CUDA runtime bump required (CUDA {cur_cuda} ->"
                f" {new_cuda}); update the Containerfile system CUDA layer to"
                f" cuda-cudart-{new_cuda}-0 / cuda-libraries-{new_cuda}-0 and"
                f" the torch wheel index to cu{new_cuda}0, else the image fails"
                f" at import with libcudart.so.{new_cuda} not found"
            )

        return "; ".join(notes)

    def _release_url(self, version: str) -> str:
        tag = version if version.startswith("v") else f"v{version}"
        return f"https://github.com/vllm-project/vllm/releases/tag/{tag}"
