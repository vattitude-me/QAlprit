"""Spec and reusable flow loader with 1-level-deep flow expansion."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Literal

import frontmatter
from pydantic import BaseModel, Field

STEP_VOCABULARY: frozenset[str] = frozenset(
    {
        "launch",
        "reset_app",
        "background",
        "tap",
        "tap_if_visible",
        "type",
        "clear",
        "swipe",
        "scroll_until_visible",
        "wait",
        "wait_for_visible",
        "assert_visible",
        "assert_not_visible",
        "assert_text",
        "back",
        "hide_keyboard",
        "biometric",
        "run_flow",
        "screenshot",
    }
)

_FLOW_PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class StepItem(BaseModel):
    """Normalized representation of a single test or flow step."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    capture: Literal["on", "off"] = "on"
    secret: bool = False
    flow_name: str | None = None


class FlowSpec(BaseModel):
    """Reusable 1-level-deep flow loaded from flows/**/*.md."""

    name: str
    params: list[str] = Field(default_factory=list)
    steps: list[StepItem] = Field(default_factory=list)
    source_path: Path
    body: str = ""


class TestSpec(BaseModel):
    """Test case specification loaded from specs/**/*.md."""

    __test__ = False

    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    platforms: list[Literal["android", "ios"]] = Field(default_factory=lambda: ["android", "ios"])
    targets: list[Literal["simulator", "device"]] = Field(
        default_factory=lambda: ["simulator", "device"]
    )
    priority: str = "medium"
    preconditions: list[str] = Field(default_factory=list)
    reset: Literal["reinstall", "clear_data", "none"] = "clear_data"
    auto_accept_alerts: bool | None = None
    continue_on_failure: bool = False
    steps: list[StepItem] = Field(default_factory=list)
    teardown: list[StepItem] = Field(default_factory=list)
    expected: str = ""
    suite: str = "default"
    source_path: Path
    body: str = ""


def _normalize_capture(raw_capture: Any) -> Literal["on", "off"]:
    # YAML 1.1 parses unquoted `off` as False and `on` as True.
    if raw_capture is False or str(raw_capture).lower() == "off":
        return "off"
    return "on"


def normalize_step(raw_step: Any) -> StepItem:
    """Convert shorthand or dict YAML step entry into a StepItem."""
    if isinstance(raw_step, str):
        return StepItem(type=raw_step.strip(), params={})

    if not isinstance(raw_step, dict) or not raw_step:
        raise ValueError(f"Step must be a string or single-key mapping, got: {raw_step!r}")

    # Allow top-level modifiers like `capture: off` alongside the step action key
    action_keys = [k for k in raw_step.keys() if k != "capture"]
    if len(action_keys) != 1:
        raise ValueError(
            f"Step must contain exactly one action key (plus optional 'capture'), got: {list(raw_step.keys())}"
        )

    step_type = action_keys[0]
    val = raw_step[step_type]
    top_capture = raw_step.get("capture")

    params: dict[str, Any] = {}
    step_capture = top_capture
    secret = False

    if isinstance(val, dict):
        params = copy.deepcopy(val)
        if "capture" in params:
            step_capture = params.pop("capture")
        if "secret" in params:
            secret = bool(params["secret"])
    elif val is None or isinstance(val, bool):
        params = {}
    elif step_type in ("tap", "tap_if_visible", "clear", "wait_for_visible", "assert_visible", "assert_not_visible", "scroll_until_visible"):
        params = {"target": str(val)}
    elif step_type in ("wait", "background"):
        params = {"seconds": val}
    elif step_type == "launch":
        params = {"app": str(val)}
    elif step_type == "swipe":
        params = {"direction": str(val)}
    elif step_type == "biometric":
        params = {"action": str(val)}
    elif step_type == "screenshot":
        params = {"name": str(val)}
    elif step_type == "run_flow":
        params = {"flow": str(val)}
    else:
        params = {"value": val}

    return StepItem(
        type=step_type,
        params=params,
        capture=_normalize_capture(step_capture),
        secret=secret,
    )


def parse_markdown_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML front matter and Markdown body from a .md file."""
    post = frontmatter.load(str(path))
    metadata = dict(post.metadata or {})
    return metadata, post.content or ""


def load_flows(flows_dir: Path) -> tuple[dict[str, FlowSpec], list[str]]:
    """Load all flow files from flows/**/*.md. Returns (flows_by_name, errors)."""
    flows: dict[str, FlowSpec] = {}
    errors: list[str] = []
    if not flows_dir.exists():
        return flows, errors

    for md_path in sorted(flows_dir.rglob("*.md")):
        try:
            meta, body = parse_markdown_frontmatter(md_path)
            name = meta.get("name")
            if not name:
                rel = md_path.relative_to(flows_dir).with_suffix("")
                name = rel.as_posix()
            raw_steps = meta.get("steps") or []
            steps = [normalize_step(s) for s in raw_steps]
            flow = FlowSpec(
                name=str(name),
                params=[str(p) for p in (meta.get("params") or [])],
                steps=steps,
                source_path=md_path,
                body=body,
            )
            if flow.name in flows:
                errors.append(
                    f"Duplicate flow name '{flow.name}' in {md_path} (already in {flows[flow.name].source_path})"
                )
            else:
                flows[flow.name] = flow
        except Exception as exc:
            errors.append(f"Failed to parse flow {md_path}: {exc}")
    return flows, errors


def load_specs(specs_dir: Path) -> tuple[list[TestSpec], list[str]]:
    """Load all spec files from specs/**/*.md. Returns (specs, errors)."""
    specs: list[TestSpec] = []
    errors: list[str] = []
    if not specs_dir.exists():
        return specs, errors

    for md_path in sorted(specs_dir.rglob("*.md")):
        try:
            meta, body = parse_markdown_frontmatter(md_path)
            if not meta.get("id"):
                raise ValueError("Missing required front matter field 'id'")
            if not meta.get("title"):
                raise ValueError("Missing required front matter field 'title'")
            raw_steps = meta.get("steps")
            if not raw_steps or not isinstance(raw_steps, list):
                raise ValueError("Front matter 'steps' must be a non-empty list")

            rel_parts = md_path.relative_to(specs_dir).parts
            inferred_suite = rel_parts[0] if len(rel_parts) > 1 else "default"
            suite = str(meta.get("suite") or inferred_suite)

            steps = [normalize_step(s) for s in raw_steps]
            teardown = [normalize_step(s) for s in (meta.get("teardown") or [])]

            spec_kwargs: dict[str, Any] = {
                "id": str(meta["id"]),
                "title": str(meta["title"]),
                "tags": [str(t) for t in (meta.get("tags") or [])],
                "platforms": meta.get("platforms") or ["android", "ios"],
                "targets": meta.get("targets") or ["simulator", "device"],
                "priority": str(meta.get("priority") or "medium"),
                "preconditions": [str(p) for p in (meta.get("preconditions") or [])],
                "reset": meta.get("reset") or "clear_data",
                "auto_accept_alerts": meta.get("auto_accept_alerts"),
                "continue_on_failure": bool(meta.get("continue_on_failure", False)),
                "steps": steps,
                "teardown": teardown,
                "expected": str(meta.get("expected") or ""),
                "suite": suite,
                "source_path": md_path,
                "body": body,
            }
            specs.append(TestSpec(**spec_kwargs))
        except Exception as exc:
            errors.append(f"Failed to parse spec {md_path}: {exc}")
    specs.sort(key=lambda s: (s.id, str(s.source_path)))
    return specs, errors


def _substitute_flow_params_in_value(value: Any, flow_params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def _repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in flow_params:
                return str(flow_params[key])
            return match.group(0)

        return _FLOW_PARAM_RE.sub(_repl, value)
    if isinstance(value, list):
        return [_substitute_flow_params_in_value(v, flow_params) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_flow_params_in_value(v, flow_params) for k, v in value.items()}
    return value


def expand_steps(steps: list[StepItem], flows: dict[str, FlowSpec]) -> list[StepItem]:
    """Expand 1-level run_flow steps into individual StepItems."""
    expanded: list[StepItem] = []
    for step in steps:
        if step.type != "run_flow":
            expanded.append(step)
            continue

        flow_name = step.params.get("flow")
        if not flow_name or flow_name not in flows:
            raise KeyError(f"Unknown flow '{flow_name}'")

        flow = flows[flow_name]
        provided_params = step.params.get("params") or {}
        for fstep in flow.steps:
            if fstep.type == "run_flow":
                raise ValueError(
                    f"Nested flow '{fstep.params.get('flow')}' inside '{flow_name}' is not allowed (max 1 level)"
                )
            new_params = _substitute_flow_params_in_value(copy.deepcopy(fstep.params), provided_params)
            # Inherit capture: off if either outer run_flow or inner flow step sets capture: off
            effective_capture: Literal["on", "off"] = (
                "off" if (step.capture == "off" or fstep.capture == "off") else "on"
            )
            expanded.append(
                StepItem(
                    type=fstep.type,
                    params=new_params,
                    capture=effective_capture,
                    secret=fstep.secret or bool(new_params.get("secret", False)),
                    flow_name=flow_name,
                )
            )
    return expanded
