export const meta = {
  name: 'promote',
  description: 'Phase 5: retag images to official quay, create PRs for developer-images and ai-lab-template, update audit log',
  whenToUse: 'Run this AFTER verifying staged templates on ROSA cluster (after /setup). Promotes staging images to production.',
  phases: [
    { title: 'Config', detail: 'Read .env and approved rows from audit log' },
    { title: 'Promote', detail: 'Retag images from personal to official quay in parallel' },
    { title: 'DevImages', detail: 'Commit version dirs to developer-images, create PRs (sequential)' },
    { title: 'Templates', detail: 'Update ai-lab-template to official tags, create upstream PR' },
    { title: 'Audit', detail: 'Mark audit log rows as promoted' },
  ],
}

const CONFIG_SCHEMA = {
  type: 'object',
  properties: {
    developer_images_path: { type: 'string' },
    ai_lab_template_path: { type: 'string' },
    quay_personal_ns: { type: 'string' },
    quay_official_ns: { type: 'string' },
    fork_owner: { type: 'string' },
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
        required: ['row_index', 'template', 'component', 'server_type', 'current_version', 'latest_version'],
      },
    },
  },
  required: ['developer_images_path', 'ai_lab_template_path', 'quay_personal_ns', 'quay_official_ns', 'fork_owner', 'rows'],
}

const PROMOTE_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    server_type: { type: 'string' },
    component: { type: 'string' },
    version: { type: 'string' },
    official_tag: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success', 'server_type', 'component', 'version'],
}

const DEVIMAGES_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    server_type: { type: 'string' },
    version: { type: 'string' },
    pr_url: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success', 'server_type', 'version'],
}

const TEMPLATE_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    pr_url: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['success'],
}

// ── Phase 1: Read Config ─────────────────────────────────────────────

phase('Config')

const config = await agent(
  `Read configuration for Phase 5 promote:
1. Read .env file in project root (never modify it). Extract:
   - DEVELOPER_IMAGES_PATH, AI_LAB_TEMPLATE_PATH
   - QUAY_PERSONAL_NS, QUAY_OFFICIAL_NS, FORK_OWNER
2. Read approved rows from audit log:
   Run: gws sheets +read --spreadsheet 11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ --range "ai audit log"
3. Parse rows where Status = "AWAITING_EXECUTION". Return all fields including row_index.
4. Return config values + rows array.`,
  { label: 'read-config', schema: CONFIG_SCHEMA }
)

if (!config || !config.rows || config.rows.length === 0) {
  log('No updates awaiting promotion.')
  return { status: 'nothing_to_promote' }
}

log(`${config.rows.length} updates to promote. Deduplicating...`)

// ── JS dedup ─────────────────────────────────────────────────────────

const uniqueServers = {}
const uniqueModels = {}

for (const row of config.rows) {
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

log(`Unique promotes: ${serverList.length} servers, ${modelList.length} models`)

// ── Phase 2: Promote Images (parallel retag) ─────────────────────────

phase('Promote')

const allPromoteItems = [
  ...serverList.map(s => ({ component: 'server', ...s })),
  ...modelList.map(m => ({ component: 'model', ...m })),
]

const promoteResults = await parallel(
  allPromoteItems.map(item => () => {
    const isServer = item.component === 'server'
    const prompt = isServer
      ? `Phase 5 promote. Read .env file at .env for config.
Retag ONE server image from personal quay (quay.io/${config.quay_personal_ns}) to official quay (quay.io/${config.quay_official_ns}) and push.
  server_type: ${item.server_type}
  version: ${item.latest_version}
Return success, server_type, component "server", version, and official_tag.`
      : `Phase 5 promote. Read .env file at .env for config.
Retag ONE model image from personal quay (quay.io/${config.quay_personal_ns}) to official quay (quay.io/${config.quay_official_ns}) and push.
  model: ${item.server_type}
  version: ${item.latest_version}
Return success, server_type, component "model", version, and official_tag.`

    return agent(prompt, {
      label: `promote:${item.server_type}`,
      phase: 'Promote',
      agentType: 'impl-builder',
      schema: PROMOTE_RESULT_SCHEMA,
    })
  })
)

const promoted = promoteResults.filter(Boolean).filter(r => r.success)
const promoteFailed = promoteResults.filter(Boolean).filter(r => !r.success)

if (promoted.length === 0) {
  log('All promotions failed. Aborting.')
  return { status: 'failed', phase: 'promote', failures: promoteFailed }
}

if (promoteFailed.length > 0) {
  log(`${promoteFailed.length} promotions failed: ${promoteFailed.map(f => f.server_type).join(', ')}`)
}

log(`${promoted.length}/${allPromoteItems.length} images promoted to official quay.`)

// ── Phase 3: Commit Dev Images (sequential — shared git working tree) ──

phase('DevImages')

const devimagesResults = []

for (let i = 0; i < serverList.length; i++) {
  const server = serverList[i]
  log(`DevImages ${i + 1}/${serverList.length}: ${server.server_type} ${server.latest_version}`)

  const result = await agent(
    `Phase 5 promote. Read .env file at .env for config.
Commit version directory for ONE server in ${config.developer_images_path} and create PR to upstream redhat-ai-dev/developer-images.
Fork owner: ${config.fork_owner}
  server_type: ${server.server_type}
  version: ${server.latest_version}
Return success, server_type, version, and pr_url.`,
    {
      label: `devimages:${server.server_type}`,
      agentType: 'impl-devimages',
      schema: DEVIMAGES_RESULT_SCHEMA,
    }
  )

  devimagesResults.push(result)
}

const devimagesSuccess = devimagesResults.filter(Boolean).filter(r => r.success)
const devimagesFailed = devimagesResults.filter(Boolean).filter(r => !r.success)

if (devimagesFailed.length > 0) {
  log(`${devimagesFailed.length} dev-images PRs failed: ${devimagesFailed.map(f => f.server_type).join(', ')}`)
}

log(`${devimagesSuccess.length}/${serverList.length} dev-images PRs created.`)

// ── Phase 4: Update Templates (official tags + upstream PR) ──────────

phase('Templates')

const serverSummary = JSON.stringify(
  promoted.filter(p => p.component === 'server').map(p => ({
    server_type: p.server_type,
    current: config.rows.find(r => r.server_type === p.server_type)?.current_version,
    latest: p.version,
  }))
)

const modelSummary = JSON.stringify(
  promoted.filter(p => p.component === 'model').map(p => ({
    model: p.server_type,
    current: config.rows.find(r => r.server_type === p.server_type)?.current_version,
    latest: p.version,
  }))
)

const templateResult = await agent(
  `Phase 5 promote. Read .env file at .env for config.
Post-verification workflow:
1. Use the EXISTING branch (from setup phase) — do NOT create a new branch. Check out the most recent update-all-* branch.
2. Update scripts/envs/* to use official quay tags (quay.io/${config.quay_official_ns}) — ALL server and model changes in ONE commit.
3. Server updates: ${serverSummary}
4. Model updates: ${modelSummary}
5. Re-run generation scripts
6. Commit, push, create PR to upstream redhat-ai-dev/ai-lab-template
Fork owner: ${config.fork_owner}

IMPORTANT: Everything in ONE branch, ONE commit. Never split servers and models into separate branches.

Return success and pr_url.`,
  {
    label: 'template-pr',
    agentType: 'impl-template',
    schema: TEMPLATE_RESULT_SCHEMA,
  }
)

if (!templateResult || !templateResult.success) {
  log('Template PR creation failed.')
}

// ── Phase 5: Update Audit Log ────────────────────────────────────────

phase('Audit')

const rowIndices = config.rows.map(r => r.row_index)

await agent(
  `Update audit log in Google Sheets. For each row index below, set column G (Status) to "PROMOTED — PRs pending".

Spreadsheet ID: 11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ
Sheet: ai audit log
Row indices: ${JSON.stringify(rowIndices)}

For each row, run:
gws sheets spreadsheets values update --params '{"spreadsheetId":"11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ","range":"ai audit log!G<ROW_INDEX>","valueInputOption":"RAW"}' --json '{"values":[["PROMOTED — PRs pending"]]}' --format json

Replace <ROW_INDEX> with each row index.`,
  { label: 'audit-log' }
)

log('Audit log updated.')

// ── Summary ──────────────────────────────────────────────────────────

const prs = [
  ...(devimagesSuccess.map(d => ({ repo: 'developer-images', type: d.server_type, url: d.pr_url }))),
  ...(templateResult && templateResult.success ? [{ repo: 'ai-lab-template', type: 'templates', url: templateResult.pr_url }] : []),
]

return {
  status: promoted.length === allPromoteItems.length ? 'success' : 'partial',
  promoted: promoted.length,
  failed: promoteFailed.map(f => f.server_type),
  prs: prs,
}
