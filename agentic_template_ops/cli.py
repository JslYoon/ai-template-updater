from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import click
from tabulate import tabulate

from agentic_template_ops.agents.drift_scanner import DriftScanner
from agentic_template_ops.config import AppConfig, EnvConfig
from agentic_template_ops.gws_client import GwsCliClient
from agentic_template_ops.report import generate_version_status

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s | %(message)s",
)

DEFAULT_SPREADSHEET_ID = "11S2h__-nN4fr25DJcfDbwQWXztLG5lrywthYPoSDPyQ"


def _run_claude_agent(
    agent_name: str,
    prompt: str,
    extra_dirs: list[str] | None = None,
    dry_run: bool = False,
) -> bool:
    if dry_run:
        click.echo(f"  [DRY RUN] would spawn @{agent_name}")
        return True
    click.echo(f"\n>>> Spawning @{agent_name}...")
    cmd = ["claude", "-p", "--agent", agent_name]
    for d in (extra_dirs or []):
        cmd.extend(["--add-dir", d])
    cmd.append(prompt)
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        click.echo(f"  @{agent_name} failed (exit {result.returncode})")
        return False
    click.echo(f"  @{agent_name} done.")
    return True


# ---------------------------------------------------------------------------
# Phase implementation helpers (called by both individual commands and `run`)
# ---------------------------------------------------------------------------

def _investigate_impl(spreadsheet_id: str, creds: str, dry_run: bool) -> list:
    config = AppConfig(
        google_credentials_path=creds,
        spreadsheet_id=spreadsheet_id,
        dry_run=dry_run,
    )

    click.echo("Using gws CLI for sheets...")
    reader = GwsCliClient(config.spreadsheet_id)

    click.echo("Reading template candidates from Google Sheet...")
    candidates = reader.read_template_candidates(config.input_sheet_name)

    if not candidates:
        click.echo("No template candidates found. Check sheet data.")
        sys.exit(1)

    click.echo(
        f"Found {len(candidates)} templates. Running sub-agent checks..."
    )

    orchestrator = DriftScanner(config)
    results = orchestrator.investigate(candidates)

    table_data = [
        [
            r.template_name,
            r.component,
            r.server_type,
            r.current_version[:20],
            r.latest_version[:20],
            "YES" if r.update_available else "no",
            r.notes[:40],
        ]
        for r in results
    ]
    click.echo(
        "\n"
        + tabulate(
            table_data,
            headers=[
                "Template",
                "Component",
                "Type",
                "Current",
                "Latest",
                "Update?",
                "Notes",
            ],
            tablefmt="grid",
        )
    )

    updates_found = sum(1 for r in results if r.update_available)
    click.echo(f"\n{updates_found} updates available out of {len(results)} checks.")

    status_path = Path(__file__).resolve().parent.parent / "VERSION_STATUS.md"
    generate_version_status(
        results,
        status_path,
        spreadsheet_id=None if dry_run else config.spreadsheet_id,
    )
    if dry_run:
        click.echo(f"[DRY RUN] Version status written to {status_path}; skipped Google Sheet.")
    else:
        click.echo(
            f"Phase 1 complete. Version status + audit log written to "
            f"{status_path} + Google Sheet."
        )

    return results


def _setup_impl(
    spreadsheet_id: str, env_file: str, all_updates: bool, dry_run: bool
) -> list[dict]:
    config = AppConfig(spreadsheet_id=spreadsheet_id, dry_run=dry_run)
    env = EnvConfig.from_env_file(env_file)

    click.echo("Reading updates from Google Sheets...")
    reader = GwsCliClient(config.spreadsheet_id)
    approved = reader.read_pending_updates(config.status_sheet_name)

    if not approved:
        click.echo("No updates in the latest audit run.")
        return []

    click.echo(f"Found {len(approved)} updates.\n")
    click.echo(f"Developer-images: {env.developer_images_path}")
    click.echo(f"AI Lab Template:  {env.ai_lab_template_path}")
    click.echo(f"Personal quay:    quay.io/{env.quay_personal_ns}")
    click.echo(f"Fork owner:       {env.fork_owner}")

    for task in approved:
        click.echo(
            f"\n  {task['template']} / {task['server_type']}"
            f" — {task['current_version']} -> {task['latest_version']}"
        )

    click.echo("\n--- Phase 3: Setup ---")

    repo_dirs = [str(env.developer_images_path), str(env.ai_lab_template_path)]

    # Deduplicate: one builder agent per unique server_type:version
    unique_servers: dict[str, dict] = {}
    for t in approved:
        if t["component"] == "server":
            key = f"{t['server_type']}:{t['latest_version']}"
            if key not in unique_servers:
                unique_servers[key] = t

    for task in unique_servers.values():
        click.echo(f"\n  Building {task['server_type']} {task['current_version']} -> {task['latest_version']}")
        _run_claude_agent(
            "impl-builder",
            f"Phase 3 setup. Read .env file at {env_file} for config.\n"
            f"Build ONE server image and push to personal quay (quay.io/{env.quay_personal_ns}):\n"
            f"  server_type: {task['server_type']}\n"
            f"  current: {task['current_version']}\n"
            f"  latest: {task['latest_version']}\n"
            f"Developer-images repo: {env.developer_images_path}\n"
            f"Copy latest version dir, update version pins, podman build, push to personal quay.",
            extra_dirs=repo_dirs,
            dry_run=dry_run,
        )

    # One builder agent per unique model update
    unique_models: dict[str, dict] = {}
    for t in approved:
        if t["component"] == "model":
            key = f"{t['server_type']}:{t['latest_version']}"
            if key not in unique_models:
                unique_models[key] = t

    for task in unique_models.values():
        click.echo(f"\n  Building model {task['server_type']} {task['current_version']} -> {task['latest_version']}")
        _run_claude_agent(
            "impl-builder",
            f"Phase 3 setup. Read .env file at {env_file} for config.\n"
            f"Build ONE model image and push to personal quay (quay.io/{env.quay_personal_ns}):\n"
            f"  model: {task['server_type']}\n"
            f"  current: {task['current_version']}\n"
            f"  latest: {task['latest_version']}\n"
            f"  notes: {task.get('notes', '')}\n"
            f"Developer-images repo: {env.developer_images_path}\n"
            f"Update model Containerfile with new HuggingFace download URL, podman build, push.",
            extra_dirs=repo_dirs,
            dry_run=dry_run,
        )

    # One template agent (env files are shared, can't parallelize)
    build_summary = json.dumps([
        {"server_type": t["server_type"], "current": t["current_version"], "latest": t["latest_version"]}
        for t in approved if t["component"] == "server"
    ])
    model_summary = json.dumps([
        {"model": t["server_type"], "current": t["current_version"], "latest": t["latest_version"]}
        for t in approved if t["component"] == "model"
    ])

    _run_claude_agent(
        "impl-template",
        f"Phase 3 setup. Read .env file at {env_file} for config.\n"
        f"Pre-verification workflow:\n"
        f"1. cd {env.ai_lab_template_path}\n"
        f"2. Branch from main\n"
        f"3. Update scripts/envs/* to use personal quay tags (quay.io/{env.quay_personal_ns})\n"
        f"4. Server updates: {build_summary}\n"
        f"5. Model updates: {model_summary}\n"
        f"6. Run ./scripts/import-ai-lab-samples && ./scripts/generate-no-app-template\n"
        f"7. Commit and push to fork ({env.fork_owner})\n"
        f"8. Output the RHDH registration URL",
        extra_dirs=repo_dirs,
        dry_run=dry_run,
    )

    if dry_run:
        click.echo("\n[DRY RUN] Agent prompts shown above.")
    else:
        click.echo("\nPhase 3 complete. Images built and templates staged.")

    return approved


def _promote_impl(
    spreadsheet_id: str, env_file: str, all_updates: bool, dry_run: bool
) -> None:
    config = AppConfig(spreadsheet_id=spreadsheet_id, dry_run=dry_run)
    env = EnvConfig.from_env_file(env_file)

    click.echo("Reading built updates from Google Sheets...")
    reader = GwsCliClient(config.spreadsheet_id)
    approved = reader.read_built_updates(config.status_sheet_name)

    if not approved:
        click.echo("No built images in the latest audit run. Run setup first.")
        return

    click.echo(f"Found {len(approved)} built updates.\n")
    click.echo(f"Official quay:    quay.io/{env.quay_official_ns}")
    click.echo(f"Developer-images: {env.developer_images_path}")
    click.echo(f"AI Lab Template:  {env.ai_lab_template_path}")

    for task in approved:
        click.echo(
            f"\n  {task['template']} / {task['server_type']}"
            f" — {task['current_version']} -> {task['latest_version']}"
        )

    click.echo("\n--- Phase 5: Promote ---")

    repo_dirs = [str(env.developer_images_path), str(env.ai_lab_template_path)]

    # One promote agent per unique server_type:version
    unique_servers: dict[str, dict] = {}
    for t in approved:
        if t["component"] == "server":
            key = f"{t['server_type']}:{t['latest_version']}"
            if key not in unique_servers:
                unique_servers[key] = t

    for task in unique_servers.values():
        click.echo(f"\n  Promoting {task['server_type']} {task['latest_version']}")
        _run_claude_agent(
            "impl-builder",
            f"Phase 5 promote. Read .env file at {env_file} for config.\n"
            f"Retag ONE image from personal quay (quay.io/{env.quay_personal_ns}) "
            f"to official quay (quay.io/{env.quay_official_ns}) and push.\n"
            f"  server_type: {task['server_type']}\n"
            f"  version: {task['latest_version']}",
            extra_dirs=repo_dirs,
            dry_run=dry_run,
        )

    # One promote agent per unique model update
    unique_models: dict[str, dict] = {}
    for t in approved:
        if t["component"] == "model":
            key = f"{t['server_type']}:{t['latest_version']}"
            if key not in unique_models:
                unique_models[key] = t

    for task in unique_models.values():
        click.echo(f"\n  Promoting model {task['server_type']} {task['latest_version']}")
        _run_claude_agent(
            "impl-builder",
            f"Phase 5 promote. Read .env file at {env_file} for config.\n"
            f"Retag ONE model image from personal quay (quay.io/{env.quay_personal_ns}) "
            f"to official quay (quay.io/{env.quay_official_ns}) and push.\n"
            f"  model: {task['server_type']}\n"
            f"  version: {task['latest_version']}",
            extra_dirs=repo_dirs,
            dry_run=dry_run,
        )

    # One devimages agent per unique server (commit version dir + PR)
    for task in unique_servers.values():
        _run_claude_agent(
            "impl-devimages",
            f"Phase 5 promote. Read .env file at {env_file} for config.\n"
            f"Commit version directory for ONE server in {env.developer_images_path} "
            f"and create PR to upstream redhat-ai-dev/developer-images.\n"
            f"Fork owner: {env.fork_owner}\n"
            f"  server_type: {task['server_type']}\n"
            f"  version: {task['latest_version']}",
            extra_dirs=repo_dirs,
            dry_run=dry_run,
        )

    # One template agent (env files are shared)
    build_summary = json.dumps([
        {"server_type": t["server_type"], "current": t["current_version"], "latest": t["latest_version"]}
        for t in approved if t["component"] == "server"
    ])
    model_summary = json.dumps([
        {"model": t["server_type"], "current": t["current_version"], "latest": t["latest_version"]}
        for t in approved if t["component"] == "model"
    ])

    _run_claude_agent(
        "impl-template",
        f"Phase 5 promote. Read .env file at {env_file} for config.\n"
        f"Post-verification workflow:\n"
        f"1. Update scripts/envs/* to use official quay tags (quay.io/{env.quay_official_ns})\n"
        f"2. Server updates: {build_summary}\n"
        f"3. Model updates: {model_summary}\n"
        f"4. Re-run generation scripts\n"
        f"5. Commit, push, create PR to upstream redhat-ai-dev/ai-lab-template\n"
        f"Fork owner: {env.fork_owner}",
        extra_dirs=repo_dirs,
        dry_run=dry_run,
    )

    click.echo("\nPhase 5 complete." if not dry_run else "\n[DRY RUN] Agent prompts shown above.")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Agentic AI Software Template & Model Update Tool"""


@cli.command()
@click.option("--creds", default="service_account.json", help="Path to GWS Service Account JSON")
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
@click.option("--dry-run", is_flag=True, help="Skip writing to Google Sheets")
def investigate(creds, spreadsheet_id, dry_run):
    """Phase 1: Check models and servers for updates, write audit log."""
    _investigate_impl(spreadsheet_id, creds, dry_run)


@cli.command()
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
@click.option("--env-file", default=".env", help="Path to .env config file")
@click.option("--all", "all_updates", is_flag=True, help="Deprecated no-op; all detected updates are always taken (no approval gate)")
@click.option("--dry-run", is_flag=True, help="Show what would happen")
def setup(spreadsheet_id, env_file, all_updates, dry_run):
    """Phase 3: Build images, push to personal quay, set up templates on cluster."""
    _setup_impl(spreadsheet_id, env_file, all_updates, dry_run)


@cli.command()
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
@click.option("--env-file", default=".env", help="Path to .env config file")
@click.option("--all", "all_updates", is_flag=True, help="Deprecated no-op; all detected updates are always taken (no approval gate)")
@click.option("--dry-run", is_flag=True, help="Show what would happen")
def promote(spreadsheet_id, env_file, all_updates, dry_run):
    """Phase 5: Promote images to official quay, create PRs, update sheets."""
    _promote_impl(spreadsheet_id, env_file, all_updates, dry_run)


@cli.command(name="record-builds")
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
@click.option("--results", "results_json", default="", help="JSON array of build results; reads stdin if omitted")
def record_builds(spreadsheet_id, results_json):
    """Record build outcomes to the Sheet's Audit Log (newest run).

    Input is a JSON array of {component, server_type, version, image_tag, success}.
    Successful builds flip `built`=TRUE and store the exact `image_tag` on every
    matching row, making the Sheet the source of truth for staging/promotion.
    """
    raw = results_json.strip() or sys.stdin.read().strip()
    if not raw:
        click.echo("No build results provided (use --results or stdin).")
        sys.exit(1)
    try:
        results = json.loads(raw)
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON: {e}")
        sys.exit(1)
    if not isinstance(results, list):
        click.echo("Build results must be a JSON array.")
        sys.exit(1)

    reader = GwsCliClient(spreadsheet_id)
    n = reader.record_build_results(results)
    click.echo(f"Recorded {n} built row(s) in the Audit Log.")


@cli.command(name="list-built")
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
def list_built(spreadsheet_id):
    """Print the newest run's built updates as JSON (source of truth for stage/promote)."""
    reader = GwsCliClient(spreadsheet_id)
    built = reader.read_built_updates()
    click.echo(json.dumps(built))


@cli.command()
@click.option("--env-file", default=".env", help="Path to .env config file")
def configure(env_file):
    """Generate .claude/settings.local.json from .env (read permissions + additional dirs)."""
    env = EnvConfig.from_env_file(env_file)

    settings_dir = Path(".claude")
    settings_dir.mkdir(exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    dev_path = str(env.developer_images_path.resolve())
    tpl_path = str(env.ai_lab_template_path.resolve())
    rolling_demo_path = str(env.rolling_demo_gitops_path.resolve()) if env.rolling_demo_gitops_path else None

    existing = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())

    permissions = existing.get("permissions", {})
    allow_rules = permissions.get("allow", [])

    read_dev = f"Read({dev_path}/**)"
    read_tpl = f"Read({tpl_path}/**)"
    for rule in [read_dev, read_tpl]:
        if rule not in allow_rules:
            allow_rules.append(rule)

    additional_dirs = [dev_path, tpl_path]

    if rolling_demo_path:
        read_rolling = f"Read({rolling_demo_path}/**)"
        if read_rolling not in allow_rules:
            allow_rules.append(read_rolling)
        additional_dirs.append(rolling_demo_path)

    permissions["allow"] = allow_rules
    permissions["additionalDirectories"] = additional_dirs
    existing["permissions"] = permissions

    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    click.echo(f"Wrote {settings_path}")
    click.echo(f"  Read access: {dev_path}")
    click.echo(f"  Read access: {tpl_path}")
    if rolling_demo_path:
        click.echo(f"  Read access: {rolling_demo_path}")
    click.echo(f"  Additional dirs: {len(additional_dirs)} added")


@cli.command()
@click.option("--creds", default="service_account.json", help="Path to GWS Service Account JSON")
@click.option("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID, help="Google Sheet spreadsheet ID")
@click.option("--env-file", default=".env", help="Path to .env config file")
@click.option("--dry-run", is_flag=True, help="Show what would happen")
def run(creds, spreadsheet_id, env_file, dry_run):
    """Full workflow: investigate, build, verify, promote (Phases 1-5)."""
    click.echo("=" * 60)
    click.echo("PHASE 1: Investigating updates")
    click.echo("=" * 60)
    results = _investigate_impl(spreadsheet_id, creds, dry_run)

    updates_found = sum(1 for r in results if r.update_available)
    if updates_found == 0:
        click.echo("\nNo updates found. Nothing to do.")
        return

    click.echo("\n" + "=" * 60)
    click.echo("PHASE 3: Setting up builds and staging")
    click.echo("=" * 60)
    approved = _setup_impl(spreadsheet_id, env_file, True, dry_run)

    if not approved:
        click.echo("\nNo approved updates to process.")
        return

    click.echo("\n" + "=" * 60)
    click.echo("PHASE 4: Human Verification")
    click.echo("=" * 60)
    click.echo("Test the staged templates on your ROSA cluster.")
    click.echo("Verify each template works with the new images.\n")

    if dry_run:
        click.echo("[DRY RUN] Skipping verification prompt.")
    else:
        click.confirm(
            "Templates verified on cluster. Proceed to Phase 5 (promote)?",
            abort=True,
        )

    click.echo("\n" + "=" * 60)
    click.echo("PHASE 5: Promoting to production")
    click.echo("=" * 60)
    _promote_impl(spreadsheet_id, env_file, True, dry_run)

    click.echo("\n" + "=" * 60)
    click.echo("ALL PHASES COMPLETE")
    click.echo("=" * 60)


if __name__ == "__main__":
    cli()
