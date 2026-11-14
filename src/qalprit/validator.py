"""Fail-fast validator for configs, screens, flows, specs, locators, and env vars."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Literal

from qalprit.config_loader import (
    DeviceProfile,
    EnvironmentConfig,
    ScreensCatalog,
    load_devices,
    load_environments,
    load_screens,
)
from qalprit.env_loader import EnvContext, load_env
from qalprit.spec_loader import (
    STEP_VOCABULARY,
    FlowSpec,
    StepItem,
    TestSpec,
    expand_steps,
    load_flows,
    load_specs,
)

_TARGETED_STEPS = {
    "tap",
    "tap_if_visible",
    "type",
    "clear",
    "scroll_until_visible",
    "wait_for_visible",
    "wait_for_enabled",
    "wait_for_text",
    "assert_visible",
    "assert_not_visible",
    "assert_text",
    "assert_enabled",
    "assert_disabled",
}
_VALID_DIRECTIONS = {"up", "down", "left", "right"}
_VALID_BIOMETRIC_ACTIONS = {"enroll", "match", "fail"}


@dataclass
class ValidationIssue:
    level: Literal["error", "warning"]
    file: Path
    location: str
    message: str

    def format(self) -> str:
        return f"[{self.level.upper()}] {self.file} ({self.location}): {self.message}"


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    specs: list[TestSpec] = field(default_factory=list)
    flows: dict[str, FlowSpec] = field(default_factory=dict)
    screens: ScreensCatalog | None = None
    environments: dict[str, EnvironmentConfig] = field(default_factory=dict)
    devices: dict[str, DeviceProfile] = field(default_factory=dict)
    env_ctx: EnvContext | None = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _iter_strings(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_iter_strings(item))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_iter_strings(v))
    return found


def _validate_step_shape(
    step: StepItem,
    file_path: Path,
    loc: str,
    issues: list[ValidationIssue],
    allow_run_flow: bool,
) -> None:
    if step.type not in STEP_VOCABULARY:
        issues.append(
            ValidationIssue("error", file_path, loc, f"Unknown step type '{step.type}'")
        )
        return

    if step.type == "run_flow":
        if not allow_run_flow:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    "Nested 'run_flow' inside a flow is forbidden (1-level-deep only)",
                )
            )
            return
        if not step.params.get("flow"):
            issues.append(
                ValidationIssue("error", file_path, loc, "'run_flow' requires a 'flow' name")
            )
        return

    if step.type in _TARGETED_STEPS:
        if not step.params.get("target"):
            issues.append(
                ValidationIssue("error", file_path, loc, f"'{step.type}' requires 'target'")
            )

    if "timeout" in step.params and step.params["timeout"] is not None:
        raw_to = step.params["timeout"]
        if isinstance(raw_to, bool) or not isinstance(raw_to, (int, float)) or raw_to <= 0:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'timeout' must be a positive number > 0, got {raw_to!r}",
                )
            )
        elif raw_to > 300:
            issues.append(
                ValidationIssue(
                    "warning",
                    file_path,
                    loc,
                    f"Timeout {raw_to}s exceeds 300s recommended maximum",
                )
            )

    if step.type == "type":
        if "value" not in step.params or step.params["value"] is None:
            issues.append(
                ValidationIssue("error", file_path, loc, "'type' requires 'value'")
            )

    elif step.type == "swipe":
        direction = step.params.get("direction")
        if direction not in _VALID_DIRECTIONS:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'swipe' direction must be one of {sorted(_VALID_DIRECTIONS)}, got {direction!r}",
                )
            )
        if "duration" in step.params and step.params["duration"] is not None:
            dur = step.params["duration"]
            if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur <= 0:
                issues.append(
                    ValidationIssue("error", file_path, loc, f"'swipe.duration' must be > 0 ms, got {dur!r}")
                )
        if "distance" in step.params and step.params["distance"] is not None:
            dist = step.params["distance"]
            if isinstance(dist, bool) or not isinstance(dist, (int, float)) or not (0.1 <= float(dist) <= 0.9):
                issues.append(
                    ValidationIssue(
                        "error",
                        file_path,
                        loc,
                        f"'swipe.distance' must be between 0.1 and 0.9, got {dist!r}",
                    )
                )

    elif step.type == "scroll_until_visible":
        direction = step.params.get("direction", "down")
        if direction not in _VALID_DIRECTIONS:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'scroll_until_visible' direction must be one of {sorted(_VALID_DIRECTIONS)}, got {direction!r}",
                )
            )
        max_swipes = step.params.get("max_swipes", 8)
        if isinstance(max_swipes, bool) or not isinstance(max_swipes, int) or max_swipes < 1:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'scroll_until_visible' max_swipes must be a positive integer, got {max_swipes!r}",
                )
            )

    elif step.type in ("wait", "background"):
        seconds = step.params.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'{step.type}' requires non-negative numeric 'seconds', got {seconds!r}",
                )
            )
        elif seconds > 300:
            issues.append(
                ValidationIssue(
                    "warning",
                    file_path,
                    loc,
                    f"'{step.type}' seconds={seconds} exceeds 300s recommended maximum",
                )
            )

    elif step.type in ("assert_text", "wait_for_text"):
        if not any(k in step.params for k in ("equals", "contains", "regex", "matches")):
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'{step.type}' requires one of 'equals', 'contains', 'regex', or 'matches'",
                )
            )
        for rx_key in ("regex", "matches"):
            if rx_key in step.params and isinstance(step.params[rx_key], str) and "${" not in step.params[rx_key]:
                try:
                    re.compile(step.params[rx_key])
                except re.error as exc:
                    issues.append(
                        ValidationIssue("error", file_path, loc, f"Invalid regex in '{step.type}': {exc}")
                    )

    elif step.type == "wait_for_keyboard":
        for kb_key in ("shown", "visible"):
            if kb_key in step.params and not isinstance(step.params[kb_key], bool):
                issues.append(
                    ValidationIssue("error", file_path, loc, f"'wait_for_keyboard.{kb_key}' must be boolean")
                )

    elif step.type == "biometric":
        action = step.params.get("action")
        if action not in _VALID_BIOMETRIC_ACTIONS:
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"'biometric' action must be one of {sorted(_VALID_BIOMETRIC_ACTIONS)}, got {action!r}",
                )
            )

    elif step.type == "screenshot":
        if not step.params.get("name"):
            issues.append(
                ValidationIssue("error", file_path, loc, "'screenshot' requires 'name'")
            )

    elif step.type == "visual_compare":
        if not (step.params.get("baseline") or step.params.get("name")):
            issues.append(
                ValidationIssue("error", file_path, loc, "'visual_compare' requires 'baseline' or 'name'")
            )
        if "threshold" in step.params and step.params["threshold"] is not None:
            th = step.params["threshold"]
            if isinstance(th, bool) or not isinstance(th, (int, float)) or not (0.0 <= float(th) <= 1.0):
                issues.append(
                    ValidationIssue(
                        "error",
                        file_path,
                        loc,
                        f"'visual_compare.threshold' must be between 0.0 and 1.0, got {th!r}",
                    )
                )


def _validate_locator_and_env(
    step: StepItem,
    platforms: list[str],
    screens: ScreensCatalog | None,
    env_ctx: EnvContext | None,
    extra_env_vars: dict[str, str],
    allowed_flow_params: set[str],
    file_path: Path,
    loc: str,
    issues: list[ValidationIssue],
) -> None:
    # Check env vars across all step string values
    if env_ctx is not None:
        for text in _iter_strings(step.params):
            for missing_var in env_ctx.find_missing(text, extra_vars=extra_env_vars):
                if missing_var in allowed_flow_params:
                    continue
                issues.append(
                    ValidationIssue(
                        "error",
                        file_path,
                        loc,
                        f"Missing environment variable '${{{missing_var}}}'",
                    )
                )

    target = step.params.get("target")
    if target and screens is not None and step.type in _TARGETED_STEPS:
        if not screens.has_target(target):
            issues.append(
                ValidationIssue(
                    "error",
                    file_path,
                    loc,
                    f"Unknown locator target '{target}' (not in screens/screens.yaml)",
                )
            )
            return

        step_tpl_params = step.params.get("params") or {}
        if not isinstance(step_tpl_params, dict):
            issues.append(
                ValidationIssue("error", file_path, loc, "'params' must be a mapping")
            )
            step_tpl_params = {}

        for plat in platforms:
            if not screens.has_target(target, platform=plat):
                issues.append(
                    ValidationIssue(
                        "error",
                        file_path,
                        loc,
                        f"Locator '{target}' is missing definition for platform '{plat}'",
                    )
                )
                continue

            req_params = screens.required_template_params(target, plat)
            missing_params = sorted(req_params - set(step_tpl_params.keys()))
            if missing_params:
                issues.append(
                    ValidationIssue(
                        "error",
                        file_path,
                        loc,
                        f"Locator '{target}' ({plat}) missing template param(s): {', '.join(missing_params)}",
                    )
                )

            if env_ctx is not None:
                for env_var in screens.required_env_vars(target, plat):
                    if env_ctx.find_missing(f"${{{env_var}}}", extra_vars=extra_env_vars):
                        issues.append(
                            ValidationIssue(
                                "error",
                                file_path,
                                loc,
                                f"Locator '{target}' ({plat}) references missing env var '${{{env_var}}}'",
                            )
                        )

            if step.type == "type" and screens.get_entry(target, plat).sensitive and not step.secret:
                issues.append(
                    ValidationIssue(
                        "warning",
                        file_path,
                        loc,
                        f"Typing into sensitive locator '{target}' ({plat}) without 'secret: true' may expose value in reports/logs",
                    )
                )


def validate_project(
    project_root: Path,
    env_name: str | None = None,
    suite: str | None = None,
    tags: list[str] | None = None,
    platform: str | None = None,
    app_path: Path | None = None,
) -> ValidationResult:
    """Validate project configs, screens, flows, specs, locators, and env vars."""
    result = ValidationResult()

    env_path = project_root / "config" / "environments.yaml"
    dev_path = project_root / "config" / "devices.yaml"
    screens_path = project_root / "screens" / "screens.yaml"
    flows_dir = project_root / "flows"
    specs_dir = project_root / "specs"

    try:
        result.environments = load_environments(env_path)
    except Exception as exc:
        result.issues.append(ValidationIssue("error", env_path, "environments", str(exc)))

    try:
        result.devices = load_devices(dev_path)
    except Exception as exc:
        result.issues.append(ValidationIssue("error", dev_path, "devices", str(exc)))

    extra_env_vars: dict[str, str] = {}
    if env_name:
        if result.environments and env_name not in result.environments:
            result.issues.append(
                ValidationIssue(
                    "error",
                    env_path,
                    "environments",
                    f"Unknown environment '{env_name}'. Available: {', '.join(sorted(result.environments))}",
                )
            )
        elif env_name in result.environments:
            extra_env_vars = result.environments[env_name].as_env_vars()
    elif result.environments:
        # Default to first configured environment for ANDROID_PACKAGE / IOS_BUNDLE_ID checks if --env omitted
        first_env = next(iter(result.environments.values()))
        extra_env_vars = first_env.as_env_vars()

    result.env_ctx = load_env(project_root, env_name=env_name, extra_vars=extra_env_vars)

    try:
        result.screens = load_screens(screens_path)
    except Exception as exc:
        result.issues.append(ValidationIssue("error", screens_path, "screens", str(exc)))

    if app_path is not None:
        if not app_path.exists():
            result.issues.append(
                ValidationIssue("error", app_path, "app", f"App binary not found: {app_path}")
            )
        elif platform:
            suffix = app_path.suffix.lower()
            valid_exts = {"android": {".apk", ".aab"}, "ios": {".app", ".ipa"}}.get(platform, set())
            if valid_exts and suffix not in valid_exts:
                result.issues.append(
                    ValidationIssue(
                        "warning",
                        app_path,
                        "app",
                        f"App binary '{app_path.name}' extension '{suffix or '<none>'}' is unusual for platform '{platform}' (expected {', '.join(sorted(valid_exts))})",
                    )
                )

    flows, flow_parse_errors = load_flows(flows_dir)
    result.flows = flows
    for err in flow_parse_errors:
        result.issues.append(ValidationIssue("error", flows_dir, "parse", err))

    all_specs, spec_parse_errors = load_specs(specs_dir)
    for err in spec_parse_errors:
        result.issues.append(ValidationIssue("error", specs_dir, "parse", err))

    seen_ids: dict[str, Path] = {}
    for spec in all_specs:
        if spec.id in seen_ids:
            result.issues.append(
                ValidationIssue(
                    "error",
                    spec.source_path,
                    "id",
                    f"Duplicate spec id '{spec.id}' (already defined in {seen_ids[spec.id]})",
                )
            )
        else:
            seen_ids[spec.id] = spec.source_path

    # Filter specs if suite/tags/platform requested
    selected_specs: list[TestSpec] = []
    for spec in all_specs:
        if suite and spec.suite != suite:
            continue
        if platform and platform not in spec.platforms:
            continue
        if tags and not any(t in spec.tags for t in tags):
            continue
        selected_specs.append(spec)
    result.specs = selected_specs

    if suite is None and tags is None:
        flows_to_validate = list(flows.values())
    else:
        referenced_flow_names = {
            step.params.get("flow")
            for spec in selected_specs
            for phase_steps in (spec.setup, spec.steps, spec.teardown)
            for step in phase_steps
            if step.type == "run_flow" and step.params.get("flow")
        }
        flows_to_validate = [
            flows[name] for name in sorted(referenced_flow_names) if name in flows
        ]

    flow_platforms = [platform] if platform else ["android", "ios"]
    for flow in flows_to_validate:
        declared_params = set(flow.params)
        flow_text = "\n".join(_iter_strings([s.params for s in flow.steps]))
        for param_name in flow.params:
            if f"${{{param_name}}}" not in flow_text and f"{{{param_name}}}" not in flow_text:
                result.issues.append(
                    ValidationIssue(
                        "warning",
                        flow.source_path,
                        f"flow:{flow.name}.params",
                        f"Declared flow param '{param_name}' is not referenced in any step",
                    )
                )
        for idx, step in enumerate(flow.steps, start=1):
            loc = f"flow:{flow.name}.steps[{idx}]"
            _validate_step_shape(step, flow.source_path, loc, result.issues, allow_run_flow=False)
            _validate_locator_and_env(
                step,
                flow_platforms,
                result.screens,
                result.env_ctx,
                extra_env_vars,
                declared_params,
                flow.source_path,
                loc,
                result.issues,
            )

    for spec in selected_specs:
        if spec.retries is not None and (spec.retries < 0 or spec.retries > 5):
            result.issues.append(
                ValidationIssue(
                    "error",
                    spec.source_path,
                    f"{spec.id}.retries",
                    f"'retries' must be between 0 and 5, got {spec.retries!r}",
                )
            )
        spec_platforms = [platform] if platform else list(spec.platforms)
        for phase_name, step_list in (("setup", spec.setup), ("steps", spec.steps), ("teardown", spec.teardown)):
            for idx, step in enumerate(step_list, start=1):
                loc = f"{spec.id}.{phase_name}[{idx}]"
                _validate_step_shape(step, spec.source_path, loc, result.issues, allow_run_flow=True)

                if step.type == "run_flow":
                    flow_name = step.params.get("flow")
                    if not flow_name:
                        continue
                    if flow_name not in flows:
                        result.issues.append(
                            ValidationIssue(
                                "error",
                                spec.source_path,
                                loc,
                                f"Unknown flow '{flow_name}'",
                            )
                        )
                        continue
                    flow = flows[flow_name]
                    provided = step.params.get("params") or {}
                    if not isinstance(provided, dict):
                        result.issues.append(
                            ValidationIssue(
                                "error",
                                spec.source_path,
                                loc,
                                "'run_flow.params' must be a mapping",
                            )
                        )
                        continue
                    missing_fp = sorted(set(flow.params) - set(provided.keys()))
                    if missing_fp:
                        result.issues.append(
                            ValidationIssue(
                                "error",
                                spec.source_path,
                                loc,
                                f"Flow '{flow_name}' missing required param(s): {', '.join(missing_fp)}",
                            )
                        )
                    unexpected_fp = sorted(set(provided.keys()) - set(flow.params))
                    if unexpected_fp:
                        result.issues.append(
                            ValidationIssue(
                                "warning",
                                spec.source_path,
                                loc,
                                f"Flow '{flow_name}' received unused param(s): {', '.join(unexpected_fp)}",
                            )
                        )
                    # Check expanded steps for env vars & platform locators
                    try:
                        expanded = expand_steps([step], flows)
                    except Exception as exc:
                        result.issues.append(
                            ValidationIssue("error", spec.source_path, loc, str(exc))
                        )
                        continue
                    for e_idx, e_step in enumerate(expanded, start=1):
                        e_loc = f"{loc}->flow:{flow_name}[{e_idx}]"
                        _validate_step_shape(
                            e_step, spec.source_path, e_loc, result.issues, allow_run_flow=False
                        )
                        _validate_locator_and_env(
                            e_step,
                            spec_platforms,
                            result.screens,
                            result.env_ctx,
                            extra_env_vars,
                            set(),
                            spec.source_path,
                            e_loc,
                            result.issues,
                        )
                else:
                    _validate_locator_and_env(
                        step,
                        spec_platforms,
                        result.screens,
                        result.env_ctx,
                        extra_env_vars,
                        set(),
                        spec.source_path,
                        loc,
                        result.issues,
                    )

    return result
