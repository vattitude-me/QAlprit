"""Tests for fail-fast validation of specs, flows, locators, params, and env vars."""

from __future__ import annotations

from pathlib import Path

from qalprit.validator import validate_project


def _write_minimal_project(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "screens").mkdir(parents=True, exist_ok=True)
    (root / "flows" / "auth").mkdir(parents=True, exist_ok=True)
    (root / "specs" / "smoke").mkdir(parents=True, exist_ok=True)

    (root / ".env").write_text("QA_USER=alice\nQA_PASS=pw123\n", encoding="utf-8")
    (root / "config" / "environments.yaml").write_text(
        "environments:\n  qa:\n    android_package: com.bank.qa\n    ios_bundle_id: com.bank.qa\n    default_timeout: 5\n",
        encoding="utf-8",
    )
    (root / "config" / "devices.yaml").write_text(
        "profiles:\n  android_emu:\n    platform: android\n    target_type: simulator\n    device_name: emu\n",
        encoding="utf-8",
    )
    (root / "screens" / "screens.yaml").write_text(
        """
login:
  username_field: {accessibility_id: username_field}
  password_field: {accessibility_id: password_field, sensitive: true}
  login_button: {accessibility_id: login_button}
accounts:
  account_list:
    android: {id: "${ANDROID_PACKAGE}:id/account_list"}
    ios: {accessibility_id: account_list}
  account_row:
    android: {text: "{name}"}
    ios: {accessibility_id: "row_{name}"}
""",
        encoding="utf-8",
    )
    (root / "flows" / "auth" / "login.md").write_text(
        """---
name: auth/login
params: [username, password]
steps:
  - type: {target: login.username_field, value: "${username}"}
  - type: {target: login.password_field, value: "${password}", secret: true}
  - tap: login.login_button
---
""",
        encoding="utf-8",
    )


def test_valid_project_passes(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    (tmp_path / "specs" / "smoke" / "ok.md").write_text(
        """---
id: SMOKE-001
title: Valid spec
platforms: [android, ios]
steps:
  - run_flow:
      flow: auth/login
      params:
        username: "${QA_USER}"
        password: "${QA_PASS}"
  - assert_visible: accounts.account_list
  - tap:
      target: accounts.account_row
      params: {name: "Checking"}
---
""",
        encoding="utf-8",
    )
    res = validate_project(tmp_path, env_name="qa")
    assert res.ok, [i.format() for i in res.errors]


def test_unknown_step_type_fails(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    (tmp_path / "specs" / "smoke" / "bad_step.md").write_text(
        "---\nid: BAD-1\ntitle: Bad step\nsteps:\n  - magic_tap: login.login_button\n---\n",
        encoding="utf-8",
    )
    res = validate_project(tmp_path, env_name="qa")
    assert not res.ok
    assert any("Unknown step type 'magic_tap'" in e.message for e in res.errors)


def test_nested_flow_fails(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    (tmp_path / "flows" / "auth" / "nested.md").write_text(
        "---\nname: auth/nested\nsteps:\n  - run_flow: auth/login\n---\n",
        encoding="utf-8",
    )
    res = validate_project(tmp_path, env_name="qa")
    assert not res.ok
    assert any("Nested 'run_flow'" in e.message for e in res.errors)


def test_unknown_locator_and_missing_template_param_and_missing_env_fail(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    (tmp_path / "specs" / "smoke" / "bad_refs.md").write_text(
        """---
id: BAD-2
title: Bad refs
steps:
  - tap: nonexistent.button
  - tap: accounts.account_row
  - type: {target: login.username_field, value: "${UNDEFINED_VAR}"}
  - run_flow: auth/missing_flow
---
""",
        encoding="utf-8",
    )
    res = validate_project(tmp_path, env_name="qa")
    assert not res.ok
    msgs = [e.message for e in res.errors]
    assert any("Unknown locator target 'nonexistent.button'" in m for m in msgs)
    assert any("missing template param(s): name" in m for m in msgs)
    assert any("UNDEFINED_VAR" in m for m in msgs)
    assert any("Unknown flow 'auth/missing_flow'" in m for m in msgs)


def test_suite_filter_ignores_unreferenced_broken_flows(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    (tmp_path / "flows" / "other").mkdir(parents=True, exist_ok=True)
    (tmp_path / "flows" / "other" / "broken.md").write_text(
        "---\nname: other/broken\nsteps:\n  - tap: unknown.button\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "specs" / "smoke" / "ok.md").write_text(
        """---
id: SMOKE-001
title: Valid spec
platforms: [android, ios]
steps:
  - run_flow:
      flow: auth/login
      params:
        username: "${QA_USER}"
        password: "${QA_PASS}"
---
""",
        encoding="utf-8",
    )
    res_scoped = validate_project(tmp_path, env_name="qa", suite="smoke")
    assert res_scoped.ok, [i.format() for i in res_scoped.errors]

    res_all = validate_project(tmp_path, env_name="qa")
    assert not res_all.ok
