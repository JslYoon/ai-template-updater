from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    server_type: str
    image_name: str
    quay_repo: str
    upstream_source: str  # "github" | "pypi" | "quay"
    upstream_id: str
    tag_prefix: str
    default_port: int
    health_endpoint: str | None


VLLM_CONFIG = ServerConfig(
    server_type="vllm",
    image_name="vllm-openai-ubi9",
    quay_repo="redhat-ai-dev/vllm-openai-ubi9",
    upstream_source="github",
    upstream_id="vllm-project/vllm",
    tag_prefix="v",
    default_port=8000,
    health_endpoint="/health",
)

LLAMACPP_CONFIG = ServerConfig(
    server_type="llamacpp",
    image_name="llamacpp_python",
    quay_repo="redhat-ai-dev/llamacpp_python",
    upstream_source="pypi",
    upstream_id="llama-cpp-python",
    tag_prefix="",
    default_port=8001,
    health_endpoint=None,
)

WHISPERCPP_CONFIG = ServerConfig(
    server_type="whispercpp",
    image_name="whispercpp",
    quay_repo="redhat-ai-dev/whispercpp",
    upstream_source="github",
    upstream_id="ggml-org/whisper.cpp",
    tag_prefix="",
    default_port=8001,
    health_endpoint=None,
)

OBJECT_DETECTION_CONFIG = ServerConfig(
    server_type="object_detection",
    image_name="object_detection_python",
    quay_repo="redhat-ai-dev/object_detection_python",
    upstream_source="quay",
    upstream_id="",
    tag_prefix="",
    default_port=8000,
    health_endpoint="/health",
)

SERVER_CONFIGS: dict[str, ServerConfig] = {
    "vllm": VLLM_CONFIG,
    "llamacpp": LLAMACPP_CONFIG,
    "llama.cpp": LLAMACPP_CONFIG,
    "whispercpp": WHISPERCPP_CONFIG,
    "whisper.cpp": WHISPERCPP_CONFIG,
    "object_detection": OBJECT_DETECTION_CONFIG,
    "detr-resnet-101": OBJECT_DETECTION_CONFIG,
}

# Server types to scan but hide from the Version Status sheet display
# (redundant/noise on the sheet; still tracked internally).
SHEET_HIDDEN_SERVERS: set[str] = {"llamacpp"}

# Audit Log section markers on the "Version Status" sheet. The run history
# lives under this section; setup/promote read the newest run's item rows as
# their work-list. report.py writes these; gws_client.py parses them.
AUDIT_SECTION_TITLE = "Audit Log"
RUN_MARKER_PREFIX = "▶ RUN "

# Audit Log item-row column order — the machine-readable contract shared by
# report.py (writer) and gws_client.py (reader / build-status updater).
# The last two columns track build state so the Sheet is the single source of
# truth after the investigate phase: `built` flips TRUE once an image is pushed,
# and `image_tag` holds the exact pushed tag that staging/promote must use.
AUDIT_ITEM_FIELDS = [
    "template",         # 0
    "component",        # 1
    "server_type",      # 2
    "current_version",  # 3
    "latest_version",   # 4
    "source_url",       # 5
    "notes",            # 6
    "built",            # 7
    "image_tag",        # 8
]
AUDIT_COL_BUILT = 7
AUDIT_COL_IMAGE_TAG = 8
BUILT_TRUE = "TRUE"
BUILT_FALSE = "FALSE"

# Mapping from sheet "Model Server (Link)" values to canonical server type
SERVER_NAME_MAP: dict[str, str] = {
    "vllm": "vllm",
    "vLLM": "vllm",
    "llama.cpp": "llamacpp",
    "llamacpp": "llamacpp",
    "whisper.cpp": "whispercpp",
    "whispercpp": "whispercpp",
    "detr-resnet-101": "object_detection",
    "object_detection": "object_detection",
}


@dataclass
class TemplateCandidate:
    template_name: str
    server_types: list[str]  # canonical types, e.g. ["llamacpp", "vllm"]
    model_id: str  # HuggingFace repo_id
    deployment_target: str
    raw_server_string: str  # original sheet value
    template_url: str = ""  # hyperlink from Name column
    model_url: str = ""  # hyperlink from Model column
    server_url: str = ""  # hyperlink from Model Server column


@dataclass
class VersionCheckResult:
    component: str  # "server" or "model"
    current_version: str
    latest_version: str
    update_available: bool
    source_url: str
    notes: str
    upstream_version: str = ""
    quay_version: str = ""
    error: str | None = None


@dataclass
class AuditResult:
    template_name: str
    server_type: str
    component: str
    current_version: str
    latest_version: str
    update_available: bool
    source_url: str
    notes: str
    checked_at: str
    upstream_version: str = ""
    quay_version: str = ""
    approved: bool = False


@dataclass
class EnvConfig:
    developer_images_path: Path
    ai_lab_template_path: Path
    quay_personal_ns: str
    quay_official_ns: str = "redhat-ai-dev"
    fork_owner: str = ""
    rolling_demo_gitops_path: Path | None = None

    @classmethod
    def from_env_file(cls, env_file: str) -> "EnvConfig":
        env_path = Path(env_file)
        values: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    values[key.strip()] = val.strip()

        def get(key: str, default: str = "") -> str:
            return values.get(key, os.environ.get(key, default))

        dev_path = get("DEVELOPER_IMAGES_PATH")
        tpl_path = get("AI_LAB_TEMPLATE_PATH")
        if not dev_path or not tpl_path:
            raise ValueError(
                "DEVELOPER_IMAGES_PATH and AI_LAB_TEMPLATE_PATH required"
                f" in {env_file} or environment"
            )

        rolling_demo = get("ROLLING_DEMO_GITOPS_PATH")

        return cls(
            developer_images_path=Path(dev_path),
            ai_lab_template_path=Path(tpl_path),
            quay_personal_ns=get("QUAY_PERSONAL_NS"),
            quay_official_ns=get("QUAY_OFFICIAL_NS", "redhat-ai-dev"),
            fork_owner=get("FORK_OWNER"),
            rolling_demo_gitops_path=Path(rolling_demo) if rolling_demo else None,
        )


@dataclass
class AppConfig:
    spreadsheet_id: str = "11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ"
    input_sheet_name: str = "Template List"
    status_sheet_name: str = "Version Status"
    google_credentials_path: Path = Path("service_account.json")
    max_workers: int = 8
    dry_run: bool = False
    target_repos: list[str] = field(default_factory=lambda: [
        "redhat-ai-dev/developer-images",
        "redhat-ai-dev/ai-lab-template",
    ])
