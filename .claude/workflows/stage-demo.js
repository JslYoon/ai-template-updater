export const meta = {
  name: 'stage-demo',
  description: 'Stage the built images (read from the Sheet) on the ai-lab-template fork and deploy the rolling demo (skips investigate + build)',
  whenToUse: 'Run when images are already built + recorded to the Sheet (via /setup Build+Record) and you only need to stage templates and deploy the rolling demo. Skips the drift scan and image builds.',
  phases: [
    { title: 'Pre-flight', detail: 'Read .env config' },
    { title: 'Read', detail: 'Read built image tags from the Sheet (source of truth)' },
    { title: 'Stage', detail: 'Update ai-lab-template env files with the exact built tags, push fork branch' },
    { title: 'Deploy', detail: 'Deploy rolling demo to ROSA cluster' },
  ],
}

const ENV_SCHEMA = {
  type: 'object',
  properties: {
    developer_images_path: { type: 'string' },
    ai_lab_template_path: { type: 'string' },
    quay_personal_ns: { type: 'string' },
    quay_official_ns: { type: 'string' },
    fork_owner: { type: 'string' },
    rolling_demo_gitops_path: { type: 'string' },
  },
  required: ['ai_lab_template_path', 'quay_personal_ns', 'fork_owner'],
}

// list-built output: the newest run's built rows (source of truth for staging)
const BUILT_LIST_SCHEMA = {
  type: 'object',
  properties: {
    built: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          template: { type: 'string' },
          component: { type: 'string' },
          server_type: { type: 'string' },
          current_version: { type: 'string' },
          latest_version: { type: 'string' },
          image_tag: { type: 'string' },
          notes: { type: 'string' },
        },
        required: ['component', 'server_type', 'latest_version', 'image_tag'],
      },
    },
  },
  required: ['built'],
}

const STAGE_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    branch_name: { type: 'string' },
    registration_url: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success'],
}

const DEPLOY_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    rhdh_base_url: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success'],
}

const TERSE = 'Be terse. No filler, no narration, no preamble. Action and result only.\n'

// ── Phase 1: Pre-flight ──────────────────────────────────────────────

phase('Pre-flight')

const env = await agent(
  TERSE + `Read config for a stage+deploy run:
1. Run: agentic-template-ops configure
2. Read the .env file in the project root (never modify it)
3. Extract and return:
   - DEVELOPER_IMAGES_PATH
   - AI_LAB_TEMPLATE_PATH
   - QUAY_PERSONAL_NS
   - QUAY_OFFICIAL_NS
   - FORK_OWNER
   - ROLLING_DEMO_GITOPS_PATH (optional — empty string if unset)
Return the extracted values as structured output.`,
  { label: 'pre-flight', model: 'claude-sonnet-4-6', schema: ENV_SCHEMA }
)

if (!env) {
  log('Pre-flight failed. Check .env and agentic-template-ops installation.')
  return { status: 'failed', phase: 'pre-flight' }
}

log(`Config loaded. Personal quay: ${env.quay_personal_ns}, template: ${env.ai_lab_template_path}`)

// ── Phase 2: Read built rows from the Sheet ──────────────────────────

phase('Read')

const recorded = await agent(
  TERSE + `Read the built updates from the Google Sheet.
Run: agentic-template-ops list-built
Return the JSON array it prints as {"built": <that array>}. Do not modify any values.`,
  { label: 'list-built', phase: 'Read', model: 'claude-sonnet-4-6', schema: BUILT_LIST_SCHEMA }
)

const builtRows = (recorded && recorded.built) || []
if (builtRows.length === 0) {
  log('No built images in the latest audit run. Run /setup (Build+Record) first.')
  return { status: 'failed', phase: 'read' }
}
log(`${builtRows.length} built rows from the Sheet.`)

// ── Phase 3: Stage templates (exact tags from the Sheet) ─────────────

phase('Stage')

const serverSummary = JSON.stringify(
  builtRows.filter(b => b.component === 'server').map(b => ({
    server_type: b.server_type,
    version: b.latest_version,
    image_tag: b.image_tag,
  }))
)

const modelSummary = JSON.stringify(
  builtRows.filter(b => b.component === 'model').map(b => ({
    model: b.server_type,
    version: b.latest_version,
    image_tag: b.image_tag,
  }))
)

const stageResult = await agent(
  TERSE + `Stage pre-built images on the ai-lab-template fork. Read .env at .env for config.
Pre-verification workflow:
1. cd ${env.ai_lab_template_path}
2. Create ONE branch from main for ALL updates (servers + models together). Never create separate branches.
3. Update scripts/envs/* to use the EXACT image_tag from each entry below — do NOT reconstruct or reformat tags.
4. Server updates: ${serverSummary}
5. Model updates: ${modelSummary}
6. Apply ALL changes to the SAME branch in a SINGLE commit.
7. Run ./scripts/import-ai-lab-samples && ./scripts/generate-no-app-template
8. Commit and push to fork (${env.fork_owner})
9. Output the RHDH registration URL

IMPORTANT: Everything goes in ONE branch, ONE commit. Use image_tag verbatim.

Return success, branch_name, and registration_url.`,
  {
    label: 'stage-templates',
    agentType: 'impl-template',
    schema: STAGE_RESULT_SCHEMA,
  }
)

if (!stageResult || !stageResult.success) {
  log('Template staging failed.')
  return { status: 'failed', phase: 'stage' }
}

log(`Templates staged on branch: ${stageResult.branch_name}`)
log(`Registration URL: ${stageResult.registration_url}`)

// ── Phase 4: Deploy rolling demo ────────────────────────────────────

let deployResult = null

if (!env.rolling_demo_gitops_path) {
  log('ROLLING_DEMO_GITOPS_PATH not set — skipping rolling demo deploy.')
} else {
  phase('Deploy')

  deployResult = await agent(
    TERSE + `Deploy rolling demo to ROSA cluster. Read .env at .env for config.
Rolling demo repo: ${env.rolling_demo_gitops_path}
Fork owner: ${env.fork_owner}
Template branch: ${stageResult.branch_name}

1. Generate private-env from .env (overwrite always)
2. Update values.yaml catalog location to fork branch
3. Commit to development branch, push
4. Run make install

Return success and rhdh_base_url.`,
    {
      label: 'deploy-rolling-demo',
      agentType: 'impl-rolling-demo',
      schema: DEPLOY_RESULT_SCHEMA,
    }
  )

  if (!deployResult || !deployResult.success) {
    log('Rolling demo deploy failed. Check agent output for details.')
  } else {
    log(`Rolling demo deployed: ${deployResult.rhdh_base_url}`)
  }
}

return {
  status: 'staged',
  servers_staged: builtRows.filter(b => b.component === 'server').length,
  models_staged: builtRows.filter(b => b.component === 'model').length,
  registration_url: stageResult.registration_url,
  branch: stageResult.branch_name,
  rhdh_url: env.rolling_demo_gitops_path ? deployResult?.rhdh_base_url : null,
  next_step: 'Test templates on RHDH, then run /promote',
}
