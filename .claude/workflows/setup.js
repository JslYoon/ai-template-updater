export const meta = {
  name: 'setup',
  description: 'Phases 1-3: investigate drift, build staging images, stage templates on fork',
  whenToUse: 'Run this to detect version drift and build staging images. After it completes, verify on ROSA cluster, then run /promote.',
  phases: [
    { title: 'Pre-flight', detail: 'Configure permissions, read .env' },
    { title: 'Investigate', detail: 'Check all servers/models for version drift' },
    { title: 'Build', detail: 'Build container images in parallel, push to personal quay' },
    { title: 'Stage', detail: 'Update ai-lab-template env files with staging tags' },
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
  },
  required: ['developer_images_path', 'ai_lab_template_path', 'quay_personal_ns', 'quay_official_ns', 'fork_owner'],
}

const AUDIT_SCHEMA = {
  type: 'object',
  properties: {
    updates_found: { type: 'number' },
    rows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          row_index: { type: 'number' },
          template: { type: 'string' },
          component: { type: 'string' },
          server_type: { type: 'string' },
          current_version: { type: 'string' },
          latest_version: { type: 'string' },
          notes: { type: 'string' },
          source_url: { type: 'string' },
        },
        required: ['template', 'component', 'server_type', 'current_version', 'latest_version'],
      },
    },
  },
  required: ['updates_found', 'rows'],
}

const BUILD_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    server_type: { type: 'string' },
    component: { type: 'string' },
    version: { type: 'string' },
    image_tag: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success', 'server_type', 'component', 'version'],
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

// ── Phase 1: Pre-flight ──────────────────────────────────────────────

phase('Pre-flight')

const env = await agent(
  `Run these commands and return the parsed values:
1. Run: agentic-template-ops configure
2. Read the .env file in the project root (never modify it)
3. Extract and return these values:
   - DEVELOPER_IMAGES_PATH
   - AI_LAB_TEMPLATE_PATH
   - QUAY_PERSONAL_NS
   - QUAY_OFFICIAL_NS
   - FORK_OWNER

4. Verify agentic-template-ops is installed: agentic-template-ops --help

Return the extracted values as structured output.`,
  { label: 'pre-flight', schema: ENV_SCHEMA }
)

if (!env) {
  log('Pre-flight failed. Check .env file and agentic-template-ops installation.')
  return { status: 'failed', phase: 'pre-flight' }
}

log(`Config loaded. Personal quay: ${env.quay_personal_ns}, dev-images: ${env.developer_images_path}`)

const quayAuth = await agent(
  `Check if podman is authenticated to quay.io for the namespace "${env.quay_personal_ns}".
Run: podman login --get-login quay.io
If it returns a username, auth is good — return authenticated: true.
If it fails or says "not logged in", return authenticated: false.`,
  {
    label: 'check-quay-auth',
    schema: {
      type: 'object',
      properties: { authenticated: { type: 'boolean' } },
      required: ['authenticated'],
    },
  }
)

if (!quayAuth || !quayAuth.authenticated) {
  log('Not authenticated to quay.io. Run: podman login quay.io — then re-run setup.')
  return { status: 'failed', phase: 'pre-flight', reason: 'quay auth missing' }
}

log('Quay auth verified.')

// ── Phase 2: Investigate ─────────────────────────────────────────────

phase('Investigate')

const audit = await agent(
  `Run version drift investigation:
1. Run: agentic-template-ops investigate
   This checks all model servers and models for updates and writes results to Google Sheets.
2. After it completes, read the approved rows from the audit log:
   Run: gws sheets +read --spreadsheet 11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ --range "ai audit log"
3. Parse the sheet output. Find rows where Status = "AWAITING_EXECUTION".
4. Return updates_found count and the row data.

If no updates found, return updates_found: 0 and empty rows array.`,
  { label: 'investigate', schema: AUDIT_SCHEMA }
)

if (!audit || audit.updates_found === 0) {
  log('No updates found. Everything current.')
  return { status: 'current', updates_found: 0 }
}

log(`${audit.updates_found} updates found. Deduplicating for builds...`)

// ── JS dedup (zero tokens) ──────────────────────────────────────────

const uniqueServers = {}
const uniqueModels = {}

for (const row of audit.rows) {
  if (row.component === 'server') {
    const key = `${row.server_type}:${row.latest_version}`
    if (!uniqueServers[key]) uniqueServers[key] = row
  } else if (row.component === 'model') {
    const key = `${row.server_type}:${row.latest_version}`
    if (!uniqueModels[key]) uniqueModels[key] = row
  }
}

const serverList = Object.values(uniqueServers)
const modelList = Object.values(uniqueModels)

log(`Unique builds: ${serverList.length} servers, ${modelList.length} models`)

// ── Phase 3: Build ───────────────────────────────────────────────────

phase('Build')

const allBuildItems = [
  ...serverList.map(s => ({
    component: 'server',
    server_type: s.server_type,
    current_version: s.current_version,
    latest_version: s.latest_version,
    notes: s.notes || '',
  })),
  ...modelList.map(m => ({
    component: 'model',
    server_type: m.server_type,
    current_version: m.current_version,
    latest_version: m.latest_version,
    notes: m.notes || '',
  })),
]

const buildResults = await parallel(
  allBuildItems.map(item => () => {
    const isServer = item.component === 'server'
    const prompt = isServer
      ? `Phase 3 setup. Read .env file at .env for config.
Build ONE server image and push to personal quay (quay.io/${env.quay_personal_ns}):
  server_type: ${item.server_type}
  current: ${item.current_version}
  latest: ${item.latest_version}
Developer-images repo: ${env.developer_images_path}
Copy latest version dir, update version pins, podman build, push to personal quay.
Return success/failure, server_type, component "server", version, and image_tag.`
      : `Phase 3 setup. Read .env file at .env for config.
Build ONE model image and push to personal quay (quay.io/${env.quay_personal_ns}):
  model: ${item.server_type}
  current: ${item.current_version}
  latest: ${item.latest_version}
  notes: ${item.notes}
Developer-images repo: ${env.developer_images_path}
Update model Containerfile with new HuggingFace download URL, podman build, push.
Return success/failure, server_type, component "model", version, and image_tag.`

    return agent(prompt, {
      label: `build:${item.server_type}`,
      phase: 'Build',
      agentType: 'impl-builder',
      schema: BUILD_RESULT_SCHEMA,
    })
  })
)

const successfulBuilds = buildResults.filter(Boolean).filter(r => r.success)
const failedBuilds = buildResults.filter(Boolean).filter(r => !r.success)

if (successfulBuilds.length === 0) {
  log('All builds failed. Aborting.')
  return { status: 'failed', phase: 'build', failures: failedBuilds }
}

if (failedBuilds.length > 0) {
  log(`${failedBuilds.length} builds failed: ${failedBuilds.map(f => f.server_type).join(', ')}`)
}

log(`${successfulBuilds.length}/${allBuildItems.length} builds succeeded. Staging templates...`)

// ── Phase 4: Stage templates ─────────────────────────────────────────

phase('Stage')

const serverSummary = JSON.stringify(
  successfulBuilds.filter(b => b.component === 'server').map(b => ({
    server_type: b.server_type,
    version: b.version,
  }))
)

const modelSummary = JSON.stringify(
  successfulBuilds.filter(b => b.component === 'model').map(b => ({
    model: b.server_type,
    version: b.version,
  }))
)

const stageResult = await agent(
  `Phase 3 setup. Read .env file at .env for config.
Pre-verification workflow:
1. cd ${env.ai_lab_template_path}
2. Create ONE branch from main for ALL updates (servers + models together). Never create separate branches.
3. Update scripts/envs/* to use personal quay tags (quay.io/${env.quay_personal_ns})
4. Server updates: ${serverSummary}
5. Model updates: ${modelSummary}
6. Apply ALL server and model changes to the SAME branch in a SINGLE commit.
7. Run ./scripts/import-ai-lab-samples && ./scripts/generate-no-app-template
8. Commit and push to fork (${env.fork_owner})
9. Output the RHDH registration URL

IMPORTANT: Everything goes in ONE branch, ONE commit. Do not split servers and models into separate branches.

Return success, branch_name, and registration_url.`,
  {
    label: 'stage-templates',
    agentType: 'impl-template',
    schema: STAGE_RESULT_SCHEMA,
  }
)

if (!stageResult || !stageResult.success) {
  log('Template staging failed.')
  return { status: 'failed', phase: 'stage', builds: successfulBuilds }
}

log(`Templates staged on branch: ${stageResult.branch_name}`)
log(`Registration URL: ${stageResult.registration_url}`)


return {
  status: 'staged',
  servers_built: successfulBuilds.filter(b => b.component === 'server').length,
  models_built: successfulBuilds.filter(b => b.component === 'model').length,
  build_failures: failedBuilds.map(f => f.server_type),
  registration_url: stageResult.registration_url,
  branch: stageResult.branch_name,
  next_step: 'Verify templates on ROSA cluster, then run /promote',
}
