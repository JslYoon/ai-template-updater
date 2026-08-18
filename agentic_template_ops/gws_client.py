from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from agentic_template_ops.config import (
    AUDIT_COL_BUILT,
    AUDIT_COL_IMAGE_TAG,
    AUDIT_SECTION_TITLE,
    BUILT_TRUE,
    RUN_MARKER_PREFIX,
    SERVER_NAME_MAP,
    TemplateCandidate,
)

log = logging.getLogger("gws_client")


def _parse_server_types(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",")]
    result = []
    for part in parts:
        canonical = SERVER_NAME_MAP.get(part)
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _cell(row: list, idx: int) -> str:
    """Safely read a trimmed cell; Sheets omits trailing empty cells."""
    return str(row[idx]).strip() if idx < len(row) else ""


def _newest_run_item_rows(values: list[list]) -> list[tuple[int, list]]:
    """Return (1-indexed sheet row number, cells) for the newest run's item rows.

    Locates the "Audit Log" section, then the first (newest) "▶ RUN " marker,
    and collects the item rows beneath it until the next run marker or the end.
    Returns [] if there is no audit section / no run yet.
    """
    start = None
    for i, row in enumerate(values):
        if row and row[0] == AUDIT_SECTION_TITLE:
            start = i
            break
    if start is None:
        return []

    rows: list[tuple[int, list]] = []
    in_run = False
    for i in range(start + 1, len(values)):
        row = values[i]
        first = row[0] if row else ""
        if first.startswith(RUN_MARKER_PREFIX):
            if in_run:
                break  # reached the next, older run — stop
            in_run = True
            continue
        if not in_run or not first:
            continue
        rows.append((i + 1, row))  # sheet rows are 1-indexed
    return rows


def _row_to_update(row: list) -> dict[str, Any]:
    return {
        "template": _cell(row, 0),
        "component": _cell(row, 1),
        "server_type": _cell(row, 2),
        "current_version": _cell(row, 3),
        "latest_version": _cell(row, 4),
        "source_url": _cell(row, 5),
        "notes": _cell(row, 6),
        "built": _cell(row, AUDIT_COL_BUILT).upper() == BUILT_TRUE,
        "image_tag": _cell(row, AUDIT_COL_IMAGE_TAG),
    }


def _compute_build_updates(
    values: list[list], results: list[dict]
) -> list[tuple[int, str]]:
    """Pure: figure out which newest-run rows to mark built + with which tag.

    Matches each successful build result to every item row sharing
    (component, server_type, latest_version) — one build marks all templates
    that use it. Returns [(sheet_row_number, image_tag), ...].
    """
    # Index successful results by (component, server_type, version)
    built: dict[tuple[str, str, str], str] = {}
    for r in results:
        if not r.get("success"):
            continue
        key = (
            str(r.get("component", "")).strip(),
            str(r.get("server_type", "")).strip(),
            str(r.get("version", "")).strip(),
        )
        tag = str(r.get("image_tag", "")).strip()
        if all(key) and tag:
            built[key] = tag

    updates: list[tuple[int, str]] = []
    for row_num, row in _newest_run_item_rows(values):
        key = (_cell(row, 1), _cell(row, 2), _cell(row, 4))
        tag = built.get(key)
        if tag:
            updates.append((row_num, tag))
    return updates


class GwsCliClient:
    """Sheets access via the `gws` CLI (uses existing OAuth, no service account)."""

    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id

    def _run_gws(self, args: list[str], input_json: dict | None = None) -> str:
        cmd = ["gws"] + args
        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": 30,
        }
        if input_json is not None:
            kwargs["input"] = json.dumps(input_json)
        result = subprocess.run(cmd, **kwargs)
        if result.returncode != 0:
            raise RuntimeError(f"gws failed: {result.stderr}")
        return result.stdout

    def read_template_candidates(
        self, sheet_name: str = "Template List"
    ) -> list[TemplateCandidate]:
        output = self._run_gws([
            "sheets", "spreadsheets", "get",
            "--params", json.dumps({
                "spreadsheetId": self.spreadsheet_id,
                "ranges": sheet_name,
                "fields": "sheets.data.rowData.values.formattedValue,"
                          "sheets.data.rowData.values.hyperlink",
            }),
            "--format", "json",
        ])

        data = json.loads(output)
        sheets = data.get("sheets", [])
        if not sheets:
            return []

        row_data_list = sheets[0].get("data", [{}])[0].get("rowData", [])
        if len(row_data_list) < 2:
            return []

        header_cells = row_data_list[0].get("values", [])
        headers = [c.get("formattedValue", "") for c in header_cells]

        col_idx = {}
        for i, h in enumerate(headers):
            col_idx[h] = i

        name_col = col_idx.get("Name (Link)")
        model_col = col_idx.get("Model (Link)")
        server_col = col_idx.get("Model Server (Link)")
        target_col = col_idx.get("Deployment Target")

        candidates = []
        for row_obj in row_data_list[1:]:
            cells = row_obj.get("values", [])

            def cell_val(idx):
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx].get("formattedValue", "").strip()

            def cell_link(idx):
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx].get("hyperlink", "")

            name = cell_val(name_col)
            model = cell_val(model_col)
            server_str = cell_val(server_col)
            target = cell_val(target_col)

            if not name or not model:
                continue

            server_types = _parse_server_types(server_str)
            if not server_types:
                log.warning(
                    "Unknown server '%s' for template '%s', skipping",
                    server_str,
                    name,
                )
                continue

            candidates.append(
                TemplateCandidate(
                    template_name=name,
                    server_types=server_types,
                    model_id=model,
                    deployment_target=target,
                    raw_server_string=server_str,
                    template_url=cell_link(name_col),
                    model_url=cell_link(model_col),
                    server_url=cell_link(server_col),
                )
            )
        return candidates

    def _read_values(self, sheet_name: str) -> list[list]:
        try:
            output = self._run_gws([
                "sheets", "spreadsheets", "values", "get",
                "--params", json.dumps({
                    "spreadsheetId": self.spreadsheet_id,
                    "range": sheet_name,
                }),
                "--format", "json",
            ])
        except RuntimeError:
            return []
        try:
            return json.loads(output).get("values", [])
        except json.JSONDecodeError:
            return []

    def read_pending_updates(
        self, sheet_name: str = "Version Status"
    ) -> list[dict[str, Any]]:
        """Return the newest Audit Log run's item rows (every detected update).

        The build phase reads this list to know what to build. Item columns are
        config.AUDIT_ITEM_FIELDS; `built` and `image_tag` are included so callers
        can see build state.
        """
        values = self._read_values(sheet_name)
        return [_row_to_update(row) for _, row in _newest_run_item_rows(values)]

    def read_built_updates(
        self, sheet_name: str = "Version Status"
    ) -> list[dict[str, Any]]:
        """Return only the newest run's rows whose image has been built + pushed.

        This is the source of truth for staging and promotion after the build
        phase: each returned row carries the exact `image_tag` to use.
        """
        return [u for u in self.read_pending_updates(sheet_name) if u["built"]]

    def record_build_results(
        self, results: list[dict], sheet_name: str = "Version Status"
    ) -> int:
        """Mark newest-run rows as built and record the exact pushed image tag.

        `results` items are {component, server_type, version, image_tag, success}.
        A single successful build marks every item row that shares
        (component, server_type, latest_version). Returns rows updated.
        """
        values = self._read_values(sheet_name)
        updates = _compute_build_updates(values, results)
        if not updates:
            return 0

        # Batch-update columns H:I (built, image_tag) for each matched row.
        col_built = chr(ord("A") + AUDIT_COL_BUILT)       # "H"
        col_tag = chr(ord("A") + AUDIT_COL_IMAGE_TAG)     # "I"
        data = [
            {
                "range": f"{sheet_name}!{col_built}{row_num}:{col_tag}{row_num}",
                "values": [[BUILT_TRUE, image_tag]],
            }
            for row_num, image_tag in updates
        ]
        self._run_gws([
            "sheets", "spreadsheets", "values", "batchUpdate",
            "--params", json.dumps({"spreadsheetId": self.spreadsheet_id}),
            "--json", json.dumps({"valueInputOption": "RAW", "data": data}),
            "--format", "json",
        ])
        log.info("Recorded %d built rows in %s", len(updates), sheet_name)
        return len(updates)
