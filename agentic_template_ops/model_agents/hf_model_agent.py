from __future__ import annotations

import json
import logging
import re
import subprocess

import requests
from packaging.version import InvalidVersion, Version

from agentic_template_ops.config import VersionCheckResult
from agentic_template_ops.model_agents.base_model_agent import BaseModelAgent


class HfModelAgent(BaseModelAgent):
    HF_API_BASE = "https://huggingface.co/api/models"

    def __init__(
        self,
        session: requests.Session | None = None,
    ):
        self.session = session or requests.Session()
        self.log = logging.getLogger("agent.model.hf")

    def check_model(
        self, model_id: str, known_sha: str | None = None
    ) -> VersionCheckResult:
        data = self._fetch_model_info(model_id)
        if data is None:
            return VersionCheckResult(
                component="model",
                current_version=known_sha or "unknown",
                latest_version="UNKNOWN",
                update_available=False,
                source_url=f"https://huggingface.co/{model_id}",
                notes="",
                error="Failed to fetch model info",
            )

        latest_sha = data.get("sha", "unknown")[:12]
        last_modified = data.get("last_modified") or data.get(
            "lastModified", "unknown"
        )
        pipeline_tag = data.get("pipeline_tag", "unknown")

        notes_parts = [
            f"pipeline: {pipeline_tag}",
            f"modified: {last_modified}",
        ]

        siblings = data.get("siblings", [])
        gguf_files = [
            s["rfilename"]
            for s in siblings
            if isinstance(s, dict)
            and s.get("rfilename", "").endswith(".gguf")
        ]
        if gguf_files:
            notes_parts.append(f"GGUF files: {len(gguf_files)}")

        family = _parse_model_family(model_id)
        newer = None
        newer_family = None
        if family:
            newer = self._find_newer_family_version(model_id, family)
            if newer:
                newer_family = _parse_model_family(newer)
                notes_parts.append(f"upgrade: {newer}")
                newer_data = self._fetch_model_info(newer)
                if newer_data:
                    newer_modified = newer_data.get("last_modified") or newer_data.get("lastModified", "unknown")
                    notes_parts.append(f"latest_modified: {newer_modified}")

        update_available = newer is not None
        current_display = family[2] if family and family[2] else latest_sha
        if newer and newer_family:
            latest_display = newer_family[2]
        else:
            latest_display = latest_sha

        return VersionCheckResult(
            component="model",
            current_version=current_display,
            latest_version=latest_display,
            update_available=update_available,
            source_url=f"https://huggingface.co/{newer if newer else model_id}",
            notes="; ".join(notes_parts),
        )

    def _find_newer_family_version(
        self, model_id: str, family: tuple[str, str, str, str]
    ) -> str | None:
        author, base, current_ver, suffix = family
        search_terms = base.replace("-", " ")
        if suffix:
            search_terms += " " + suffix.replace("-", " ")

        try:
            resp = self.session.get(
                self.HF_API_BASE,
                params={
                    "author": author,
                    "search": search_terms,
                    "sort": "lastModified",
                    "direction": "-1",
                    "limit": 30,
                },
                timeout=15,
            )
            resp.raise_for_status()
            candidates = resp.json()
        except Exception as e:
            self.log.debug("Family search failed for %s: %s", model_id, e)
            return None

        current_v = _parse_version(current_ver)
        if current_v is None:
            return None

        best_ver = current_v
        best_id = None

        for candidate in candidates:
            cand_id = candidate.get("modelId", "")
            cand_family = _parse_model_family(cand_id)
            if not cand_family:
                continue

            cand_author, cand_base, cand_ver_str, cand_suffix = cand_family

            if cand_author != author:
                continue
            if cand_base.lower() != base.lower():
                continue
            if cand_suffix.lower() != suffix.lower():
                continue

            cand_v = _parse_version(cand_ver_str)
            if cand_v is None or cand_v.is_prerelease:
                continue

            if cand_v > best_ver:
                best_ver = cand_v
                best_id = cand_id

        return best_id

    def _fetch_model_info(self, model_id: str) -> dict | None:
        data = self._try_hf_cli(model_id)
        if data is not None:
            return data
        return self._try_http_api(model_id)

    def _try_hf_cli(self, model_id: str) -> dict | None:
        try:
            cmd = [
                "hf", "models", "info", model_id,
                "--format", "json",
                "--expand",
                "sha,lastModified,pipeline_tag,siblings",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
        return None

    def _try_http_api(self, model_id: str) -> dict | None:
        try:
            resp = self.session.get(
                f"{self.HF_API_BASE}/{model_id}",
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.log.error("HTTP API failed for %s: %s", model_id, e)
            return None


def _parse_model_family(model_id: str) -> tuple[str, str, str, str] | None:
    if "/" not in model_id:
        return None
    author, name = model_id.split("/", 1)

    parts = name.split("-")
    for i, part in enumerate(parts):
        match = re.match(r"^v?(\d+\.\d+(?:\.\d+)?)$", part)
        if match:
            base = "-".join(parts[:i])
            version = match.group(1)
            suffix = "-".join(parts[i + 1 :]) if i + 1 < len(parts) else ""
            return author, base, version, suffix

    return None


def _parse_version(ver_str: str | None) -> Version | None:
    if not ver_str:
        return None
    try:
        return Version(ver_str.lstrip("v"))
    except InvalidVersion:
        return None
