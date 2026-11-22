"""if_visible and repeat blocks, runs against the app already installed, and the recorder's
"only if shown" steps."""

from __future__ import annotations

from pathlib import Path

from qalprit.config_loader import DeviceProfile, EnvironmentConfig, ScreensCatalog
from qalprit.env_loader import EnvContext
from qalprit.recorder import _step_to_yaml_dict, is_system_dialog_element
from qalprit.runner import TestRunner, apply_app_reset, installed_app_problems
from qalprit.spec_loader import FlowSpec, TestSpec, launched_app_id, normalize_step
from qalprit.validator import validate_project
from tests.fake_driver import FakeDriver
from tests.test_validator import _write_minimal_project


def _screens() -> ScreensCatalog:
    return ScreensCatalog(
        {
            "popup": {
                "allow": {"accessibility_id": "allow"},
                "title": {"accessibility_id": "popup_title"},
            },
            "list": {
                "add": {"accessibility_id": "add"},
                "row": {"accessibility_id": "row_{n}"},
                "done": {"accessibility_id": "done"},
            },
            "home": {"title": {"accessibility_id": "home_title"}},
        }
    )


def _runner(tmp_path: Path, driver: FakeDriver, *, flows=None, env_cfg=None, app_path=None) -> TestRunner:
    env_cfg = env_cfg or EnvironmentConfig(android_package="com.bank.qa", default_timeout=0.3)
    return TestRunner(
        driver_factory=lambda _aa: driver,
        profile=DeviceProfile(platform="android", target_type="simulator", device_name="emu"),
        env_name="qa",
        env_cfg=env_cfg,
        env_ctx=EnvContext(variables={}),
        screens=_screens(),
        flows=flows or {},
        run_dir=tmp_path / "results" / "r1",
        run_id="r1",
        poll_interval=0.01,
        app_path=app_path,
    )


_SRC = Path("specs/t.md")


def _spec(steps: list, reset: str = "none") -> TestSpec:
    return TestSpec(id="T-1", title="t", reset=reset, steps=[normalize_step(s) for s in steps], source_path=_SRC)


# ---------------- if_visible ----------------


def test_if_visible_runs_then_when_the_popup_shows(tmp_path: Path) -> None:
    driver = FakeDriver()
    allow = driver.add_element("accessibility_id", "allow")
    driver.add_element("accessibility_id", "popup_title")
    driver.add_element("accessibility_id", "home_title")
    spec = _spec([
        {"if_visible": {"target": "popup.title", "timeout": 0.1, "then": [{"tap": "popup.allow"}]}},
        {"assert_visible": "home.title"},
    ])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.passed == 1
    assert allow.clicks == 1
    descs = [s.description for s in res.tests[0].steps]
    assert any("yes, running 1 step" in d for d in descs)
    assert any(d.startswith("If shown: ") for d in descs)


def test_if_visible_skips_then_and_runs_else_when_the_popup_is_gone(tmp_path: Path) -> None:
    driver = FakeDriver()
    allow = driver.add_element("accessibility_id", "allow")
    done = driver.add_element("accessibility_id", "done")
    spec = _spec([
        {"if_visible": {
            "target": "popup.title",
            "timeout": 0.05,
            "then": [{"tap": "popup.allow"}],
            "else": [{"tap": "list.done"}],
        }},
    ])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.passed == 1
    assert allow.clicks == 0
    assert done.clicks == 1
    assert any(s.description.startswith("Otherwise: ") for s in res.tests[0].steps)


def test_if_visible_without_else_passes_when_nothing_shows(tmp_path: Path) -> None:
    driver = FakeDriver()
    spec = _spec([{"if_visible": {"target": "popup.title", "timeout": 0.05, "then": [{"tap": "popup.allow"}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.passed == 1
    assert "nothing to do" in res.tests[0].steps[0].description


def test_a_failing_step_inside_if_visible_fails_the_test(tmp_path: Path) -> None:
    driver = FakeDriver()
    driver.add_element("accessibility_id", "popup_title")
    spec = _spec([{"if_visible": {"target": "popup.title", "timeout": 0.05, "then": [{"tap": "popup.allow"}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.failed == 1


# ---------------- repeat ----------------


def test_repeat_times_sets_the_round_variable(tmp_path: Path) -> None:
    driver = FakeDriver()
    rows = [driver.add_element("accessibility_id", f"row_{n}") for n in (1, 2, 3)]
    spec = _spec([{"repeat": {"times": 3, "steps": [{"tap": {"target": "list.row", "params": {"n": "${round}"}}}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.passed == 1, res.tests[0].attempts[-1].error
    assert [r.clicks for r in rows] == [1, 1, 1]
    descs = [s.description for s in res.tests[0].steps]
    assert any(d.startswith("Round 3: ") for d in descs)
    assert any("3 rounds passed" in d for d in descs)


def test_repeat_until_stops_when_the_element_shows(tmp_path: Path) -> None:
    driver = FakeDriver()
    add = driver.add_element("accessibility_id", "add")
    driver.appear_after("accessibility_id", "done", calls=3)
    spec = _spec([{"repeat": {"until": "list.done", "max": 10, "until_timeout": 0.01, "steps": [{"tap": "list.add"}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.passed == 1, res.tests[0].attempts[-1].error
    assert 1 <= add.clicks < 10
    assert any("is shown after" in s.description for s in res.tests[0].steps)


def test_repeat_until_fails_when_the_element_never_shows(tmp_path: Path) -> None:
    driver = FakeDriver()
    add = driver.add_element("accessibility_id", "add")
    spec = _spec([{"repeat": {"until": "list.done", "max": 3, "until_timeout": 0.01, "steps": [{"tap": "list.add"}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.failed == 1
    assert add.clicks == 3
    assert "did not appear after 3 rounds" in (res.tests[0].attempts[-1].error or "")


def test_repeat_stops_at_the_first_failing_round(tmp_path: Path) -> None:
    driver = FakeDriver()
    driver.add_element("accessibility_id", "row_1")
    spec = _spec([{"repeat": {"times": 3, "steps": [{"tap": {"target": "list.row", "params": {"n": "${round}"}}}]}}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.failed == 1
    assert not any(s.description.startswith("Round 3: ") for s in res.tests[0].steps)


def test_blocks_inside_a_workflow_take_the_workflow_params(tmp_path: Path) -> None:
    driver = FakeDriver()
    row = driver.add_element("accessibility_id", "row_7")
    driver.add_element("accessibility_id", "popup_title")
    flow = FlowSpec(
        name="pick",
        params=["which"],
        steps=[normalize_step({"if_visible": {
            "target": "popup.title", "timeout": 0.05,
            "then": [{"tap": {"target": "list.row", "params": {"n": "${which}"}}}],
        }})],
        source_path=tmp_path / "flows" / "pick.md",
    )
    spec = _spec([{"run_flow": {"flow": "pick", "params": {"which": "7"}}}])
    res = _runner(tmp_path, driver, flows={"pick": flow}).run([spec])
    assert res.summary.passed == 1, res.tests[0].attempts[-1].error
    assert row.clicks == 1


# ---------------- validator ----------------


def _write_spec(root: Path, body: str) -> None:
    (root / "specs" / "smoke" / "t.md").write_text(
        "---\nid: SMOKE-009\ntitle: t\nplatforms: [android]\nreset: none\nsteps:\n" + body + "---\n",
        encoding="utf-8",
    )


def test_validator_accepts_blocks_and_checks_nested_locators(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    _write_spec(tmp_path, (
        "  - if_visible:\n"
        "      target: login.login_button\n"
        "      then:\n"
        "        - tap: login.login_button\n"
        "  - repeat:\n"
        "      times: 2\n"
        "      steps:\n"
        "        - tap: login.no_such_button\n"
    ))
    res = validate_project(tmp_path, env_name="qa", platform="android")
    msgs = " ".join(i.format() for i in res.errors)
    assert "login.no_such_button" in msgs


def test_validator_rejects_a_repeat_without_rounds(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    _write_spec(tmp_path, "  - repeat:\n      steps:\n        - tap: login.login_button\n")
    res = validate_project(tmp_path, env_name="qa", platform="android")
    assert not res.ok
    assert any("times" in i.format() for i in res.errors)


def test_validator_rejects_if_visible_without_branches(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    _write_spec(tmp_path, "  - if_visible: login.login_button\n")
    res = validate_project(tmp_path, env_name="qa", platform="android")
    assert not res.ok


def test_validator_accepts_a_valid_repeat_with_the_round_variable(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    _write_spec(tmp_path, (
        "  - repeat:\n"
        "      until: login.login_button\n"
        "      max: 4\n"
        "      steps:\n"
        "        - tap: {target: accounts.account_row, params: {name: \"${round}\"}}\n"
    ))
    res = validate_project(tmp_path, env_name="qa", platform="android")
    assert res.ok, [i.format() for i in res.errors]


# ---------------- installed app, no app file ----------------


def test_launched_app_id_reads_the_first_launch_step() -> None:
    steps = [normalize_step("back"), normalize_step({"launch": {"app": "ca.tng.app", "restart": True}})]
    assert launched_app_id(steps) == "ca.tng.app"
    assert launched_app_id([normalize_step({"launch": "app"})]) == ""


def test_reinstall_without_an_app_file_never_uninstalls() -> None:
    driver = FakeDriver()
    profile = DeviceProfile(platform="android", target_type="device", device_name="phone")
    ok, msg = apply_app_reset(driver, "reinstall", app_id="ca.tng.app", profile=profile, app_path=None, masker=EnvContext(variables={}).masker)
    assert not ok
    assert "needs the app file" in msg
    assert not any(c[0] == "remove_app" for c in driver.calls)


def test_ios_clear_data_without_an_app_file_never_uninstalls() -> None:
    driver = FakeDriver(platform="ios")
    profile = DeviceProfile(platform="ios", target_type="simulator", device_name="sim")
    ok, _ = apply_app_reset(driver, "clear_data", app_id="ca.tng.app", profile=profile, app_path=None, masker=EnvContext(variables={}).masker)
    assert not ok
    assert not any(c[0] == "remove_app" for c in driver.calls)


def test_run_without_app_file_uses_the_journeys_launch_step(tmp_path: Path) -> None:
    driver = FakeDriver()
    driver.add_element("accessibility_id", "home_title")
    env_cfg = EnvironmentConfig(default_timeout=0.3)  # no package in the environment
    spec = _spec([{"launch": {"app": "ca.tng.app", "restart": True}}, {"assert_visible": "home.title"}], reset="clear_data")
    res = _runner(tmp_path, driver, env_cfg=env_cfg).run([spec])
    assert res.summary.passed == 1, res.tests[0].attempts[-1].error
    assert ("is_app_installed", "ca.tng.app") in driver.calls
    assert ("activate_app", "ca.tng.app") in driver.calls


def test_run_without_app_file_fails_clearly_when_the_app_is_missing(tmp_path: Path) -> None:
    driver = FakeDriver()
    driver.installed_apps = set()
    spec = _spec([{"launch": "app"}])
    res = _runner(tmp_path, driver).run([spec])
    assert res.summary.failed == 1
    assert "com.bank.qa is not installed on this device" in (res.tests[0].attempts[-1].error or "")
    assert not any(c[0] == "remove_app" for c in driver.calls)


def test_installed_app_problems_names_what_is_missing() -> None:
    env_cfg = EnvironmentConfig()
    no_app = TestSpec(id="A-1", title="a", reset="none", steps=[normalize_step("back")], source_path=_SRC)
    reinstall = TestSpec(id="A-2", title="b", reset="reinstall", steps=[normalize_step({"launch": "ca.tng.app"})], source_path=_SRC)
    fine = TestSpec(id="A-3", title="c", reset="clear_data", steps=[normalize_step({"launch": "ca.tng.app"})], source_path=_SRC)
    problems = installed_app_problems([no_app, reinstall, fine], {}, env_cfg, "android")
    assert len(problems) == 2
    assert problems[0].startswith("A-1: no app to open")
    assert problems[1].startswith("A-2: Reset 'reinstall' needs the app file")
    # clear_data on iOS uninstalls, so it needs the file there.
    ios_fine = TestSpec(id="A-4", title="d", reset="clear_data", platforms=["ios"], steps=[normalize_step({"launch": "ca.tng.app"})], source_path=_SRC)
    assert installed_app_problems([ios_fine], {}, env_cfg, "ios")[0].startswith("A-4: Reset 'clear_data'")


# ---------------- recorder "only if shown" ----------------


def test_optional_recorded_steps_become_conditional() -> None:
    tap = {"action": "tap", "target": "popup.allow", "value": "", "optional": True}
    assert _step_to_yaml_dict(tap) == {"tap_if_visible": "popup.allow"}
    typed = {"action": "type", "target": "popup.name", "value": "x", "optional": True}
    assert _step_to_yaml_dict(typed) == {
        "if_visible": {"target": "popup.name", "then": [{"type": {"target": "popup.name", "value": "x"}}]}
    }
    assert _step_to_yaml_dict({"action": "tap", "target": "popup.allow", "value": ""}) == {"tap": "popup.allow"}


def test_permission_dialog_buttons_are_detected() -> None:
    el = {"recommended_value": "com.android.permissioncontroller:id/permission_allow_button"}
    assert is_system_dialog_element(el)
    assert is_system_dialog_element({"recommended_value": "Allow", "xml_snippet": 'package="com.google.android.permissioncontroller"'})
    assert not is_system_dialog_element({"recommended_value": "ca.tng.app:id/continue"})
