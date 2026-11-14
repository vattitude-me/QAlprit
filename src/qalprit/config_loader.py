"""Configuration loaders for environments.yaml, devices.yaml, and screens.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from qalprit.env_loader import EnvContext

_LOCATOR_STRATEGIES = (
    "accessibility_id",
    "id",
    "text",
    "xpath",
    "class_name",
    "ios_predicate",
    "android_uiautomator",
)
_TEMPLATE_PARAM_RE = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z0-9_:.]+)\}")


class EnvironmentConfig(BaseModel):
    """Per-environment app identifiers and timeouts."""

    android_package: str = ""
    ios_bundle_id: str = ""
    default_timeout: float = Field(default=10.0, gt=0)
    poll_interval: float = Field(default=0.25, gt=0)
    secure_evidence: bool = True
    save_page_source_on_failure: bool = True
    variables: dict[str, str] = Field(default_factory=dict)

    def as_env_vars(self) -> dict[str, str]:
        data = dict(self.variables)
        if self.android_package:
            data.setdefault("ANDROID_PACKAGE", self.android_package)
        if self.ios_bundle_id:
            data.setdefault("IOS_BUNDLE_ID", self.ios_bundle_id)
        return data


class DeviceProfile(BaseModel):
    """Named device or simulator profile."""

    platform: Literal["android", "ios"]
    target_type: Literal["simulator", "device"] = "simulator"
    device_name: str
    udid: str | None = None
    avd: str | None = None
    platform_version: str | None = None
    automation_name: str | None = None
    auto_accept_alerts: bool = True
    capabilities: dict[str, Any] = Field(default_factory=dict)


class LocatorEntry(BaseModel):
    """Concrete locator definition for one platform."""

    accessibility_id: str | None = None
    id: str | None = None
    text: str | None = None
    xpath: str | None = None
    class_name: str | None = None
    ios_predicate: str | None = None
    android_uiautomator: str | None = None
    sensitive: bool = False

    @model_validator(mode="after")
    def _validate_single_strategy(self) -> LocatorEntry:
        found = [key for key in _LOCATOR_STRATEGIES if getattr(self, key) is not None]
        if not found:
            raise ValueError(
                "Locator must specify at least one strategy (e.g. accessibility_id, id, text, xpath)"
            )
        if len(found) > 1:
            raise ValueError(
                f"Locator specifies multiple competing strategies ({', '.join(found)}); specify exactly one strategy per platform entry"
            )
        return self

    def strategy_and_value(self) -> tuple[str, str]:
        for key in _LOCATOR_STRATEGIES:
            val = getattr(self, key)
            if val is not None:
                return key, val
        raise ValueError("Locator must specify at least one strategy (e.g. accessibility_id, id, text, xpath)")


@dataclass(frozen=True)
class ResolvedLocator:
    """Resolved locator ready for Appium or FakeDriver."""

    target: str
    strategy: str
    value: str
    sensitive: bool = False


class ScreensCatalog:
    """Catalog of logical screen.element names mapped to platform locators."""

    def __init__(self, raw: dict[str, dict[str, Any]]) -> None:
        self._raw = raw
        self._entries: dict[str, dict[str, LocatorEntry]] = {}
        self._parse(raw)

    def _parse(self, raw: dict[str, dict[str, Any]]) -> None:
        for screen_name, elements in raw.items():
            if not isinstance(elements, dict):
                raise ValueError(f"Screen '{screen_name}' must map to a dictionary of elements")
            for elem_name, definition in elements.items():
                logical = f"{screen_name}.{elem_name}"
                if not isinstance(definition, dict):
                    raise ValueError(f"Locator '{logical}' must be a mapping")
                if "android" in definition or "ios" in definition:
                    root_sensitive = bool(definition.get("sensitive", False))
                    platform_map: dict[str, LocatorEntry] = {}
                    for plat in ("android", "ios"):
                        if plat in definition and definition[plat] is not None:
                            pdata = dict(definition[plat])
                            pdata.setdefault("sensitive", root_sensitive)
                            entry = LocatorEntry(**pdata)
                            entry.strategy_and_value()
                            platform_map[plat] = entry
                    if not platform_map:
                        raise ValueError(f"Locator '{logical}' has empty platform overrides")
                    self._entries[logical] = platform_map
                else:
                    entry = LocatorEntry(**definition)
                    entry.strategy_and_value()
                    self._entries[logical] = {"android": entry, "ios": entry}

    def has_target(self, target: str, platform: str | None = None) -> bool:
        if target not in self._entries:
            return False
        if platform is None:
            return True
        return platform in self._entries[target]

    def all_targets(self) -> list[str]:
        return sorted(self._entries.keys())

    def get_entry(self, target: str, platform: str) -> LocatorEntry:
        if target not in self._entries:
            raise KeyError(f"Unknown locator target '{target}' in screens.yaml")
        plat_map = self._entries[target]
        if platform not in plat_map:
            raise KeyError(f"Locator '{target}' has no definition for platform '{platform}'")
        return plat_map[platform]

    def required_template_params(self, target: str, platform: str) -> set[str]:
        entry = self.get_entry(target, platform)
        _, raw_val = entry.strategy_and_value()
        return set(_TEMPLATE_PARAM_RE.findall(raw_val))

    def required_env_vars(self, target: str, platform: str) -> list[str]:
        entry = self.get_entry(target, platform)
        _, raw_val = entry.strategy_and_value()
        return [m.group(1) for m in _ENV_VAR_RE.finditer(raw_val)]

    def sensitive_targets(self, platform: str) -> list[str]:
        """Return non-parameterized targets marked sensitive: true on this platform."""
        result: list[str] = []
        for target, plat_map in self._entries.items():
            entry = plat_map.get(platform)
            if entry and entry.sensitive:
                _, raw_val = entry.strategy_and_value()
                if not _TEMPLATE_PARAM_RE.search(raw_val):
                    result.append(target)
        return result

    def resolve(
        self,
        target: str,
        platform: str,
        env_ctx: EnvContext | None = None,
        params: dict[str, Any] | None = None,
        extra_vars: dict[str, str] | None = None,
    ) -> ResolvedLocator:
        entry = self.get_entry(target, platform)
        strategy, value = entry.strategy_and_value()

        if env_ctx is not None:
            value = env_ctx.substitute(value, extra_vars=extra_vars)
        elif extra_vars:
            for k, v in extra_vars.items():
                value = value.replace(f"${{{k}}}", v)

        needed = _TEMPLATE_PARAM_RE.findall(value)
        if needed:
            provided = params or {}
            missing = [p for p in needed if p not in provided]
            if missing:
                raise KeyError(
                    f"Locator '{target}' missing template parameter(s): {', '.join(missing)}"
                )
            for p in needed:
                value = value.replace(f"{{{p}}}", str(provided[p]))

        return ResolvedLocator(
            target=target,
            strategy=strategy,
            value=value,
            sensitive=entry.sensitive,
        )


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def load_environments(path: Path) -> dict[str, EnvironmentConfig]:
    """Load config/environments.yaml into EnvironmentConfig objects."""
    raw = _read_yaml_dict(path)
    env_block = raw.get("environments", raw)
    if not isinstance(env_block, dict):
        raise ValueError(f"Invalid environments block in {path}")
    return {name: EnvironmentConfig(**(cfg or {})) for name, cfg in env_block.items()}


def load_devices(path: Path) -> dict[str, DeviceProfile]:
    """Load config/devices.yaml into DeviceProfile objects."""
    raw = _read_yaml_dict(path)
    profiles_block = raw.get("profiles", raw.get("devices", raw))
    if not isinstance(profiles_block, dict):
        raise ValueError(f"Invalid device profiles block in {path}")
    return {name: DeviceProfile(**(cfg or {})) for name, cfg in profiles_block.items()}


def load_screens(path: Path) -> ScreensCatalog:
    """Load screens/screens.yaml into a ScreensCatalog."""
    raw = _read_yaml_dict(path)
    return ScreensCatalog(raw)
