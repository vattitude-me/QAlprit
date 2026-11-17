"""CSV-backed dynamic variable datasets for Workflows and Test Journeys."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from qalprit.env_loader import EnvContext
from qalprit.spec_loader import FlowSpec, StepItem, expand_steps

_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FULL_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_:.]*)\}$")
_SENSITIVE_COL_MARKERS = ("PASS", "SECRET", "TOKEN", "PIN", "KEY", "OTP", "AUTH")
_RESERVED_COLS = {"dataset", "name", "case", "expected", "selected", "enabled"}


def is_sensitive_column(col_name: str) -> bool:
    """Return True if a variable column name suggests sensitive/secret data."""
    upper = (col_name or "").upper()
    return any(marker in upper for marker in _SENSITIVE_COL_MARKERS)


def _safe_dataset_rel_path(target_kind: Literal["flow", "spec"], target_name: str) -> Path:
    parts = [
        re.sub(r"[^a-zA-Z0-9._-]+", "_", p.strip()).strip("._-") or "item"
        for p in (target_name or "").replace("\\", "/").split("/")
        if p.strip()
    ]
    if not parts:
        parts = ["default"]
    sub = "flows" if target_kind == "flow" else "specs"
    return Path("datasets") / sub / Path(*parts[:-1]) / f"{parts[-1]}.csv"


def _meta_path_for_csv(csv_path: Path) -> Path:
    return csv_path.with_suffix(".meta.json")


class DatasetRow(BaseModel):
    """Single parameterized dataset row (e.g. one row of a CSV)."""

    name: str
    selected: bool = True
    values: dict[str, str] = Field(default_factory=dict)
    expected: str = ""


class DatasetBundle(BaseModel):
    """Collection of dynamic variable datasets for a workflow or test journey."""

    target_kind: Literal["flow", "spec"]
    target_name: str
    variables: list[str] = Field(default_factory=list)
    secret_columns: list[str] = Field(default_factory=list)
    rows: list[DatasetRow] = Field(default_factory=list)
    source_path: str = ""


def _parse_bool_cell(raw: str | None, default: bool = True) -> bool:
    val = (raw or "").strip().lower()
    if not val:
        return default
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return True


def _clean_var_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (raw or "").strip()).strip("_")
    if not cleaned:
        return "var"
    if cleaned[0].isdigit():
        cleaned = f"v_{cleaned}"
    return cleaned


def parse_csv_datasets(
    csv_text: str,
    *,
    target_kind: Literal["flow", "spec"],
    target_name: str,
    existing_secret_cols: list[str] | None = None,
    allow_empty: bool = False,
) -> DatasetBundle:
    """Parse CSV text into a DatasetBundle, preserving column order and secret markers.

    `allow_empty` accepts a header with no data rows. The Workbench saves that shape when a
    user deletes every row but keeps the variables, so reading it back must not fail.
    """
    cleaned_text = (csv_text or "").lstrip("\ufeff")
    if not cleaned_text.strip():
        raise ValueError("CSV input is empty.")

    reader = csv.DictReader(io.StringIO(cleaned_text))
    if not reader.fieldnames:
        raise ValueError("CSV file must include a header row.")

    raw_fields = [f.strip() for f in reader.fieldnames if f and f.strip()]
    if not raw_fields:
        raise ValueError("CSV header row has no valid column names.")

    dataset_col: str | None = None
    expected_col: str | None = None
    selected_col: str | None = None
    var_col_map: list[tuple[str, str]] = []

    for col in raw_fields:
        low = col.lower()
        if dataset_col is None and low in {"dataset", "name", "case"}:
            dataset_col = col
        elif expected_col is None and low == "expected":
            expected_col = col
        elif selected_col is None and low in {"selected", "enabled"}:
            selected_col = col
        else:
            var_col_map.append((col, _clean_var_name(col)))

    variables: list[str] = []
    seen_vars: set[str] = set()
    for _, clean_v in var_col_map:
        if clean_v not in seen_vars:
            seen_vars.add(clean_v)
            variables.append(clean_v)

    secret_set = set(existing_secret_cols or [])
    for v in variables:
        if is_sensitive_column(v):
            secret_set.add(v)

    rows: list[DatasetRow] = []
    used_names: set[str] = set()
    for idx, raw_row in enumerate(reader, start=1):
        if raw_row is None:
            continue
        # Skip completely blank trailing lines
        if all((v is None or not str(v).strip()) for v in raw_row.values()):
            continue

        base_name = (raw_row.get(dataset_col) or "").strip() if dataset_col else ""
        if not base_name:
            base_name = f"dataset_{idx}"
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)

        selected = _parse_bool_cell(raw_row.get(selected_col), True) if selected_col else True
        expected = str(raw_row.get(expected_col) or "").strip() if expected_col else ""

        values: dict[str, str] = {}
        for orig_col, clean_v in var_col_map:
            cell = raw_row.get(orig_col)
            values[clean_v] = "" if cell is None else str(cell)

        rows.append(
            DatasetRow(
                name=name,
                selected=selected,
                values=values,
                expected=expected,
            )
        )

    if not rows and not allow_empty:
        raise ValueError("CSV contains headers but zero data rows.")

    return DatasetBundle(
        target_kind=target_kind,
        target_name=target_name,
        variables=variables,
        secret_columns=[v for v in variables if v in secret_set],
        rows=rows,
    )


def export_csv_datasets(bundle: DatasetBundle) -> str:
    """Serialize a DatasetBundle to UTF-8 CSV text."""
    buf = io.StringIO()
    fieldnames = ["dataset", *bundle.variables]
    if any(r.expected for r in bundle.rows):
        fieldnames.append("expected")
    if any(not r.selected for r in bundle.rows):
        fieldnames.append("selected")

    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in bundle.rows:
        out: dict[str, str] = {"dataset": row.name}
        for v in bundle.variables:
            out[v] = row.values.get(v, "")
        if "expected" in fieldnames:
            out["expected"] = row.expected
        if "selected" in fieldnames:
            out["selected"] = "true" if row.selected else "false"
        writer.writerow(out)
    return buf.getvalue()


def load_datasets(
    project_root: Path,
    target_kind: Literal["flow", "spec"],
    target_name: str,
) -> DatasetBundle:
    """Load persisted CSV dataset bundle for a flow or spec if present."""
    root = project_root.resolve()
    rel = _safe_dataset_rel_path(target_kind, target_name)
    csv_path = (root / rel).resolve()
    if not csv_path.is_relative_to(root):
        raise ValueError("Dataset path escapes project root.")

    if not csv_path.is_file():
        return DatasetBundle(
            target_kind=target_kind,
            target_name=target_name,
            variables=[],
            secret_columns=[],
            rows=[],
            source_path=rel.as_posix(),
        )

    meta_path = _meta_path_for_csv(csv_path)
    meta_secrets: list[str] = []
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and isinstance(meta.get("secret_columns"), list):
                meta_secrets = [str(s) for s in meta["secret_columns"]]
        except Exception:
            meta_secrets = []

    bundle = parse_csv_datasets(
        csv_path.read_text(encoding="utf-8"),
        target_kind=target_kind,
        target_name=target_name,
        existing_secret_cols=meta_secrets,
        allow_empty=True,
    )
    bundle.source_path = rel.as_posix()
    return bundle


def save_datasets(
    project_root: Path,
    target_kind: Literal["flow", "spec"],
    target_name: str,
    payload: dict[str, Any],
) -> DatasetBundle:
    """Save dataset variables and rows to `datasets/{flows|specs}/<name>.csv`."""
    root = project_root.resolve()
    rel = _safe_dataset_rel_path(target_kind, target_name)
    csv_path = (root / rel).resolve()
    if not csv_path.is_relative_to(root):
        raise ValueError("Dataset path escapes project root.")

    existing = load_datasets(root, target_kind, target_name)
    existing_by_name = {r.name: r for r in existing.rows}

    raw_vars = [str(v).strip() for v in (payload.get("variables") or []) if str(v).strip()]
    variables: list[str] = []
    seen_vars: set[str] = set()
    for rv in raw_vars:
        cv = _clean_var_name(rv)
        if cv not in seen_vars and cv.lower() not in _RESERVED_COLS:
            seen_vars.add(cv)
            variables.append(cv)

    secret_cols_in = {str(s).strip() for s in (payload.get("secret_columns") or []) if str(s).strip()}
    secret_columns = [v for v in variables if (v in secret_cols_in or is_sensitive_column(v))]

    rows_out: list[DatasetRow] = []
    used_names: set[str] = set()
    for idx, raw_row in enumerate(payload.get("rows") or [], start=1):
        if not isinstance(raw_row, dict):
            continue
        base_name = str(raw_row.get("name") or "").strip() or f"dataset_{idx}"
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)

        selected = bool(raw_row.get("selected", True))
        expected = str(raw_row.get("expected") or "").strip()
        raw_vals = raw_row.get("values") if isinstance(raw_row.get("values"), dict) else {}
        prev_row = existing_by_name.get(str(raw_row.get("orig_name") or name))

        values: dict[str, str] = {}
        for v in variables:
            val = raw_vals.get(v)
            if val == "__KEEP_SECRET__" and prev_row is not None:
                values[v] = prev_row.values.get(v, "")
            else:
                values[v] = "" if val is None else str(val)
        rows_out.append(DatasetRow(name=name, selected=selected, values=values, expected=expected))

    bundle = DatasetBundle(
        target_kind=target_kind,
        target_name=target_name,
        variables=variables,
        secret_columns=secret_columns,
        rows=rows_out,
        source_path=rel.as_posix(),
    )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = _meta_path_for_csv(csv_path)
    if not rows_out and not variables:
        csv_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return bundle

    csv_path.write_text(export_csv_datasets(bundle), encoding="utf-8")
    meta_path.write_text(
        json.dumps({"secret_columns": secret_columns}, indent=2),
        encoding="utf-8",
    )
    return bundle


def import_csv_to_datasets(
    project_root: Path,
    target_kind: Literal["flow", "spec"],
    target_name: str,
    csv_text: str,
) -> DatasetBundle:
    """Parse uploaded CSV text and persist it for the given flow or spec."""
    existing = load_datasets(project_root, target_kind, target_name)
    parsed = parse_csv_datasets(
        csv_text,
        target_kind=target_kind,
        target_name=target_name,
        existing_secret_cols=existing.secret_columns,
    )
    return save_datasets(
        project_root,
        target_kind,
        target_name,
        {
            "variables": parsed.variables,
            "secret_columns": parsed.secret_columns,
            "rows": [r.model_dump() for r in parsed.rows],
        },
    )


def mask_bundle_for_ui(bundle: DatasetBundle) -> dict[str, Any]:
    """Serialize a DatasetBundle for the workbench UI without leaking literal secret values."""
    secret_set = set(bundle.secret_columns)
    ui_rows: list[dict[str, Any]] = []
    for row in bundle.rows:
        cells: dict[str, dict[str, Any]] = {}
        for var in bundle.variables:
            raw = row.values.get(var, "")
            is_secret = var in secret_set
            is_env_ref = bool(_FULL_ENV_REF_RE.match(raw.strip())) if raw else False
            if raw == "":
                cells[var] = {
                    "kind": "empty",
                    "display": "",
                    "is_secret": is_secret,
                    "is_env_ref": False,
                }
            elif is_env_ref:
                cells[var] = {
                    "kind": "env_ref",
                    "display": raw.strip(),
                    "is_secret": is_secret,
                    "is_env_ref": True,
                }
            elif is_secret:
                cells[var] = {
                    "kind": "secret_literal",
                    "display": "••••••",
                    "is_secret": True,
                    "is_env_ref": False,
                }
            else:
                cells[var] = {
                    "kind": "literal",
                    "display": raw,
                    "is_secret": False,
                    "is_env_ref": False,
                }
        ui_rows.append(
            {
                "name": row.name,
                "selected": row.selected,
                "expected": row.expected,
                "cells": cells,
            }
        )
    return {
        "target_kind": bundle.target_kind,
        "target_name": bundle.target_name,
        "variables": list(bundle.variables),
        "secret_columns": list(bundle.secret_columns),
        "source_path": bundle.source_path,
        "rows": ui_rows,
    }


def _iter_param_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_iter_param_strings(item))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_iter_param_strings(v))
    return out


def extract_variables_from_steps(
    steps: list[StepItem],
    flows: dict[str, FlowSpec] | None = None,
) -> list[str]:
    """Extract unique `${var}` variable names referenced directly or via expanded flows."""
    found: list[str] = []
    seen: set[str] = set()
    to_scan = list(steps)
    if flows:
        try:
            to_scan = expand_steps(steps, flows)
        except Exception:
            to_scan = list(steps)

    for step in to_scan:
        for text in _iter_param_strings(step.params):
            for match in _VAR_REF_RE.finditer(text):
                token = match.group(1)
                if token not in seen:
                    seen.add(token)
                    found.append(token)
    return found


def resolve_dataset_vars(
    row: DatasetRow,
    env_ctx: EnvContext,
    secret_columns: list[str],
    extra_env_vars: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve `${ENV_VAR}` references inside dataset cells and register secrets in SecretMasker."""
    resolved: dict[str, str] = {}
    secret_set = set(secret_columns)
    for var_name, raw_val in row.values.items():
        is_secret = var_name in secret_set or is_sensitive_column(var_name)
        if raw_val and "${" in raw_val:
            val = env_ctx.substitute(
                raw_val,
                extra_vars=extra_env_vars,
                record_secret=is_secret,
            )
        else:
            val = raw_val
        if is_secret and val:
            env_ctx.masker.add(val)
        resolved[var_name] = val

    if row.expected:
        resolved.setdefault("expected", row.expected)
    resolved.setdefault("dataset", row.name)
    return resolved
