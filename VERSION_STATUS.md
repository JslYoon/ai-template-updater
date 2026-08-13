# Version Status

> Current deployed versions across RHDH AI templates.
> Updated after each successful apply-updates cycle.
> Last checked: 2026-08-13 05:45 UTC

## Model Servers

| Server | Image | Current | Upstream | Latest (quay) | Update? | Source |
|--------|-------|---------|----------|---------------|---------|--------|
| llamacpp | `quay.io/redhat-ai-dev/llamacpp` | 0.3.16 | 0.3.34 | 0.3.16 | YES | https://pypi.org/project/llama-cpp-python/0.3.34/ |
| object_detection | `quay.io/redhat-ai-dev/object_detection` | latest (unknown) | — | — | current | https://quay.io/repository/redhat-ai-dev/object_detection_python?tab=tags |
| vllm | `quay.io/redhat-ai-dev/vllm` | 0.11.0 | v0.27.1 | v0.11.0 | YES | https://github.com/vllm-project/vllm/releases/tag/v0.27.1 |
| whispercpp | `quay.io/redhat-ai-dev/whispercpp` | 1.5.4 | v1.9.2 | 1.8.0 | YES | https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2 |

## Models (HuggingFace)

| Model | Used By | Pipeline | Current | Latest | Update? | Current Date | Latest Date |
|-------|---------|----------|---------|--------|---------|--------------|-------------|
| `Any without GPU req` | sample-local |  | N/A | N/A | current | — | — |
| `TheBloke/Mistral-7B-Instruct-v0.2-AWQ` | codegen | text-generation | 0.2 | f970a2bb89d5 | current | 2023-12-11 | — |
| `facebook/detr-resnet-101` | object-detection | object-detection | 7d14702e444d | 7d14702e444d | current | 2023-12-14 | — |
| `ggerganov/whisper.cpp` | audio-to-text | automatic-speech-recognition | 5359861c739e | 5359861c739e | current | 2024-10-29 | — |
| `granite-20b` | openshift-generic |  | N/A | N/A | current | — | — |
| `ibm-granite/granite-3.3-8b-instruct` | chatbot, model-server, rag | text-generation | 3.1 | 3.3 | YES | 2025-04-16 | 2025-05-12 |
| `instructlab/granite-7b-lab` | chatbot-quarkus | text-generation | 4fb6a018d68a | 4fb6a018d68a | current | 2024-06-05 | — |
