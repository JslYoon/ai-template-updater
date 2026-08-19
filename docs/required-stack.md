# Required Stack

Everything `ai-template-updater` needs to run, grouped by layer, with a source URL to
install or configure each item. Ticked items are required for the core flow; the rest
are needed only for specific phases.

See also: [../README.md](../README.md) · [../CLAUDE.md](../CLAUDE.md)

---

## 1. Runtime

| Requirement | Version | Install / docs |
|-------------|---------|----------------|
| Python | 3.11+ | https://www.python.org/downloads/ |
| Claude Code | latest | `npm install -g @anthropic-ai/claude-code` — https://docs.anthropic.com/en/docs/claude-code |

## 2. Python libraries

Declared in `pyproject.toml`; installed via `pip install -e .`.

| Library | Purpose | Source |
|---------|---------|--------|
| `click` | CLI framework | https://pypi.org/project/click/ |
| `requests` | HTTP for version checks (quay / PyPI / HuggingFace) | https://pypi.org/project/requests/ |
| `tabulate` | `investigate` results table | https://pypi.org/project/tabulate/ |
| `packaging` | semver comparison | https://pypi.org/project/packaging/ |
| `huggingface-hub` (>=0.34) | provides the **`hf` CLI** used by `hf_model_agent` (not imported as a library; optional — HTTP fallback) | https://pypi.org/project/huggingface-hub/ |

**Declared but genuinely unused** (safe to remove): `pydantic` — https://pypi.org/project/pydantic/

## 3. External CLIs

### Used directly by the Python package
| CLI | Used by | Purpose | Install / docs |
|-----|---------|---------|----------------|
| `gws` | every Sheet read/write | Google Sheets access (installed by the `gws` Claude plugin) | https://github.com/WadeWarren/gws-claude-plugin |
| `gh` | server checks (`investigate`), PRs | GitHub API for vLLM / whisper.cpp release tags, file reads, PRs | https://cli.github.com/ |
| `hf` | model checks (`investigate`) | HuggingFace model info (optional — falls back to the HF HTTP API) | https://huggingface.co/docs/huggingface_hub/guides/cli |
| `claude` | `setup` / `promote` / `run` | Spawns the impl-* subagents (headless `-p`) — legacy CLI path | https://docs.anthropic.com/en/docs/claude-code |

> The lean core: `agentic-template-ops investigate` needs only `gws`, `gh` (and
> optionally `hf`) plus outbound HTTPS. `record-builds` / `list-built` / `configure`
> need at most `gws`.

### Used by the subagents / workflows (build, stage, promote, deploy)
| CLI | Purpose | Install / docs |
|-----|---------|----------------|
| `podman` | Build / push / retag container images | https://podman.io/docs/installation |
| `git` | Branches, commits, pushes | https://git-scm.com/downloads |
| `gh` | Create PRs to upstream repos | https://cli.github.com/ |
| `skopeo` | Image inspection / tag listing | https://github.com/containers/skopeo/blob/main/install.md |
| `oc` | OpenShift / ROSA access (rolling demo) | https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html |
| `make` | Rolling-demo `make install` | https://www.gnu.org/software/make/ |

### Transitive — required on the host that runs the rolling-demo `make install`
| CLI | Install / docs |
|-----|----------------|
| `oc` | https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html |
| `kubectl` | https://kubernetes.io/docs/tasks/tools/ |
| `yq` | https://github.com/mikefarah/yq#install |
| `argocd` | https://argo-cd.readthedocs.io/en/stable/cli_installation/ |
| `cosign` | https://docs.sigstore.dev/cosign/system_config/installation/ |
| `openssl` | https://www.openssl.org/source/ (usually preinstalled) |
| `envsubst` | part of GNU gettext — https://www.gnu.org/software/gettext/ |

(Plus the OpenShift operators `make install` provisions: GitOps, Pipelines, NFD; RHOAI optional.)

## 4. Claude Code plugins

Install with `claude plugins add`.

| Plugin | Provides | Required for | Source |
|--------|----------|--------------|--------|
| `superpowers` | Workflow engine, brainstorming/planning skills | Running `/setup`, `/stage-demo`, `/promote` | `claude plugins add superpowers@anthropics/claude-plugins-official` |
| `gws` | Google Workspace CLI + skills (installs the `gws` binary) | All Sheet I/O | https://github.com/WadeWarren/gws-claude-plugin |
| `caveman` | Terse subagent output (token savings) | Optional | https://github.com/JuliusBrussee/caveman |
| `i-have-adhd` | Output formatting | Optional | https://github.com/ayghri/i-have-adhd |

Skills relied on: the **workflow** primitives from `superpowers`, and `gws:gws-sheets*`
for Sheet access. Brainstorming/writing-plans skills are used during development, not at runtime.

## 5. Authentication

| Service | Command | Used for | Docs |
|---------|---------|----------|------|
| quay.io | `podman login quay.io` | Push/pull/retag images | https://docs.quay.io/guides/login.html |
| GitHub | `gh auth login` | GitHub API (version checks) + PRs + fork pushes | https://cli.github.com/manual/gh_auth_login |
| Google | `gws auth` | Read Template List, write the Version Status sheet | https://github.com/WadeWarren/gws-claude-plugin |
| ROSA cluster | `oc login …` | Rolling-demo deploy (optional) | https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html |

## 6. Local repo forks

Set their paths in `.env`. Fork + clone with `gh repo fork <repo> --clone`.

| Repo | Purpose | Source |
|------|---------|--------|
| `redhat-ai-dev/developer-images` | Container build sources (Containerfiles, config) | https://github.com/redhat-ai-dev/developer-images |
| `redhat-ai-dev/ai-lab-template` | RHDH software templates (env files, generation scripts) | https://github.com/redhat-ai-dev/ai-lab-template |
| `redhat-ai-dev/ai-rolling-demo-gitops` (optional) | ROSA cluster deployment for testing | https://github.com/redhat-ai-dev/ai-rolling-demo-gitops |

## 7. Google Sheet

One shared spreadsheet — [Version drift tracker](https://docs.google.com/spreadsheets/d/11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ/edit)
(id `11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ`, the default in `cli.py`). Two tabs:
- **Template List** — input (which templates to monitor)
- **Version Status** — output (`report.py`): Model Servers, Models, and the **Audit Log**
  run history that carries build state (`built` + `image_tag`). See CLAUDE.md §9.
