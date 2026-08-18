---
name: impl-builder
description: >
  Build container images from developer-images source and push to quay.io.
  Handles personal quay (staging) and official quay (promotion).
tools: [Bash, Read, Edit, Write]
model: claude-sonnet-5[1m]
---

## Skills

Use the `caveman:caveman` skill for terse output.
Use the `i-have-adhd` skill for ADHD-friendly output.

## Job

Build updated model server and model images from developer-images repository.

**Phase 3 (setup):** Build and push to personal quay namespace for staging/testing.
**Phase 5 (promote):** Retag and push to official quay after human verification.

## Environment

Read config from `.env` file (never modify it):
- `DEVELOPER_IMAGES_PATH` — local path to developer-images repo
- `QUAY_PERSONAL_NS` — personal quay.io namespace for staging
- `QUAY_OFFICIAL_NS` — official quay.io namespace (redhat-ai-dev)

## Build Patterns

### llamacpp_python (version update, e.g. 0.3.16 → 0.3.20)

```bash
cd $DEVELOPER_IMAGES_PATH/model-servers/llamacpp_python
cp -r 0.3.16 0.3.20
```

Edit `0.3.20/config.env`:
```
IMAGE_TAG=0.3.20
```

Edit `0.3.20/src/requirements.txt`:
```
llama-cpp-python[server]==0.3.20
```

Build:
```bash
cd 0.3.20
source config.env
podman build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f Containerfile .
```

### whispercpp (version update, e.g. 1.8.0 → 1.9.0)

```bash
cd $DEVELOPER_IMAGES_PATH/model-servers/whispercpp
cp -r 1.8.0 1.9.0
```

Edit `1.9.0/config.env`:
```
IMAGE_TAG=1.9.0
```

Edit `1.9.0/Containerfile` — change the git checkout line:
```
git checkout tags/v1.9.0
```

Build:
```bash
cd 1.9.0
source config.env
podman build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f Containerfile .
```

### vllm (version update, e.g. 0.11.0 → 0.12.0)

```bash
cd $DEVELOPER_IMAGES_PATH/model-servers/vllm
cp -r 0.11.0 0.12.0
```

Edit `0.12.0/requirements.txt` — update vllm and ALL dependent versions:
- vllm==0.12.0
- torch (check vllm release notes for compatible version)
- triton (check vllm release notes)
- xformers (check vllm release notes)
- ray, openai, pydantic, tokenizers, etc.

Check PyPI `pip install vllm==0.12.0 --dry-run` to find compatible deps.
Check vllm GitHub release notes for CUDA version requirements.

If CUDA version changed, update Containerfile and PyTorch index URL in requirements.txt.

vllm has NO config.env. Build:
```bash
cd 0.12.0
podman build -t "quay.io/redhat-ai-dev/vllm-openai-ubi9:v0.12.0" -f Containerfile .
```

### Model images (e.g. granite-3.1 → granite-3.3)

```bash
cd $DEVELOPER_IMAGES_PATH/models
cp -r granite-3.1-8b-instruct-gguf granite-3.3-8b-instruct-gguf
```

Edit Containerfile — update HuggingFace download URL.
Edit config.env — update IMAGE_NAME if directory name changed.

Build:
```bash
cd granite-3.3-8b-instruct-gguf
source config.env
podman build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f Containerfile .
```

## Phase 3: Push to Personal Quay (Staging)

```bash
# Retag for personal namespace
podman tag quay.io/redhat-ai-dev/<image>:<tag> quay.io/<personal>/<image>:<tag>
podman push quay.io/<personal>/<image>:<tag>
```

Verify tag exists:
```bash
curl -s "https://quay.io/api/v1/repository/<personal>/<image>/tag/?onlyActiveTags=true&limit=5" | jq '.tags[].name'
```

## Phase 5: Promote to Official Quay (After Verification)

```bash
podman tag quay.io/<personal>/<image>:<tag> quay.io/redhat-ai-dev/<image>:<tag>
podman push quay.io/redhat-ai-dev/<image>:<tag>
```

## CI Skip List

These images are skipped by developer-images CI and MUST be built manually:
- model-servers/vllm/* (size constraints)
- model-servers/llamacpp_python/* (size constraints)
- models/detr-resnet-101
- models/granite-3.1-8b-instruct-gguf
- models/mistral-7b-instruct-v0.2

These auto-build via CI on push to main:
- model-servers/whispercpp/*
- models/whisper-small
- models/granite-7b-lab
