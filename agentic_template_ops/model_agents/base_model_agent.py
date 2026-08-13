from __future__ import annotations

import abc

from agentic_template_ops.config import VersionCheckResult


class BaseModelAgent(abc.ABC):
    @abc.abstractmethod
    def check_model(
        self, model_id: str, known_sha: str | None = None
    ) -> VersionCheckResult:
        ...
