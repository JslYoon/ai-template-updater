from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import requests

from agentic_template_ops.config import (
    SERVER_CONFIGS,
    AuditResult,
    VersionCheckResult,
)
from agentic_template_ops.model_agents.hf_model_agent import HfModelAgent
from agentic_template_ops.server_agents.llamacpp_agent import LlamacppAgent
from agentic_template_ops.server_agents.object_detection_agent import (
    ObjectDetectionAgent,
)
from agentic_template_ops.server_agents.vllm_agent import VllmAgent
from agentic_template_ops.server_agents.whispercpp_agent import WhispercppAgent

if TYPE_CHECKING:
    from agentic_template_ops.config import AppConfig, TemplateCandidate
    from agentic_template_ops.server_agents.base_server_agent import (
        BaseServerAgent,
    )

AGENT_MAP: dict[str, type[BaseServerAgent]] = {
    "vllm": VllmAgent,
    "llamacpp": LlamacppAgent,
    "whispercpp": WhispercppAgent,
    "object_detection": ObjectDetectionAgent,
}


class DriftScanner:
    def __init__(self, app_config: AppConfig):
        self.config = app_config
        self.session = self._build_session()
        self.model_agent = HfModelAgent(session=self.session)
        self.log = logging.getLogger("drift_scanner")

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        s.headers["User-Agent"] = "agentic-template-ops/0.1"
        return s

    def _get_server_agent(self, server_type: str) -> BaseServerAgent:
        agent_cls = AGENT_MAP.get(server_type)
        if agent_cls is None:
            raise ValueError(f"Unknown server type: {server_type}")
        return agent_cls(session=self.session)

    def investigate(
        self, candidates: list[TemplateCandidate]
    ) -> list[AuditResult]:
        results: list[AuditResult] = []
        futures = {}

        with ThreadPoolExecutor(
            max_workers=self.config.max_workers
        ) as executor:
            for candidate in candidates:
                # Submit server checks -- one per server type listed
                for server_type in candidate.server_types:
                    try:
                        agent = self._get_server_agent(server_type)
                        future = executor.submit(
                            agent.check_for_updates, candidate
                        )
                        futures[future] = (
                            "server",
                            server_type,
                            candidate,
                        )
                    except ValueError as e:
                        self.log.warning(
                            "Skipping %s for %s: %s",
                            server_type,
                            candidate.template_name,
                            e,
                        )

                # Submit model check
                model_future = executor.submit(
                    self.model_agent.check_model,
                    candidate.model_id,
                    None,
                )
                futures[model_future] = (
                    "model",
                    "huggingface",
                    candidate,
                )

            for future in as_completed(futures):
                check_type, sub_type, candidate = futures[future]
                try:
                    result: VersionCheckResult = future.result(timeout=60)
                    results.append(
                        AuditResult(
                            template_name=candidate.template_name,
                            server_type=sub_type,
                            component=result.component,
                            current_version=result.current_version,
                            latest_version=result.latest_version,
                            update_available=result.update_available,
                            source_url=result.source_url,
                            notes=result.notes
                            + (
                                f" ERROR: {result.error}"
                                if result.error
                                else ""
                            ),
                            checked_at=datetime.now(
                                timezone.utc
                            ).isoformat(),
                            upstream_version=getattr(result, "upstream_version", ""),
                            quay_version=getattr(result, "quay_version", ""),
                        )
                    )
                except Exception as e:
                    self.log.error(
                        "Failed %s/%s check for %s: %s",
                        check_type,
                        sub_type,
                        candidate.template_name,
                        e,
                    )
                    results.append(
                        AuditResult(
                            template_name=candidate.template_name,
                            server_type=sub_type,
                            component=check_type,
                            current_version="ERROR",
                            latest_version="ERROR",
                            update_available=False,
                            source_url="",
                            notes=f"Check failed: {e}",
                            checked_at=datetime.now(
                                timezone.utc
                            ).isoformat(),
                        )
                    )

        return results
