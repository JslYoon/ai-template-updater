from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from agentic_template_ops.config import (
    SERVER_NAME_MAP,
    AuditResult,
    TemplateCandidate,
)

log = logging.getLogger("gws_client")


def _sanitize(value: str) -> str:
    if (
        isinstance(value, str)
        and value
        and value[0] in ("=", "+", "-", "@", "\t", "\r")
    ):
        return "'" + value
    return value


def _parse_server_types(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",")]
    result = []
    for part in parts:
        canonical = SERVER_NAME_MAP.get(part)
        if canonical and canonical not in result:
            result.append(canonical)
    return result


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

    def write_audit_results(
        self,
        results: list[AuditResult],
        sheet_name: str = "ai audit log",
    ) -> None:
        rows = []
        for r in results:
            rows.append([
                False,
                _sanitize(r.template_name),
                _sanitize(r.component),
                _sanitize(r.server_type),
                _sanitize(r.current_version),
                _sanitize(r.latest_version),
                "AWAITING_EXECUTION" if r.update_available else "CURRENT",
                _sanitize(r.notes),
                _sanitize(r.source_url),
                r.checked_at,
            ])

        if not rows:
            return

        # Clear existing data rows (keep header row 1)
        try:
            self._run_gws([
                "sheets", "spreadsheets", "values", "clear",
                "--params", json.dumps({
                    "spreadsheetId": self.spreadsheet_id,
                    "range": f"{sheet_name}!A2:J",
                }),
                "--format", "json",
            ])
        except RuntimeError:
            pass

        start_row = 2
        end_row = start_row + len(rows) - 1
        end_col = chr(ord("A") + len(rows[0]) - 1)
        cell_range = f"{sheet_name}!A{start_row}:{end_col}{end_row}"

        self._run_gws(
            [
                "sheets", "spreadsheets", "values", "update",
                "--params", json.dumps({
                    "spreadsheetId": self.spreadsheet_id,
                    "range": cell_range,
                    "valueInputOption": "USER_ENTERED",
                }),
                "--json", json.dumps({"values": rows}),
                "--format", "json",
            ]
        )
        log.info("Wrote %d rows to %s", len(rows), cell_range)

        # Add checkboxes to column A
        output = self._run_gws([
            "sheets", "spreadsheets", "get",
            "--params", json.dumps({
                "spreadsheetId": self.spreadsheet_id,
                "fields": "sheets.properties",
            }),
            "--format", "json",
        ])
        sheets_data = json.loads(output)
        sheet_id = None
        for s in sheets_data.get("sheets", []):
            if s["properties"]["title"] == sheet_name:
                sheet_id = s["properties"]["sheetId"]
                break

        if sheet_id is not None:
            self._run_gws(
                [
                    "sheets", "spreadsheets", "batchUpdate",
                    "--params", json.dumps({
                        "spreadsheetId": self.spreadsheet_id,
                    }),
                    "--json", json.dumps({"requests": [{
                        "setDataValidation": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row - 1,
                                "endRowIndex": end_row,
                                "startColumnIndex": 0,
                                "endColumnIndex": 1,
                            },
                            "rule": {
                                "condition": {"type": "BOOLEAN"},
                                "showCustomUi": True,
                            },
                        }
                    }]}),
                    "--format", "json",
                ]
            )
            log.info("Added checkboxes to A%d:A%d", start_row, end_row)

    def read_approved_rows(
        self, sheet_name: str = "ai audit log", all_updates: bool = False
    ) -> list[dict[str, Any]]:
        try:
            output = self._run_gws([
                "sheets", "+read",
                "--spreadsheet", self.spreadsheet_id,
                "--range", sheet_name,
                "--format", "json",
            ])
        except RuntimeError:
            return []

        data = json.loads(output)
        values = data.get("values", [])
        if len(values) < 2:
            return []

        headers = values[0]
        approved = []
        for idx, row in enumerate(values[1:], start=2):
            row_dict = {}
            for i, h in enumerate(headers):
                row_dict[h] = row[i] if i < len(row) else ""

            status = str(row_dict.get("Status", ""))
            if status != "AWAITING_EXECUTION":
                continue

            is_approved = str(row_dict.get("Approve Upgrade", "")).upper() == "TRUE"
            if not all_updates and not is_approved:
                continue

            approved.append({
                "row_index": idx,
                "template": str(row_dict.get("Template", "")),
                "component": str(row_dict.get("Component", "")),
                "server_type": str(row_dict.get("Server Type", "")),
                "current_version": str(row_dict.get("Current Version", "")),
                "latest_version": str(row_dict.get("Latest Version", "")),
                "notes": str(row_dict.get("Notes", "")),
                "source_url": str(row_dict.get("Source URL", "")),
            })
        return approved

    def mark_row_processed(self, row_index: int, pr_url: str) -> None:
        self._run_gws([
            "sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({
                "spreadsheetId": self.spreadsheet_id,
                "range": f"ai audit log!G{row_index}",
                "valueInputOption": "RAW",
            }),
            "--json", json.dumps({"values": [[f"PR_CREATED: {pr_url}"]]}),
            "--format", "json",
        ])
