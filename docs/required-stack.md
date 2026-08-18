# Required Stack

Everything `ai-template-updater` needs to run, grouped by layer. Ticked items are
required for the core flow; the rest are needed only for specific phases.

See also: [../README.md](../README.md) · [../CLAUDE.md](../CLAUDE.md)

---

## 1. Runtime

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | Runs the `agentic-template-ops` CLI |
| Claude Code | latest | `npm install -g @anthropic-ai/claude-code` — runs the workflows + subagents |

## 2. Python libraries

Declared in `pyproject.toml`. **Actually imported:**

- `click` — CLI framework
- `requests` — HTTP for version checks (quay / PyPI / HuggingFace)
- `tabulate` — `investigate` results table
- `packaging` — semver comparison

**Declared but unused** (safe to remove): `pydantic`, `huggingface-hub`.

## 3. External CLIs

### Used directly by the Python package
| CLI | Used by | Purpose |
|-----|---------|---------|
| `gws` | every Sheet read/write | Google Sheets access (installed by the `gws` Claude plugin) |
| `gh` | server checks (`investigate`) | GitHub API for vLLM / whisper.cpp release tags, file reads, PRs |
| `hf` | model checks (`investigate`) | HuggingFace model info (optional — falls back to the HF HTTP API) |
| `claude` | `setup` / `promote` / `run` | Spawns the impl-* subagents (headless `-p`) — legacy CLI path |

> The lean core: `agentic-template-ops investigate` needs only `gws`, `gh` (and
> optionally `hf`) plus outbound HTTPS. `record-builds` / `list-built` / `configure`
> need at most `gws`.

### Used by the subagents / workflows (build, stage, promote, deploy)
| CLI | Purpose |
|-----|---------|
| `podman` | Build / push / retag container images |
| `git` | Branches, commits, pushes |
| `gh` | Create PRs to upstream repos |
| `skopeo` | Image inspection / tag listing |
| `oc` | OpenShift / ROSA access (rolling demo) |
| `make` | Rolling-demo `make install` |

### Transitive — required on the host that runs the rolling-demo `make install`
`oc`, `kubectl`, `yq`, `argocd`, `cosign`, `openssl`, `envsubst`
(plus the OpenShift operators it installs: GitOps, Pipelines, NFD; RHOAI optional).

## 4. Claude Code plugins

Install with `claude plugins add`:

| Plugin | Provides | Required for |
|--------|----------|--------------|
| `superpowers` | Workflow engine, brainstorming/planning skills | Running `/setup`, `/stage-demo`, `/promote` |
| `gws` | Google Workspace CLI + skills (installs the `gws` binary) | All Sheet I/O |
| `caveman` | Terse subagent output (token savings) | Optional |
| `i-have-adhd` | Output formatting | Optional |

Skills relied on: the **workflow** primitives from `superpowers`, and `gws:gws-sheets*`
for Sheet access. Brainstorming/writing-plans skills are used during development, not at runtime.

## 5. Authentication

| Service | Command | Used for |
|---------|---------|----------|
| quay.io | `podman login quay.io` | Push/pull/retag images |
| GitHub | `gh auth login` | GitHub API (version checks) + PRs + fork pushes |
| Google | `gws auth` | Read Template List, write the Version Status sheet |
| ROSA cluster | `oc login …` | Rolling-demo deploy (optional) |

## 6. Local repo forks

Set their paths in `.env`. Fork + clone with `gh repo fork <repo> --clone`.

| Repo | Purpose |
|------|---------|
| `redhat-ai-dev/developer-images` | Container build sources (Containerfiles, config) |
| `redhat-ai-dev/ai-lab-template` | RHDH software templates (env files, generation scripts) |
| `redhat-ai-dev/ai-rolling-demo-gitops` (optional) | ROSA cluster deployment for testing |

## 7. Google Sheet

One shared spreadsheet, two tabs:
- **Template List** — input (which templates to monitor)
- **Version Status** — output (`report.py`): Model Servers, Models, and the **Audit Log**
  run history that carries build state (`built` + `image_tag`). See CLAUDE.md §9.
