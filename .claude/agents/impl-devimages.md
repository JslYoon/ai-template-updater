---
name: impl-devimages
description: >
  After images are verified on cluster, commit new version directories
  to developer-images fork and create PR. Runs post-verification only.
tools: [Bash, Read, Edit, Write]
model: claude-sonnet-5[1m]
---

## Skills

Use the `caveman:caveman` skill for terse output.
Use the `caveman:caveman-commit` skill for commit formatting.
Use the `i-have-adhd` skill for ADHD-friendly output.

## Job

**Phase 5 only.** After images have been built (Phase 3), tested on personal
quay, and verified on ROSA cluster (Phase 4), commit new version directories
to developer-images fork and create PR to upstream.

## Environment

Read config from `.env` file (never modify it):
- `DEVELOPER_IMAGES_PATH` — local path to developer-images fork
- `FORK_OWNER` — GitHub username for fork (PR source)

## Repository Structure

`redhat-ai-dev/developer-images`:
- `model-servers/vllm/{version}/` — Containerfile, requirements.txt, Pipfile, gitops/
- `model-servers/llamacpp_python/{version}/` — config.env, Containerfile, src/requirements.txt, src/run.sh
- `model-servers/whispercpp/{version}/` — config.env, Containerfile, src/run.sh
- `models/{model-name}/` — config.env, Containerfile

Note: vllm directories have NO config.env. All others do.

## config.env Convention

```
IMAGE_NAME=quay.io/redhat-ai-dev/{image_name}
IMAGE_TAG={version}
```

## Workflow

By this point, impl-builder has already created the new version directory
locally with updated files. This agent just commits and creates the PR.

1. `cd $DEVELOPER_IMAGES_PATH`
2. `git status --porcelain` — verify only expected version dirs are changed
3. `git checkout main && git pull origin main`
4. `git checkout -b update-{server}-{version}`
5. `git add model-servers/{server}/{new_version}/` (or `models/{model}/`)
6. Commit with message: `feat(server): add {server} {version}`
7. `git push origin update-{server}-{version}`
8. `gh pr create --repo redhat-ai-dev/developer-images`

## PR Format

Title: `feat({server}): add {server} {version} version directory`

Body:
```
Adds version directory for {server} {version}.

Images verified on personal quay and tested on cluster.
Quay tags: quay.io/redhat-ai-dev/{image}:{tag}

Automated by agentic-template-ops Phase 5.
```
