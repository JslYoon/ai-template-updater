from __future__ import annotations

import abc
import json
import logging
import re
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from agentic_template_ops.config import (
        ServerConfig,
        TemplateCandidate,
        VersionCheckResult,
    )

from agentic_template_ops.config import VersionCheckResult


class BaseServerAgent(abc.ABC):
    def __init__(
        self, config: ServerConfig, session: requests.Session | None = None
    ):
        self.config = config
        self.session = session or requests.Session()
        self.log = logging.getLogger(f"agent.server.{config.server_type}")

    def get_quay_tags(
        self, tag_prefix: str = "", limit: int = 100
    ) -> list[dict]:
        url = f"https://quay.io/api/v1/repository/{self.config.quay_repo}/tag/"
        params: dict = {"limit": limit, "onlyActiveTags": "true"}
        if tag_prefix:
            params["filter_tag_name"] = f"like:{tag_prefix}"

        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("tags", [])

    def get_latest_quay_version(self) -> str | None:
        tags = self.get_quay_tags(tag_prefix=self.config.tag_prefix)
        versioned: list[tuple[Version, str]] = []
        for tag in tags:
            name = tag["name"] if isinstance(tag, dict) else tag
            if name == "latest":
                continue
            try:
                v = Version(name.lstrip("v"))
                if v.is_prerelease:
                    continue
                versioned.append((v, name))
            except InvalidVersion:
                continue
        if not versioned:
            return None
        versioned.sort(key=lambda x: x[0], reverse=True)
        return versioned[0][1]

    @abc.abstractmethod
    def get_upstream_latest_version(self) -> str:
        ...

    @abc.abstractmethod
    def check_compatibility(self, current: str, new: str) -> str:
        ...

    @abc.abstractmethod
    def _release_url(self, version: str) -> str:
        ...

    def check_for_updates(
        self, candidate: TemplateCandidate
    ) -> VersionCheckResult:
        try:
            latest_quay = self.get_latest_quay_version()
            latest_upstream = self.get_upstream_latest_version()
            current = self._extract_current_version(candidate)

            # Compare against upstream (the real target), not just quay
            effective_latest = latest_upstream or latest_quay or ""

            if current == "not tracked":
                update_available = False
            else:
                update_available = self._is_newer(current, effective_latest)

            notes = ""
            if update_available:
                notes = self.check_compatibility(current, effective_latest)

            if latest_upstream and latest_quay:
                try:
                    up_v = Version(latest_upstream.lstrip("v"))
                    quay_v = Version(latest_quay.lstrip("v"))
                    if up_v > quay_v:
                        notes += (
                            f" [Needs build: quay has {latest_quay},"
                            f" upstream has {latest_upstream}]"
                        )
                except InvalidVersion:
                    pass

            return VersionCheckResult(
                component="server",
                current_version=current,
                latest_version=effective_latest,
                update_available=update_available,
                source_url=self._release_url(effective_latest),
                notes=notes.strip(),
                upstream_version=latest_upstream or "",
                quay_version=latest_quay or "",
            )
        except Exception as e:
            self.log.error(
                "Version check failed for %s: %s",
                candidate.template_name,
                e,
            )
            return VersionCheckResult(
                component="server",
                current_version=self._extract_current_version(candidate),
                latest_version="UNKNOWN",
                update_available=False,
                source_url="",
                notes="",
                error=str(e),
            )

    def _extract_current_version(self, candidate: TemplateCandidate) -> str:
        url = candidate.server_url
        if url:
            version = self._parse_version_from_url(url)
            if version:
                return version
            version = self._fetch_version_from_github(url)
            if version:
                return version

        # No hyperlink or couldn't parse — try known source repo
        version = self._fetch_version_from_known_source()
        if version:
            return version

        return "not tracked"

    def _fetch_version_from_known_source(self) -> str | None:
        """Override to fetch version from a known repo when no hyperlink exists."""
        return None

    def _gh_api(self, endpoint):
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh api failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def _find_latest_version_dir(self, owner_repo: str, base_path: str) -> str | None:
        try:
            dirs = self._gh_api(f"repos/{owner_repo}/contents/{base_path}")
        except (RuntimeError, json.JSONDecodeError):
            return None
        versions = []
        for d in dirs:
            name = d.get("name", "")
            try:
                versions.append((Version(name), name))
            except InvalidVersion:
                continue
        if not versions:
            return None
        versions.sort(reverse=True)
        return versions[0][1]

    @staticmethod
    def _parse_version_from_url(url: str) -> str | None:
        path = urlparse(url).path
        # developer-images pattern: /model-servers/vllm/0.6.6
        match = re.search(r"/model-servers?/[^/]+/(\d+\.\d+[\d.]*)", path)
        if match:
            return match.group(1)
        return None

    def _fetch_version_from_github(self, url: str) -> str | None:
        """Fetch file from GitHub raw URL and extract version. Override per agent."""
        return None

    @staticmethod
    def _github_url_to_raw(url: str) -> tuple[str, str, str] | None:
        """Parse GitHub blob/tree URL into (owner/repo, ref, path)."""
        match = re.match(
            r"https://github\.com/([^/]+/[^/]+)/(blob|tree)/([^/]+)/(.*)",
            url,
        )
        if match:
            return match.group(1), match.group(3), match.group(4)
        return None

    def _fetch_github_file(self, owner_repo: str, ref: str, path: str) -> str | None:
        endpoint = f"repos/{owner_repo}/contents/{path}?ref={ref}"
        try:
            result = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github.raw+json", endpoint],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                self.log.debug("gh api failed for %s: %s", endpoint, result.stderr.strip())
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            self.log.debug("Timeout fetching %s", endpoint)
            return None

    def _is_newer(self, current: str, candidate_ver: str) -> bool:
        if not current or not candidate_ver:
            return False
        try:
            return Version(candidate_ver.lstrip("v")) > Version(
                current.lstrip("v")
            )
        except InvalidVersion:
            return current != candidate_ver
