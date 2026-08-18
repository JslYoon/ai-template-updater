from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agentic_template_ops.config import (
    AUDIT_SECTION_TITLE,
    BUILT_FALSE,
    RUN_MARKER_PREFIX,
    SHEET_HIDDEN_SERVERS,
    AuditResult,
)

log = logging.getLogger("report")

MAX_RUNS = 20  # cap run history to bound sheet size


def generate_version_status(
    results: list[AuditResult],
    output_path: Path,
    spreadsheet_id: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    servers = [r for r in results if r.component == "server"]
    models = [r for r in results if r.component == "model"]

    seen_servers: dict[str, list[AuditResult]] = {}
    for r in servers:
        seen_servers.setdefault(r.server_type, []).append(r)

    seen_models: dict[str, AuditResult] = {}
    model_templates: dict[str, list[str]] = {}
    for r in models:
        model_id = _extract_model_id(r.source_url)
        if model_id not in seen_models:
            seen_models[model_id] = r
            model_templates[model_id] = []
        model_templates[model_id].append(r.template_name)

    # Deduplicate servers: one row per server type, pick entry with known version
    deduped_servers: dict[str, AuditResult] = {}
    for r in servers:
        existing = deduped_servers.get(r.server_type)
        if not existing or (r.current_version != "not tracked" and existing.current_version == "not tracked"):
            deduped_servers[r.server_type] = r

    # Deduplicate models: one row per model ID
    deduped_models: dict[str, AuditResult] = {}
    model_templates: dict[str, list[str]] = {}
    for r in models:
        model_id = _extract_model_id(r.source_url)
        if model_id not in deduped_models:
            deduped_models[model_id] = r
            model_templates[model_id] = []
        model_templates[model_id].append(r.template_name)

    lines = [
        "# Version Status",
        "",
        "> Current deployed versions across RHDH AI templates.",
        "> Updated after each successful apply-updates cycle.",
        f"> Last checked: {now}",
        "",
        "## Model Servers",
        "",
        "| Server | Image | Current | Upstream | Latest (quay) | Update? | Source |",
        "|--------|-------|---------|----------|---------------|---------|--------|",
    ]

    for server_type in sorted(deduped_servers):
        r = deduped_servers[server_type]
        source = r.source_url if r.source_url else "—"
        upstream = r.upstream_version if r.upstream_version else "—"
        quay = r.quay_version if r.quay_version else "—"
        update = "YES" if r.update_available else "current"
        lines.append(
            f"| {server_type} | `quay.io/redhat-ai-dev/{server_type}` "
            f"| {r.current_version} | {upstream} | {quay} | {update} | {source} |"
        )

    lines += [
        "",
        "## Models (HuggingFace)",
        "",
        "| Model | Used By | Pipeline | Current | Latest | Update? | Current Date | Latest Date |",
        "|-------|---------|----------|---------|--------|---------|--------------|-------------|",
    ]

    for model_id in sorted(deduped_models):
        r = deduped_models[model_id]
        templates = ", ".join(sorted(set(model_templates[model_id])))
        pipeline = _extract_field(r.notes, "pipeline")
        current = r.current_version
        if current == "UNKNOWN" or current == "unknown":
            current = "N/A"
        latest = r.latest_version
        if latest == "UNKNOWN" or latest == "unknown":
            latest = "N/A"
        update = "YES" if r.update_available else "current"
        cur_date = _short_date(_extract_field(r.notes, "modified"))
        lat_date = _short_date(_extract_field(r.notes, "latest_modified"))
        lines.append(
            f"| `{model_id}` | {templates} | {pipeline} | {current} | {latest} | {update} | {cur_date} | {lat_date} |"
        )

    lines.append("")

    output_path.write_text("\n".join(lines))

    if spreadsheet_id:
        _write_status_sheet(
            spreadsheet_id, now, seen_servers, seen_models, model_templates, results
        )


def _write_status_sheet(
    spreadsheet_id: str,
    timestamp: str,
    seen_servers: dict[str, list[AuditResult]],
    seen_models: dict[str, AuditResult],
    model_templates: dict[str, list[str]],
    results: list[AuditResult],
) -> None:
    sheet_name = "Version Status"

    server_rows = []
    server_update_flags = []
    for server_type in sorted(seen_servers):
        if server_type in SHEET_HIDDEN_SERVERS:
            continue
        group = seen_servers[server_type]
        by_version: dict[str, list[AuditResult]] = {}
        for r in group:
            by_version.setdefault(r.current_version, []).append(r)

        for current_ver, entries in sorted(by_version.items()):
            rep = max(entries, key=lambda x: x.update_available)
            templates = ", ".join(sorted({e.template_name for e in entries}))
            upstream = rep.upstream_version or _extract_upstream(rep.notes)
            quay = rep.quay_version or rep.latest_version
            compat = _extract_compat(rep.notes) if rep.update_available else ""
            is_update = rep.update_available
            status = "YES" if is_update else "current"
            server_rows.append([
                server_type, templates, current_ver,
                quay, upstream, status, compat,
            ])
            server_update_flags.append(is_update)

    model_rows = []
    model_update_flags = []
    for model_id in sorted(seen_models):
        r = seen_models[model_id]
        templates = ", ".join(sorted(set(model_templates[model_id])))
        pipeline = _extract_field(r.notes, "pipeline")
        current = r.current_version
        if current == "UNKNOWN" or current == "unknown":
            current = "N/A"
        latest = r.latest_version
        if latest == "UNKNOWN" or latest == "unknown":
            latest = "N/A"
        is_update = r.update_available
        status = "YES" if is_update else "current"
        cur_date = _short_date(_extract_field(r.notes, "modified"))
        lat_date = _short_date(_extract_field(r.notes, "latest_modified"))
        model_rows.append([model_id, templates, pipeline, current, latest, status, cur_date, lat_date])
        model_update_flags.append(is_update)

    # -- Audit Log: this run's block = 1 summary row + 1 item row per update found --
    audit_items = [r for r in results if r.update_available]
    new_run_block: list[list] = [[
        f"{RUN_MARKER_PREFIX}{timestamp}",
        f"{len(audit_items)} updates",
        f"{len(results)} checks",
    ]]
    for r in audit_items:
        # Columns must match config.AUDIT_ITEM_FIELDS. built/image_tag start
        # empty; the build phase flips them via gws_client.record_build_results.
        new_run_block.append([
            r.template_name, r.component, r.server_type,
            r.current_version, r.latest_version, r.source_url, r.notes,
            BUILT_FALSE, "",
        ])

    prior_runs = _read_existing_runs(spreadsheet_id, sheet_name)
    run_blocks = [new_run_block] + prior_runs
    if len(run_blocks) > MAX_RUNS:
        log.info(
            "Audit Log capped at %d runs; dropping %d oldest",
            MAX_RUNS, len(run_blocks) - MAX_RUNS,
        )
        run_blocks = run_blocks[:MAX_RUNS]

    # Row layout
    # 0: title
    # 1: empty
    # 2: "Model Servers" section header (merged)
    # 3: server column headers
    # 4..: server data rows
    # empty
    # "Models (HuggingFace)" section header (merged)
    # model column headers
    # model data rows
    # empty
    # "Audit Log" section header (merged)
    # run blocks: each = 1 summary row + N item rows (newest first)

    n_srv = len(server_rows)
    srv_header_row = 3
    srv_data_start = 4
    srv_data_end = srv_data_start + n_srv
    model_section_row = srv_data_end + 1
    model_header_row = model_section_row + 1
    model_data_start = model_header_row + 1
    model_data_end = model_data_start + len(model_rows)
    audit_section_row = model_data_end + 1
    audit_data_start = audit_section_row + 1

    all_rows: list[list] = [
        [f"Version Status — Last updated: {timestamp}"],
        [],
        ["Model Servers"],
        ["Server", "Templates", "Current", "Latest (quay)", "Upstream", "Update?", "Compatibility"],
    ]
    all_rows.extend(server_rows)
    all_rows.append([])
    all_rows.append(["Models (HuggingFace)"])
    all_rows.append(["Model", "Used By", "Pipeline", "Current", "Latest", "Update?", "Current Date", "Latest Date"])
    all_rows.extend(model_rows)
    all_rows.append([])
    all_rows.append([AUDIT_SECTION_TITLE])
    for block in run_blocks:
        all_rows.extend(block)

    # Locate run-summary rows and per-run item ranges for formatting / grouping
    run_summary_rows: list[int] = []
    item_group_ranges: list[tuple[int, int]] = []
    cursor = audit_data_start
    for block in run_blocks:
        run_summary_rows.append(cursor)
        n_items = len(block) - 1
        if n_items > 0:
            item_group_ranges.append((cursor + 1, cursor + 1 + n_items))
        cursor += len(block)
    n_audit_rows = cursor - audit_data_start

    try:
        # Clear old data first
        _gws_run([
            "sheets", "spreadsheets", "values", "clear",
            "--params", json.dumps({
                "spreadsheetId": spreadsheet_id,
                "range": sheet_name,
            }),
            "--format", "json",
        ])

        # Write data
        _gws_run([
            "sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({
                "spreadsheetId": spreadsheet_id,
                "range": f"{sheet_name}!A1",
                "valueInputOption": "RAW",
            }),
            "--json", json.dumps({"values": all_rows}),
            "--format", "json",
        ])

        # Get sheet ID
        sheet_id = _get_sheet_id(spreadsheet_id, sheet_name)
        if sheet_id is None:
            log.warning("Could not find sheet ID for formatting")
            return

        # Build formatting requests
        requests_list = _build_format_requests(
            sheet_id, n_srv, len(model_rows),
            srv_header_row, srv_data_start,
            model_section_row, model_header_row, model_data_start,
            audit_section_row, audit_data_start, n_audit_rows, run_summary_rows,
            server_update_flags, model_update_flags,
        )

        _gws_run([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
            "--json", json.dumps({"requests": requests_list}),
            "--format", "json",
        ])

        # Collapsible row groups for each run's item rows (best-effort)
        _apply_run_grouping(spreadsheet_id, sheet_id, item_group_ranges)

        log.info("Updated '%s' sheet tab with formatting", sheet_name)
    except Exception as e:
        log.warning("Failed to write Version Status sheet: %s", e)


def _read_existing_runs(spreadsheet_id: str, sheet_name: str) -> list[list[list]]:
    """Read prior Audit Log run blocks (newest-first) so history survives rewrite."""
    try:
        output = _gws_run([
            "sheets", "spreadsheets", "values", "get",
            "--params", json.dumps({
                "spreadsheetId": spreadsheet_id,
                "range": sheet_name,
            }),
            "--format", "json",
        ])
        values = json.loads(output).get("values", [])
    except (RuntimeError, json.JSONDecodeError):
        return []

    start = None
    for i, row in enumerate(values):
        if row and row[0] == AUDIT_SECTION_TITLE:
            start = i + 1
            break
    if start is None:
        return []

    blocks: list[list[list]] = []
    current: list[list] | None = None
    for row in values[start:]:
        first = row[0] if row else ""
        if first.startswith(RUN_MARKER_PREFIX):
            if current is not None:
                blocks.append(current)
            current = [row]
        elif current is not None and first:
            current.append(row)
    if current is not None:
        blocks.append(current)
    return blocks


def _apply_run_grouping(
    spreadsheet_id: str, sheet_id: int, ranges: list[tuple[int, int]]
) -> None:
    """Create collapsible row groups for each run's item rows.

    Deletes any pre-existing row groups on the sheet first so groups don't
    accumulate across runs.
    """
    if not ranges:
        return

    requests: list[dict] = []

    # Delete existing row groups (metadata survives a values clear)
    try:
        output = _gws_run([
            "sheets", "spreadsheets", "get",
            "--params", json.dumps({
                "spreadsheetId": spreadsheet_id,
                "fields": "sheets(properties.sheetId,rowGroups.range)",
            }),
            "--format", "json",
        ])
        for s in json.loads(output).get("sheets", []):
            if s.get("properties", {}).get("sheetId") != sheet_id:
                continue
            for grp in s.get("rowGroups", []):
                rng = grp.get("range", {})
                requests.append({"deleteDimensionGroup": {"range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": rng.get("startIndex"),
                    "endIndex": rng.get("endIndex"),
                }}})
    except (RuntimeError, json.JSONDecodeError):
        pass

    for start, end in ranges:
        requests.append({"addDimensionGroup": {"range": {
            "sheetId": sheet_id,
            "dimension": "ROWS",
            "startIndex": start,
            "endIndex": end,
        }}})

    try:
        _gws_run([
            "sheets", "spreadsheets", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
            "--json", json.dumps({"requests": requests}),
            "--format", "json",
        ])
    except RuntimeError as e:
        log.warning("Run grouping skipped: %s", e)


def _get_sheet_id(spreadsheet_id: str, sheet_name: str) -> int | None:
    output = _gws_run([
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({
            "spreadsheetId": spreadsheet_id,
            "fields": "sheets.properties",
        }),
        "--format", "json",
    ])
    data = json.loads(output)
    for s in data.get("sheets", []):
        if s["properties"]["title"] == sheet_name:
            return s["properties"]["sheetId"]
    return None


def _rgb(r: int, g: int, b: int) -> dict:
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _cell_format(
    sheet_id: int,
    row_start: int, row_end: int,
    col_start: int, col_end: int,
    fmt: dict,
    fields: str,
) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_start,
                "endRowIndex": row_end,
                "startColumnIndex": col_start,
                "endColumnIndex": col_end,
            },
            "cell": {"userEnteredFormat": fmt},
            "fields": f"userEnteredFormat({fields})",
        }
    }


def _merge(sheet_id: int, row: int, col_end: int) -> dict:
    return {
        "mergeCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": 0,
                "endColumnIndex": col_end,
            },
            "mergeType": "MERGE_ALL",
        }
    }


def _build_format_requests(
    sheet_id: int,
    n_srv: int, n_model: int,
    srv_header_row: int, srv_data_start: int,
    model_section_row: int, model_header_row: int, model_data_start: int,
    audit_section_row: int, audit_data_start: int, n_audit_rows: int,
    run_summary_rows: list[int],
    server_update_flags: list[bool],
    model_update_flags: list[bool] | None = None,
) -> list[dict]:
    max_col = 8
    dark_blue = _rgb(26, 35, 126)
    mid_blue = _rgb(66, 133, 244)
    light_blue = _rgb(232, 240, 254)
    white = _rgb(255, 255, 255)
    light_gray = _rgb(245, 245, 245)
    green_bg = _rgb(212, 237, 218)
    red_bg = _rgb(248, 215, 218)
    dark_text = _rgb(33, 33, 33)

    reqs: list[dict] = []

    # -- Title row (row 0): big, bold, dark blue background, white text --
    reqs.append(_merge(sheet_id, 0, max_col))
    reqs.append(_cell_format(sheet_id, 0, 1, 0, max_col, {
        "textFormat": {"bold": True, "fontSize": 14, "foregroundColorStyle": {"rgbColor": white}},
        "backgroundColor": dark_blue,
        "verticalAlignment": "MIDDLE",
        "padding": {"top": 8, "bottom": 8},
    }, "textFormat,backgroundColor,verticalAlignment,padding"))

    # -- Section headers: "Model Servers" (row 2), "Models", "Audit Log" --
    for sec_row in [2, model_section_row, audit_section_row]:
        reqs.append(_merge(sheet_id, sec_row, max_col))
        reqs.append(_cell_format(sheet_id, sec_row, sec_row + 1, 0, max_col, {
            "textFormat": {"bold": True, "fontSize": 12, "foregroundColorStyle": {"rgbColor": white}},
            "backgroundColor": mid_blue,
            "verticalAlignment": "MIDDLE",
            "padding": {"top": 4, "bottom": 4},
        }, "textFormat,backgroundColor,verticalAlignment,padding"))

    # -- Column headers: server (row 3), model (model_header_row) --
    for hdr_row, cols in [
        (srv_header_row, max_col),
        (model_header_row, max_col),
    ]:
        reqs.append(_cell_format(sheet_id, hdr_row, hdr_row + 1, 0, cols, {
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColorStyle": {"rgbColor": dark_text}},
            "backgroundColor": light_blue,
        }, "textFormat,backgroundColor"))

    # -- Data rows: alternating white/light gray, 10pt font --
    for data_start, data_count, cols in [
        (srv_data_start, n_srv, max_col),
        (model_data_start, n_model, max_col),
    ]:
        for i in range(data_count):
            row = data_start + i
            bg = light_gray if i % 2 == 1 else white
            reqs.append(_cell_format(sheet_id, row, row + 1, 0, cols, {
                "textFormat": {"fontSize": 10},
                "backgroundColor": bg,
            }, "textFormat,backgroundColor"))

    # -- Audit Log rows: base 10pt white, then bold light-blue run-summary rows --
    audit_cols = 9  # includes built + image_tag (config.AUDIT_ITEM_FIELDS)
    if n_audit_rows > 0:
        reqs.append(_cell_format(
            sheet_id, audit_data_start, audit_data_start + n_audit_rows,
            0, audit_cols, {
                "textFormat": {"fontSize": 10},
                "backgroundColor": white,
            }, "textFormat,backgroundColor"))
    for row in run_summary_rows:
        reqs.append(_cell_format(sheet_id, row, row + 1, 0, max_col, {
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColorStyle": {"rgbColor": dark_text}},
            "backgroundColor": light_blue,
        }, "textFormat,backgroundColor"))

    # -- Update status coloring: green for "current", red for "YES" (column F = index 5) --
    for i, is_update in enumerate(server_update_flags):
        row = srv_data_start + i
        bg = red_bg if is_update else green_bg
        reqs.append(_cell_format(sheet_id, row, row + 1, 5, 6, {
            "backgroundColor": bg,
            "textFormat": {"bold": is_update, "fontSize": 10},
        }, "backgroundColor,textFormat"))

    if model_update_flags:
        for i, is_update in enumerate(model_update_flags):
            row = model_data_start + i
            bg = red_bg if is_update else green_bg
            reqs.append(_cell_format(sheet_id, row, row + 1, 5, 6, {
                "backgroundColor": bg,
                "textFormat": {"bold": is_update, "fontSize": 10},
            }, "backgroundColor,textFormat"))

    # -- Column widths -- (9th = image_tag, used by the Audit Log section)
    col_widths = [130, 280, 100, 100, 100, 80, 110, 110, 340]
    for i, w in enumerate(col_widths):
        reqs.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": i,
                    "endIndex": i + 1,
                },
                "properties": {"pixelSize": w},
                "fields": "pixelSize",
            }
        })

    # -- Row height for title --
    reqs.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": 0,
                "endIndex": 1,
            },
            "properties": {"pixelSize": 40},
            "fields": "pixelSize",
        }
    })

    # -- Freeze header area --
    reqs.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    return reqs


def _gws_run(args: list[str]) -> str:
    result = subprocess.run(
        ["gws"] + args,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gws failed: {result.stderr}")
    return result.stdout


def _extract_upstream(notes: str) -> str:
    import re
    match = re.search(r"upstream (?:has )?(v?[\d.]+)", notes)
    return match.group(1) if match else "—"


def _extract_compat(notes: str) -> str:
    import re
    clean = re.sub(r"\[Build lag:.*?\]", "", notes).strip()
    clean = clean.rstrip(";").strip()
    return clean if clean else "—"


def _extract_model_id(source_url: str) -> str:
    if "huggingface.co/" in source_url:
        return source_url.split("huggingface.co/")[-1]
    return source_url or "unknown"


def _extract_field(notes: str, field: str) -> str:
    import re
    match = re.search(rf"{field}: ([^;]+)", notes)
    return match.group(1).strip() if match else ""


def _short_date(iso_str: str) -> str:
    if not iso_str or iso_str == "unknown":
        return "—"
    return iso_str[:10]
