---
name: impl-rolling-demo
description: >
  Deploy rolling demo to ROSA cluster. Generates private-env from .env,
  updates values.yaml catalog location to point at staged template branch,
  commits to development branch, and runs make install.
tools: [Bash, Read, Edit, Write]
model: sonnet
---

## Skills

Use the `caveman:caveman` skill for terse output.
Use the `caveman:caveman-commit` skill for commit formatting.

## Job

Deploy ai-rolling-demo-gitops to a ROSA cluster with staged template branch.

### Step 1: Generate private-env

Read ALL key=value pairs from the template updater `.env` file (path given in prompt).
Write `scripts/private-env` in the rolling demo repo. Format:

```bash
#!/bin/bash
export KEY=VALUE
```

Rules:
- Skip comment lines (starting with `#`) and blank lines
- Every `KEY=VALUE` line becomes `export KEY=VALUE`
- Always overwrite the entire file — never append
- No filtering — include all vars. Rolling demo's `setup.sh` validates its own required set

### Step 2: Update catalog location

In `charts/rhdh/values.yaml`, find the ai-lab-template catalog entry:

```yaml
- target: https://github.com/redhat-ai-dev/ai-lab-template/blob/ai-rolling-demo-1_10/all.yaml
```

Replace with the fork branch URL provided in the prompt:

```yaml
- target: https://github.com/<fork_owner>/ai-lab-template/blob/<branch>/all.yaml
```

Only change the `target` value. Leave `type` and `rules` unchanged.

### Step 3: Commit and push

1. `git fetch upstream`
2. Check if local `development` branch exists:
   - If yes: `git checkout development && git pull upstream development`
   - If no: `git checkout -b development --track upstream/development`
3. `git add charts/rhdh/values.yaml scripts/private-env`
4. Commit with message: `test: point templates at staging branch <branch>`
5. `git push origin development`

### Step 4: Run make install

1. `cd <rolling_demo_gitops_path>`
2. `make install`

This is a long-running operation. It installs operators, configures RHDH,
sets up ArgoCD, creates secrets, and deploys the full rolling demo.

After completion, compute and return the RHDH base URL:
`https://<ARGOCD_APP_NAME>-backstage-<RHDH_NAMESPACE>.<RHDH_CLUSTER_ROUTER_BASE>`

Read `ARGOCD_APP_NAME`, `RHDH_NAMESPACE`, and `RHDH_CLUSTER_ROUTER_BASE` from
the `scripts/private-env` file you just wrote.

## Environment

Read config from the `.env` file path provided in the prompt (never modify it).
The rolling demo repo path is also provided in the prompt.

## Error Handling

If `make install` fails, return `success: false` with the error output.
Do not retry automatically — the user needs to investigate.
