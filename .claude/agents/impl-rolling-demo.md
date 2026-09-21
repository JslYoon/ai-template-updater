---
name: impl-rolling-demo
description: >
  Deploy rolling demo to ROSA cluster. Syncs CLUSTER_API/CLUSTER_TOKEN into the
  existing private-env (merge in place, never overwrite secrets),
  updates values.yaml catalog location to point at staged template branch,
  commits to development branch, and runs make install-no-rhoai.
tools: [Bash, Read, Edit, Write]
model: claude-sonnet-5[1m]
---

## Skills

Use the `caveman:caveman` skill for terse output.
Use the `caveman:caveman-commit` skill for commit formatting.

## Job

Deploy ai-rolling-demo-gitops to a ROSA cluster with staged template branch.

### Step 1: Sync private-env (merge in place — NEVER overwrite)

`scripts/private-env` in the rolling demo repo already exists and holds required
secrets that are NOT in the template updater `.env` (GITHUB_APP_*,
GITOPS_GIT_TOKEN, QUAY_DOCKERCONFIGJSON, KEYCLOAK_*, OPENAI_API_KEY,
VLLM_API_KEY, POSTGRES/LIGHTSPEED passwords, …). Overwriting it from `.env`
irrecoverably destroys those secrets and makes `setup.sh` validation abort.

Rules:
- **Never overwrite or regenerate the whole file when it exists.** Edit IN PLACE.
- Update ONLY `CLUSTER_API` and `CLUSTER_TOKEN` to match the template updater
  `.env` (as `export CLUSTER_API=…` / `export CLUSTER_TOKEN=…`). If those lines
  are missing, add just those two.
- **Sync `RHDH_CLUSTER_ROUTER_BASE` to the cluster `CLUSTER_API` points at**
  (REQUIRED — it must move with the cluster, or the RHDH route is generated on a
  dead domain and DNS fails). It is NOT in `.env`; derive it from the live
  cluster after logging in:
  ```bash
  oc login "$CLUSTER_API" --token="$CLUSTER_TOKEN"
  domain=$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}')
  # → e.g. apps.rosa.p4wtt-kq36a-ypk.w7wk.p3.openshiftapps.com
  ```
  Set `export RHDH_CLUSTER_ROUTER_BASE=$domain` in private-env. If the existing
  value differs from the live domain, it is stale — replace it.
- **Sync `GITHUB_APP_WEBHOOK_URL` to the cluster too** (REQUIRED — it is the
  Pipelines-as-Code controller route and lives on the cluster router base, so it
  goes stale the moment the cluster changes; a stale webhook URL points GitHub at
  a dead domain). This is the ONE `GITHUB_APP_*` line you may change — never touch
  the other `GITHUB_APP_*` secrets (id, key, secret, client id/secret). Derive it
  from the live PAC route (fall back to templating on the router base if the route
  is not up yet):
  ```bash
  webhook_host=$(oc get route pipelines-as-code-controller -n openshift-pipelines \
    -o jsonpath='{.spec.host}' 2>/dev/null)
  webhook_host="${webhook_host:-pipelines-as-code-controller-openshift-pipelines.$domain}"
  # → export GITHUB_APP_WEBHOOK_URL="https://$webhook_host"
  ```
  Set `export GITHUB_APP_WEBHOOK_URL="https://$webhook_host"`. If the existing
  value's host does not match the live router base, it is stale — replace it.
  Leave `GITHUB_APP_WEBHOOK_SECRET` and all other secrets untouched.
- Do NOT copy the path/registry block from `.env` into private-env
  (`DEVELOPER_IMAGES_PATH`, `AI_LAB_TEMPLATE_PATH`, `QUAY_PERSONAL_NS`,
  `QUAY_OFFICIAL_NS`, `FORK_OWNER`, `ROLLING_DEMO_GITOPS_PATH`) — private-env
  does not use them.
- `GITOPS_TARGET_REVISION` is handled in Step 2b. Leave every other line exactly
  as-is; never touch the secrets.
- Only if `scripts/private-env` does NOT exist: generate it fresh from `.env`
  (`export KEY=VALUE` per non-comment line), then still derive and set
  `RHDH_CLUSTER_ROUTER_BASE` from the cluster as above — but a pre-existing file
  is the normal path.

**Stale route:** if `RHDH_CLUSTER_ROUTER_BASE` changed, an old
`rolling-demo-backstage` route may still carry the previous domain. After
`make install-no-rhoai`, verify `oc get route rolling-demo-backstage -n <ns>
-o jsonpath='{.spec.host}'` matches the new router base; if not, delete the stale
route (`oc delete route rolling-demo-backstage -n <ns>`) and let ArgoCD/make
recreate it.

### Step 2: Update catalog location

In `charts/rhdh/values.yaml`, find the rhdh-ai-template catalog entry:

```yaml
- target: https://github.com/redhat-developer/rhdh-ai-template/blob/ai-rolling-demo-1_10/all.yaml
```

Replace with the fork branch URL provided in the prompt:

```yaml
- target: https://github.com/<fork_owner>/rhdh-ai-template/blob/<branch>/all.yaml
```

Only change the `target` value. Leave `type` and `rules` unchanged.

### Step 2b: Force the deploy target branch to `development` (REQUIRED)

The ArgoCD `rolling-demo` Application MUST track the **`development`** branch of
the rolling-demo-gitops fork, because Step 3 commits the catalog edit there. If
the app tracks any other branch (e.g. `main`), the catalog edit is never read and
the demo silently serves the upstream/old templates.

Before running `make install-no-rhoai`, ensure `scripts/private-env` contains exactly:

```bash
export GITOPS_TARGET_REVISION=development
```

If the line is missing or set to anything else, rewrite it to `development`
(this is the only var in `private-env` you may change; never touch the secrets).
`setup.sh` sources `private-env`, and `apply-argocd-application.sh` copies
`GITOPS_TARGET_REVISION` into the Application's `.spec.source.targetRevision`.

### Step 3: Commit and push

1. `git fetch upstream`
2. Check if local `development` branch exists:
   - If yes: `git checkout development && git pull upstream development`
   - If no: `git checkout -b development --track upstream/development`
3. `git add charts/rhdh/values.yaml scripts/private-env`
4. Commit with message: `test: point templates at staging branch <branch>`
5. `git push origin development`

### Step 4: Run make install-no-rhoai

1. `cd <rolling_demo_gitops_path>`
2. `make install-no-rhoai`

This is a long-running operation. It installs operators, configures RHDH,
sets up ArgoCD, creates secrets, and deploys the full rolling demo.

After completion, compute the RHDH base URL:
`https://<ARGOCD_APP_NAME>-backstage-<RHDH_NAMESPACE>.<RHDH_CLUSTER_ROUTER_BASE>`

Read `ARGOCD_APP_NAME`, `RHDH_NAMESPACE`, and `RHDH_CLUSTER_ROUTER_BASE` from
the `scripts/private-env` file you just wrote.

Also compute the two URLs the user applies on their GitHub App (return both):
- **Callback URL** (`rhdh_callback_url`): `<RHDH_BASE_URL>/api/auth/oidc/handler/frame`
- **Webhook URL** (`github_webhook_url`): the `GITHUB_APP_WEBHOOK_URL` you synced
  in Step 1 (the live PAC controller route).

### Step 4b: Reconcile the live app to `development` (REQUIRED)

`apply-argocd-application.sh` sets `.spec.source.targetRevision` **only when it
first creates** the Application; if `rolling-demo` already exists it exits without
updating. So a re-run against a cluster that still has an old app can leave it
tracking the wrong branch. After `make install-no-rhoai`, always reconcile:

```bash
current=$(oc get application "$ARGOCD_APP_NAME" -n openshift-gitops \
  -o jsonpath='{.spec.source.targetRevision}')
if [ "$current" != "development" ]; then
  oc patch application "$ARGOCD_APP_NAME" -n openshift-gitops --type merge \
    -p '{"spec":{"source":{"targetRevision":"development"}}}'
  oc annotate application "$ARGOCD_APP_NAME" -n openshift-gitops \
    argocd.argoproj.io/refresh=hard --overwrite
fi
```

### Step 5: Verify deployment

Do not report success until all checks pass:

1. `oc get application -n openshift-gitops <ARGOCD_APP_NAME> -o jsonpath='{.spec.source.targetRevision}'` — MUST be `development`
2. `oc get pods -n <RHDH_NAMESPACE>` — all pods must be Running with all containers ready
3. `oc get application -n openshift-gitops <ARGOCD_APP_NAME>` — health status must be Healthy
4. `curl -skL -o /dev/null -w '%{http_code}' <RHDH_BASE_URL>` — must return 200

If pods are in Init or ContainerCreating, poll every 15 seconds (up to 10 minutes).
If any check fails after timeout, return `success: false` with the failing check output.

Only after all three checks pass, return success with the RHDH base URL,
`github_webhook_url`, and `rhdh_callback_url`.

## Environment

Read config from the `.env` file path provided in the prompt (never modify it).
The rolling demo repo path is also provided in the prompt.

## Error Handling

If `make install-no-rhoai` fails, return `success: false` with the error output.
Do not retry automatically — the user needs to investigate.

## Log inspection

Never read whole log files. `make install-no-rhoai` and the deploy scripts emit huge
logs — reading them in full blows the context window. Inspect with
`rg`/`grep`/`tail` only: e.g. `tail -n 100 <log>`,
`grep -C 5 -iE 'error|fail' <log>`. Never `cat` or Read a log over ~100 lines;
quote only the shortest decisive lines (that error output is the shortest
decisive lines, not the whole log).
