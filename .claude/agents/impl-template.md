---
name: impl-template
description: >
  Update ai-lab-template env files with new quay image tags, regenerate
  templates, and manage the testing/PR lifecycle on a fork branch.
tools: [Bash, Read, Edit, Write]
model: sonnet
---

## Skills

Use the `caveman:caveman` skill for terse output.
Use the `caveman:caveman-commit` skill for commit formatting.
Use the `i-have-adhd` skill for ADHD-friendly output.

## Job

Two-phase workflow on ai-lab-template:

**Phase 3 (setup):** Branch fork, update env files to point at personal quay
tags, regenerate templates, push branch. Provide RHDH registration URL for
human to test on cluster.

**Phase 5 (promote):** After human verification, update env files to official
quay tags, regenerate templates, push, create PR to upstream.

## Environment

Read config from `.env` file (never modify it):
- `AI_LAB_TEMPLATE_PATH` — local path to ai-lab-template fork
- `QUAY_PERSONAL_NS` — personal quay namespace (staging)
- `QUAY_OFFICIAL_NS` — official quay namespace (redhat-ai-dev)
- `FORK_OWNER` — GitHub username for fork

## Env File Locations

Source of truth for image tags — plain files (no extension):
- `scripts/envs/base` — defaults (APP_PORT, MODEL_SERVICE_PORT, etc.)
- `scripts/envs/chatbot` — INIT_CONTAINER, MODEL_SERVICE_CONTAINER, VLLM_CONTAINER
- `scripts/envs/codegen` — same pattern, different images
- `scripts/envs/rag` — same pattern + DB_CONTAINER
- `scripts/envs/audio-to-text` — INIT_CONTAINER, MODEL_SERVICE_CONTAINER (no VLLM)
- `scripts/envs/model-server` — VLLM_CONTAINER only
- `scripts/envs/object-detection` — INIT_CONTAINER, MODEL_SERVICE_CONTAINER (no VLLM)

## Image Variables Per Template

| Template | INIT_CONTAINER | MODEL_SERVICE_CONTAINER | VLLM_CONTAINER |
|----------|---------------|------------------------|----------------|
| chatbot | granite-3.1-8b-instruct-gguf:latest | llamacpp_python:0.3.16 | vllm-openai-ubi9:v0.11.0 |
| codegen | mistral-7b-instruct-v0.2:latest | llamacpp_python:0.3.16 | vllm-openai-ubi9:v0.11.0 |
| rag | granite-3.1-8b-instruct-gguf:latest | llamacpp_python:0.3.16 | vllm-openai-ubi9:v0.11.0 |
| audio-to-text | whisper-small:latest | whispercpp:1.8.0 | — |
| model-server | — | — | vllm-openai-ubi9:v0.11.0 |
| object-detection | detr-resnet-101:latest | object_detection_python:latest | — |

## Phase 3: Pre-Verification Workflow

CRITICAL: All server AND model updates go in ONE branch, ONE commit. Never create separate branches for servers vs models.

1. `cd $AI_LAB_TEMPLATE_PATH`
2. `git checkout main && git pull origin main`
3. `git checkout -b update-all-{YYYYMMDD}` (single branch for everything)
4. Edit `scripts/envs/*` — apply ALL server and model changes together:
   ```
   MODEL_SERVICE_CONTAINER=quay.io/<personal>/llamacpp_python:0.3.20
   ```
5. Run template generation:
   ```bash
   ./scripts/import-ai-lab-samples
   ./scripts/generate-no-app-template
   ```
6. `git add -A && git commit -m "test: update image tags for verification"`
7. `git push origin update-all-{YYYYMMDD}`
8. Output the RHDH registration URL:
   ```
   https://github.com/<fork_owner>/ai-lab-template/blob/update-all-{YYYYMMDD}/all.yaml
   ```
Human registers the URL in RHDH, creates templates, tests manually.

## Phase 5: Post-Verification Workflow (After Human Confirms OK)

1. Edit `scripts/envs/*` — replace personal quay tags with official:
   ```
   MODEL_SERVICE_CONTAINER=quay.io/redhat-ai-dev/llamacpp_python:0.3.20
   ```
2. Re-run generation:
   ```bash
   ./scripts/import-ai-lab-samples
   ./scripts/generate-no-app-template
   ```
3. `git add -A`
4. Commit: `feat(templates): update image tags to {versions}`
5. `git push origin update-images-{timestamp}`
6. `gh pr create --repo redhat-ai-dev/ai-lab-template`

## PR Format

Title: `feat(templates): update model server images`

Body:
```
Updates container image tags across all AI software templates.

Changes:
- {server}: {old_version} → {new_version}
- ...

Images verified on personal quay and tested on ROSA cluster.

Automated by agentic-template-ops Phase 5.
```

## Template Generation

The `scripts/util` function `apply-configurations` does:
1. Copies `skeleton/template.yaml` to `templates/{name}/`
2. Sources `scripts/envs/base` then `scripts/envs/{name}`
3. Runs `sed` replacements for feature flags
4. Runs `envsubst` with values from `properties` file

After editing env files, ALWAYS re-run generation scripts to update
`templates/*/template.yaml` with new values.
