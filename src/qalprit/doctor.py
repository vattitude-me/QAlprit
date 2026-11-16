"""OS-aware environment diagnostics for qalprit doctor."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from qalprit import __version__
from qalprit.paths import (
    DEFAULT_AVD_NAME,
    get_android_user_home,
    get_legacy_dot_qalprit_dir,
    get_legacy_workspace_dir,
    get_os_app_data_root,
    get_qalprit_avd_home,
    get_qalprit_data_dir,
    get_settings_path,
    get_standard_workspace_dir,
    get_user_android_sdk_dir,
    load_user_settings,
    save_user_settings,
)


def win_subprocess_kwargs() -> dict[str, Any]:
    """Return Windows creationflags preventing stray console popups from GUI builds."""
    if platform.system() == "Windows":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            return {"creationflags": flags}
    return {}


def find_bundled_runtime_dir() -> Path | None:
    """Locate optional bundled desktop runtime directory (`runtime/`)."""
    candidates: list[Path] = []
    env_rt = os.environ.get("QALPRIT_RUNTIME_DIR")
    if env_rt:
        candidates.append(Path(env_rt))
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir / "runtime", exe_dir.parent / "Resources" / "runtime"])
    else:
        candidates.append(Path(__file__).resolve().parents[2] / "runtime")
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def find_node_bin() -> str | None:
    """Locate Node.js executable, preferring bundled runtime if present."""
    exe = "node.exe" if platform.system() == "Windows" else "node"
    rt = find_bundled_runtime_dir()
    if rt is not None:
        for cand in (rt / "node" / exe, rt / "node" / "bin" / exe):
            if cand.is_file():
                return str(cand)
    return shutil.which("node")


def _is_inside_macos_app_bundle(path: Path) -> bool:
    """Return True if path resides inside a macOS *.app/Contents bundle."""
    parts = path.parts
    return any(
        part.endswith(".app") and idx + 1 < len(parts) and parts[idx + 1] == "Contents"
        for idx, part in enumerate(parts)
    )


def ensure_portable_appium_home(appium_home: Path) -> Path:
    """Rewrite bundled Appium extensions.yaml installPath entries without breaking .app seals."""
    ext_rel = Path("node_modules") / ".cache" / "appium" / "extensions.yaml"
    ext_file = appium_home / ext_rel
    if not ext_file.is_file():
        return appium_home

    try:
        import yaml

        data = yaml.safe_load(ext_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return appium_home

    if not isinstance(data, dict):
        return appium_home

    changed = False
    resolved_mods = (appium_home / "node_modules").resolve()
    for section in ("drivers", "plugins"):
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            pkg_name = str(entry.get("pkgName") or "").strip()
            if not pkg_name:
                continue
            local_pkg = resolved_mods / pkg_name
            expected = str(local_pkg)
            if local_pkg.is_dir() and str(entry.get("installPath") or "") != expected:
                entry["installPath"] = expected
                changed = True

    in_app_bundle = _is_inside_macos_app_bundle(appium_home)
    if not changed and not in_app_bundle:
        return appium_home

    rendered = yaml.safe_dump(data, sort_keys=False)
    if not in_app_bundle:
        try:
            ext_file.write_text(rendered, encoding="utf-8")
            return appium_home
        except OSError:
            pass

    fallback_home = get_qalprit_data_dir() / "appium_home"
    fallback_mods = fallback_home / "node_modules"
    fallback_ext = fallback_home / ext_rel
    try:
        fallback_mods.mkdir(parents=True, exist_ok=True)
        for item in resolved_mods.iterdir():
            if item.name == ".cache":
                continue
            target = fallback_mods / item.name
            resolved_item = item.resolve()
            if target.is_symlink():
                try:
                    if target.resolve() != resolved_item:
                        target.unlink()
                except OSError:
                    target.unlink()
            if not target.exists() and not target.is_symlink():
                target.symlink_to(resolved_item, target_is_directory=item.is_dir())
        if not fallback_ext.is_file() or fallback_ext.read_text(encoding="utf-8") != rendered:
            fallback_ext.parent.mkdir(parents=True, exist_ok=True)
            fallback_ext.write_text(rendered, encoding="utf-8")
        return fallback_home
    except Exception:
        return appium_home


def find_appium_home() -> Path | None:
    """Locate bundled APPIUM_HOME containing pre-installed Appium drivers."""
    rt = find_bundled_runtime_dir()
    if rt is not None:
        cand = rt / "appium_home"
        if cand.is_dir():
            return ensure_portable_appium_home(cand)
    return None


def find_appium_entry() -> tuple[list[str] | None, str]:
    """Return command prefix to invoke Appium 2 and display path/label."""
    rt = find_bundled_runtime_dir()
    node_bin = find_node_bin()
    if rt is not None and node_bin:
        for rel in (
            rt / "appium" / "node_modules" / "appium" / "build" / "lib" / "main.js",
            rt / "node" / "node_modules" / "appium" / "build" / "lib" / "main.js",
        ):
            if rel.is_file():
                return [node_bin, str(rel)], str(rel)
    appium_bin = shutil.which("appium")
    if appium_bin:
        return [appium_bin], appium_bin
    npx_bin = shutil.which("npx")
    if npx_bin:
        return [npx_bin, "--no-install", "appium"], f"{npx_bin} --no-install appium"
    return None, ""


@dataclass
class CheckItem:
    name: str
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str
    remediation: str = ""


def _is_reparse_or_symlink(path: Path) -> bool:
    """Inspect path with lstat (without traversing) to detect symlinks or Windows junctions."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _unlink_reparse_or_symlink(path: Path) -> None:
    """Safely remove a symlink or NTFS junction entry without deleting target files."""
    if not _is_reparse_or_symlink(path):
        return
    try:
        st = os.lstat(path)
    except OSError:
        return
    if platform.system() == "Windows" and stat.S_ISDIR(st.st_mode):
        try:
            os.rmdir(path)
            return
        except OSError:
            pass
    try:
        path.unlink()
    except OSError:
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(path)


def _link_or_copy_dir(src: Path, dst: Path, *, force_refresh: bool = False) -> None:
    """Expose bundled read-only SDK subfolders inside user-writable Android SDK."""
    if not src.exists():
        return

    # Heal any NTFS directory junctions (which raise WinError 448 on Windows traversal)
    # and refresh stale/dangling POSIX symlinks when bundled app paths change on upgrade.
    if _is_reparse_or_symlink(dst):
        if platform.system() != "Windows":
            try:
                if dst.resolve() == src.resolve():
                    return
            except OSError:
                pass
        _unlink_reparse_or_symlink(dst)
    elif os.path.lexists(dst) and not force_refresh:
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        # Ensure locked adb.exe daemon does not block refreshing platform-tools DLLs.
        if force_refresh and dst.name == "platform-tools":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "adb.exe"],
                    capture_output=True,
                    timeout=5,
                    **win_subprocess_kwargs(),
                )
            except Exception:
                pass
        # Copy real directories on Windows so Python & Java tools never hit
        # WinError 448 ("untrusted mount point") on NTFS junctions.
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        try:
            dst.symlink_to(src)
        except OSError:
            shutil.copytree(src, dst, dirs_exist_ok=True)


def _sync_bundled_sdk_into(user_sdk: Path) -> None:
    rt = find_bundled_runtime_dir()
    if rt is None:
        return
    marker_file = user_sdk / ".qalprit_sdk_src"
    for cand_root in (rt / "android-sdk", rt / "android"):
        if not cand_root.exists():
            continue
        resolved_src = f"{cand_root.resolve()}|{__version__}"
        prev_src = ""
        try:
            if marker_file.is_file():
                prev_src = marker_file.read_text(encoding="utf-8").strip()
        except OSError:
            prev_src = ""
        source_changed = bool(prev_src != resolved_src)
        pt_dst = user_sdk / "platform-tools"
        pt_broken = (
            platform.system() == "Windows"
            and (pt_dst / "adb.exe").exists()
            and not (pt_dst / "AdbWinApi.dll").is_file()
        )
        for sub in ("platform-tools", "build-tools", "cmdline-tools"):
            src_sub = cand_root / sub
            if src_sub.exists():
                _link_or_copy_dir(
                    src_sub,
                    user_sdk / sub,
                    force_refresh=source_changed or (sub == "platform-tools" and pt_broken),
                )
        try:
            marker_file.write_text(resolved_src, encoding="utf-8")
        except OSError:
            pass
        break


def _system_android_sdk_candidates() -> list[Path]:
    """Return ordered candidate directories where a pre-existing Android SDK may live."""
    candidates: list[Path] = []
    for env_key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(env_key, "").strip()
        if val:
            candidates.append(Path(val).expanduser())

    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        candidates.extend(
            [
                home / "Library" / "Android" / "sdk",
                Path("/opt/homebrew/share/android-commandlinetools"),
                Path("/usr/local/share/android-commandlinetools"),
            ]
        )
    elif system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            candidates.append(Path(local_app) / "Android" / "Sdk")
        candidates.append(home / "AppData" / "Local" / "Android" / "Sdk")
    else:
        candidates.extend([home / "Android" / "Sdk", Path("/opt/android-sdk")])

    unique: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _is_healthy_sdk_tool(cand: Path) -> bool:
    """Return True when tool binary exists and required side-by-side Windows DLLs are intact."""
    try:
        if not cand.exists():
            return False
    except OSError:
        return False
    if platform.system() == "Windows" and cand.name.lower() == "adb.exe":
        return (cand.parent / "AdbWinApi.dll").is_file()
    return True


def _sdk_has_complete_emulator(path: Path) -> bool:
    """Return True when an SDK contains emulator, at least one system image, and healthy tools."""
    if not (path / "emulator").is_dir():
        return False
    sys_img_dir = path / "system-images"
    if not sys_img_dir.is_dir() or not any(sys_img_dir.rglob("source.properties")):
        return False
    if platform.system() == "Windows":
        adb_cand = path / "platform-tools" / "adb.exe"
        if adb_cand.exists() and not (path / "platform-tools" / "AdbWinApi.dll").is_file():
            return False
    return True


def find_system_android_sdk(*, require_emulator: bool = False) -> Path | None:
    """Locate a pre-existing non-bundled Android SDK (e.g. Android Studio SDK)."""
    rt = find_bundled_runtime_dir()
    rt_resolved = rt.resolve() if rt is not None else None
    for path in _system_android_sdk_candidates():
        if not path.is_dir():
            continue
        try:
            if rt_resolved and path.resolve().is_relative_to(rt_resolved):
                continue
        except OSError:
            pass
        if require_emulator and not _sdk_has_complete_emulator(path):
            continue
        return path
    return None


def prepare_writable_android_sdk() -> Path:
    """Ensure QAlprit's writable Android SDK directory exists with links to bundled SDK tools."""
    user_sdk = get_user_android_sdk_dir()
    user_sdk.mkdir(parents=True, exist_ok=True)
    _sync_bundled_sdk_into(user_sdk)
    return user_sdk


def find_android_sdk() -> Path | None:
    """Locate Android SDK directory across QAlprit storage, bundled runtime, and system SDKs."""
    settings = load_user_settings()
    if settings.get("prefer_existing_system_sdk"):
        complete_sys = find_system_android_sdk(require_emulator=True)
        if complete_sys is not None:
            return complete_sys

    user_sdk = get_user_android_sdk_dir()
    if user_sdk.exists() and (
        (user_sdk / "emulator").is_dir() or (user_sdk / "system-images").is_dir()
    ):
        _sync_bundled_sdk_into(user_sdk)
        return user_sdk

    if settings.get("prefer_existing_system_sdk"):
        any_sys = find_system_android_sdk(require_emulator=False)
        if any_sys is not None:
            return any_sys

    candidates: list[Path] = []
    rt = find_bundled_runtime_dir()
    if rt is not None:
        candidates.extend([rt / "android-sdk", rt / "android"])
    candidates.extend(_system_android_sdk_candidates())

    for path in candidates:
        if path.exists():
            return path
    return None


def find_sdk_for_avd(avd_name: str = DEFAULT_AVD_NAME) -> Path | None:
    """Resolve the Android SDK directory containing the given AVD's image.sysdir.1."""
    ini_path = get_qalprit_avd_home(avd_name) / f"{avd_name}.avd" / "config.ini"
    sysdir_rel = ""
    if ini_path.is_file():
        try:
            for line in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().lower().startswith("image.sysdir.1="):
                    raw_val = line.split("=", 1)[1].strip().replace("\\", "/").strip("/")
                    if raw_val:
                        sysdir_rel = raw_val
                    break
        except OSError:
            sysdir_rel = ""

    if sysdir_rel:
        candidates: list[Path] = [
            get_user_android_sdk_dir(),
            get_qalprit_data_dir() / "android-sdk",
            get_legacy_dot_qalprit_dir() / "android-sdk",
        ]
        active = find_android_sdk()
        if active is not None:
            candidates.insert(1, active)
        candidates.extend(_system_android_sdk_candidates())

        seen: set[str] = set()
        for cand in candidates:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            if (cand / sysdir_rel).is_dir():
                if cand == get_user_android_sdk_dir():
                    _sync_bundled_sdk_into(cand)
                return cand
    return find_android_sdk()


def _cmdline_tools_bin_dirs(sdk: Path) -> list[Path]:
    """Return cmdline-tools bin directories ordered with 'latest' first, then versioned folders descending."""
    ct_root = sdk / "cmdline-tools"
    if not ct_root.is_dir():
        return []
    dirs: list[Path] = []
    latest_bin = ct_root / "latest" / "bin"
    if latest_bin.is_dir():
        dirs.append(latest_bin)
    try:
        versioned = sorted(
            (p / "bin" for p in ct_root.iterdir() if p.is_dir() and p.name != "latest" and (p / "bin").is_dir()),
            reverse=True,
        )
        dirs.extend(versioned)
    except OSError:
        pass
    return dirs


def _tool_in_dirs(dirs: list[Path], names: list[str]) -> str | None:
    for directory in dirs:
        for fname in names:
            cand = directory / fname
            if _is_healthy_sdk_tool(cand):
                return str(cand)
    return None


def _tool_binary_names(name: str) -> list[str]:
    return [f"{name}.exe", f"{name}.bat", f"{name}.cmd", name] if platform.system() == "Windows" else [name]


def find_cmdline_tool(name: str, preferred_sdk: Path | None = None) -> str | None:
    """Resolve sdkmanager/avdmanager preferring modern cmdline-tools over deprecated legacy tools/bin."""
    names = _tool_binary_names(name)
    candidates: list[Path] = []
    if preferred_sdk is not None:
        candidates.extend(_cmdline_tools_bin_dirs(preferred_sdk))
    candidates.extend(_cmdline_tools_bin_dirs(get_user_android_sdk_dir()))
    rt = find_bundled_runtime_dir()
    if rt is not None:
        for sub in ("android-sdk", "android"):
            candidates.extend(_cmdline_tools_bin_dirs(rt / sub))
    found = _tool_in_dirs(candidates, names)
    if found:
        return found
    return find_android_tool(name)


def find_android_tool(name: str) -> str | None:
    """Resolve an Android SDK binary (adb, aapt2, sdkmanager, avdmanager, emulator)."""
    sdk = find_android_sdk()
    names = _tool_binary_names(name)
    is_cli_java_tool = name in {"sdkmanager", "avdmanager"}
    if sdk:
        rel_dirs = [
            sdk / "platform-tools",
            sdk / "emulator",
            *_cmdline_tools_bin_dirs(sdk),
        ]
        bt_dir = sdk / "build-tools"
        try:
            if bt_dir.is_dir():
                rel_dirs.extend(sorted((p for p in bt_dir.iterdir() if p.is_dir()), reverse=True))
        except OSError:
            pass
        found = _tool_in_dirs(rel_dirs, names)
        if found:
            return found

    if is_cli_java_tool:
        # Never fall back to deprecated pre-JDK11 sdk/tools/bin when QAlprit ships modern cmdline-tools.
        modern_dirs: list[Path] = list(_cmdline_tools_bin_dirs(get_user_android_sdk_dir()))
        rt = find_bundled_runtime_dir()
        if rt is not None:
            for sub in ("android-sdk", "android"):
                modern_dirs.extend(_cmdline_tools_bin_dirs(rt / sub))
        modern = _tool_in_dirs(modern_dirs, names)
        if modern:
            return modern

    if not is_cli_java_tool:
        # Fall back to QAlprit writable/bundled tools if system SDK has missing DLLs (e.g. AdbWinApi.dll).
        fallback_dirs: list[Path] = [
            get_user_android_sdk_dir() / "platform-tools",
            get_user_android_sdk_dir() / "emulator",
        ]
        rt = find_bundled_runtime_dir()
        if rt is not None:
            for sub in ("android-sdk", "android"):
                fallback_dirs.extend([rt / sub / "platform-tools", rt / sub / "emulator"])
        fallback = _tool_in_dirs(fallback_dirs, names)
        if fallback:
            return fallback

    if sdk:
        legacy = _tool_in_dirs([sdk / "tools" / "bin"], names)
        if legacy:
            return legacy

    direct = shutil.which(name)
    if direct and _is_healthy_sdk_tool(Path(direct)):
        return direct
    return None


def find_java_bin() -> str | None:
    """Locate java binary, checking bundled runtime, PATH, JAVA_HOME, and Homebrew OpenJDK."""
    java_exe = "java.exe" if platform.system() == "Windows" else "java"
    rt = find_bundled_runtime_dir()
    if rt is not None:
        cand = rt / "jdk" / "bin" / java_exe
        if cand.exists():
            return str(cand)
    java_home = os.environ.get("JAVA_HOME")
    if java_home and java_home.rstrip("/\\") != "/usr":
        cand = Path(java_home) / "bin" / java_exe
        if cand.exists():
            return str(cand)

    direct = shutil.which("java")
    if direct and not (platform.system() == "Darwin" and direct == "/usr/bin/java"):
        return direct

    if platform.system() == "Darwin":
        # Resolve the real JDK Home rather than Apple's /usr/bin/java stub so
        # callers deriving JAVA_HOME from bin/java never set JAVA_HOME=/usr.
        try:
            jh = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True, timeout=5)
            if jh.returncode == 0:
                home_dir = Path((jh.stdout or "").strip())
                cand = home_dir / "bin" / "java"
                if cand.exists():
                    return str(cand)
        except Exception:
            pass
        for cand in (
            Path("/opt/homebrew/opt/openjdk@17/bin/java"),
            Path("/opt/homebrew/opt/openjdk@21/bin/java"),
            Path("/opt/homebrew/opt/openjdk/bin/java"),
        ):
            if cand.exists():
                return str(cand)
        if direct == "/usr/bin/java":
            probe = subprocess.run([direct, "-version"], capture_output=True, text=True)
            if probe.returncode == 0:
                return direct
    return None


def _run_quick(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            **win_subprocess_kwargs(),
        )
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, output.strip()
    except Exception as exc:
        return 1, str(exc)


def _macos_xcode_env() -> dict[str, str]:
    env = dict(os.environ)
    xcode_dev = Path("/Applications/Xcode.app/Contents/Developer")
    if xcode_dev.exists():
        # Prefer full Xcode if xcode-select points to CommandLineTools
        rc, sel = _run_quick(["xcode-select", "-p"])
        if rc != 0 or "CommandLineTools" in sel:
            env["DEVELOPER_DIR"] = str(xcode_dev)
    return env


def run_doctor(python_min: tuple[int, int] = (3, 10)) -> list[CheckItem]:
    """Run OS-aware prerequisite checks for qalprit."""
    checks: list[CheckItem] = []
    system = platform.system()

    # 1. Python
    import sys

    py_ver = sys.version_info
    if (py_ver.major, py_ver.minor) >= python_min:
        checks.append(CheckItem("Python", "ok", f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"))
    else:
        checks.append(
            CheckItem(
                "Python",
                "fail",
                f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
                f"Upgrade to Python {python_min[0]}.{python_min[1]}+",
            )
        )

    # 1b. Python Appium client + package metadata (critical for frozen PyInstaller bundles)
    try:
        from appium import webdriver as _ap_webdriver  # noqa: F401
        from appium.options.android import UiAutomator2Options as _Uia2Opts  # noqa: F401
        from appium.version import version as _ap_py_ver

        checks.append(CheckItem("Appium Python client", "ok", f"v{_ap_py_ver}"))
    except Exception as exc:
        checks.append(
            CheckItem(
                "Appium Python client",
                "fail",
                str(exc),
                "Install or bundle Appium-Python-Client package metadata",
            )
        )

    # 2. Node.js
    node_bin = find_node_bin()
    if node_bin:
        rc, out = _run_quick([node_bin, "--version"])
        checks.append(CheckItem("Node.js", "ok" if rc == 0 else "warn", f"{out or node_bin} ({node_bin})"))
    else:
        checks.append(
            CheckItem("Node.js", "fail", "Not found", "Install Node.js LTS to run Appium 2")
        )

    # 3. Appium 2 + drivers
    appium_cmd, appium_label = find_appium_entry()
    app_env = dict(os.environ)
    appium_home = find_appium_home()
    if appium_home is not None:
        app_env.setdefault("APPIUM_HOME", str(appium_home))
    if appium_cmd:
        rc, ver_out = _run_quick(appium_cmd + ["--version"], env=app_env, timeout=25.0)
        ver_str = ver_out.splitlines()[0] if ver_out else "unknown"
        major = 0
        m = re.match(r"(\d+)\.", ver_str.strip())
        if m:
            major = int(m.group(1))
        status: Literal["ok", "warn", "fail"] = "ok" if (rc == 0 and major >= 2) else "warn"
        checks.append(
            CheckItem(
                "Appium",
                status,
                f"v{ver_str} ({appium_label})",
                "" if major >= 2 else "Upgrade to Appium 2: npm install -g appium",
            )
        )

        # Installed drivers
        rc_d, drv_out = _run_quick(
            appium_cmd + ["driver", "list", "--installed", "--json"],
            env=app_env,
            timeout=25.0,
        )
        installed_drivers: set[str] = set()
        if rc_d == 0 and drv_out:
            try:
                parsed = json.loads(drv_out)
                if isinstance(parsed, dict):
                    installed_drivers = set(parsed.keys())
            except Exception:
                pass
        if "uiautomator2" in installed_drivers:
            checks.append(CheckItem("Appium driver: uiautomator2", "ok", "Installed"))
        else:
            checks.append(
                CheckItem(
                    "Appium driver: uiautomator2",
                    "fail",
                    "Not installed",
                    "Run: appium driver install uiautomator2",
                )
            )
        if system == "Darwin":
            if "xcuitest" in installed_drivers:
                checks.append(CheckItem("Appium driver: xcuitest", "ok", "Installed"))
            else:
                checks.append(
                    CheckItem(
                        "Appium driver: xcuitest",
                        "warn",
                        "Not installed",
                        "Run: appium driver install xcuitest",
                    )
                )
        else:
            checks.append(
                CheckItem("Appium driver: xcuitest", "skip", f"iOS unsupported on {system}")
            )
    else:
        checks.append(
            CheckItem(
                "Appium",
                "fail",
                "Not found",
                "Install Appium 2: npm install -g appium && appium driver install uiautomator2",
            )
        )

    # 4. Java (JDK 17+)
    java_bin = find_java_bin()
    if java_bin:
        rc, j_out = _run_quick([java_bin, "-version"])
        first_line = j_out.splitlines()[0] if j_out else java_bin
        m = re.search(r'"(\d+)', first_line)
        j_major = int(m.group(1)) if m else 0
        if rc == 0 and j_major >= 17:
            checks.append(CheckItem("Java (JDK)", "ok", f"{first_line} ({java_bin})"))
        else:
            checks.append(
                CheckItem(
                    "Java (JDK)",
                    "warn",
                    f"{first_line} ({java_bin})",
                    "JDK 17+ recommended for Android tooling",
                )
            )
    else:
        checks.append(
            CheckItem("Java (JDK)", "fail", "Not found", "Install JDK 17+ and set JAVA_HOME")
        )

    # 5. Android SDK & adb / emulator
    sdk = find_android_sdk()
    if sdk:
        checks.append(CheckItem("Android SDK", "ok", str(sdk)))
    else:
        checks.append(
            CheckItem(
                "Android SDK",
                "fail",
                "Not found",
                "Install Android SDK command-line tools and set ANDROID_HOME",
            )
        )

    adb_bin = find_android_tool("adb")
    if adb_bin:
        rc, adb_out = _run_quick([adb_bin, "version"])
        ver_line = adb_out.splitlines()[0] if adb_out else adb_bin
        checks.append(CheckItem("Android adb", "ok", f"{ver_line} ({adb_bin})"))
    else:
        checks.append(
            CheckItem("Android adb", "fail", "Not found", "Install Android platform-tools")
        )

    aapt2_bin = find_android_tool("aapt2")
    if aapt2_bin:
        checks.append(CheckItem("Android build-tools (aapt2)", "ok", aapt2_bin))
    else:
        checks.append(
            CheckItem(
                "Android build-tools (aapt2)",
                "warn",
                "Not found",
                "Install Android SDK build-tools for APK inspection with Appium",
            )
        )

    sdkmgr_bin = find_android_tool("sdkmanager")
    if sdkmgr_bin:
        checks.append(CheckItem("Android cmdline-tools (sdkmanager)", "ok", sdkmgr_bin))
    else:
        checks.append(
            CheckItem(
                "Android cmdline-tools (sdkmanager)",
                "warn",
                "Not found",
                "Required for 1-click Virtual Android Emulator setup",
            )
        )

    emu_bin = find_android_tool("emulator")
    if emu_bin:
        emu_env = dict(os.environ)
        emu_env.setdefault("ANDROID_AVD_HOME", str(get_qalprit_avd_home()))
        emu_env.setdefault("ANDROID_USER_HOME", str(get_android_user_home()))
        rc, avd_out = _run_quick([emu_bin, "-list-avds"], env=emu_env)
        avds = [line.strip() for line in avd_out.splitlines() if line.strip()] if rc == 0 else []
        detail = f"{len(avds)} AVD(s): {', '.join(avds)}" if avds else "No AVDs found — run Emulator Setup"
        checks.append(CheckItem("Android emulator", "ok" if avds else "warn", detail))
    else:
        checks.append(
            CheckItem(
                "Android emulator",
                "warn",
                "Not installed yet — click Setup Virtual Android Emulator in Step 1A (primary path)",
                "Click 'Setup Virtual Android Emulator' in Step 1A or run: qalprit emulator setup",
            )
        )

    # 6. Xcode & iOS Simulator (macOS only)
    if system == "Darwin":
        xcode_app = Path("/Applications/Xcode.app")
        rc_sel, sel_out = _run_quick(["xcode-select", "-p"])
        if xcode_app.exists():
            xc_env = _macos_xcode_env()
            rc_sim, sim_out = _run_quick(["xcrun", "simctl", "list", "devices", "available"], env=xc_env)
            if rc_sim == 0:
                sim_count = sum(1 for ln in sim_out.splitlines() if "(" in ln and ")" in ln and "--" not in ln)
                note = "" if "Xcode.app" in sel_out else " (using DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer)"
                checks.append(
                    CheckItem("Xcode / iOS Simulator", "ok", f"{sim_count} available simulator(s){note}")
                )
            elif "license" in sim_out.lower():
                checks.append(
                    CheckItem(
                        "Xcode / iOS Simulator",
                        "fail",
                        "Xcode license agreement not accepted",
                        "Run: sudo xcodebuild -license accept && sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                    )
                )
            else:
                checks.append(
                    CheckItem(
                        "Xcode / iOS Simulator",
                        "warn",
                        sim_out.splitlines()[0] if sim_out else "simctl unavailable",
                        "Run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer",
                    )
                )
        else:
            checks.append(
                CheckItem(
                    "Xcode / iOS Simulator",
                    "fail",
                    "Xcode.app not installed",
                    "Install Xcode from the App Store to run iOS tests",
                )
            )
    else:
        checks.append(
            CheckItem("Xcode / iOS Simulator", "skip", f"Skipped on {system} (iOS requires macOS)")
        )

    return checks


def _is_inside(path: Path | str | None, root: Path | None) -> bool:
    if not path or root is None:
        return False
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def discover_system_software(workspace_root: Path | None = None) -> dict[str, Any]:
    """Scan system-installed vs QAlprit-bundled runtimes, SDKs, AVDs, and storage paths."""
    system = platform.system()
    rt = find_bundled_runtime_dir()
    settings = load_user_settings()
    data_dir = get_qalprit_data_dir()
    sdk_dir = get_user_android_sdk_dir()
    avd_dir = get_qalprit_avd_home()
    legacy_dot = get_legacy_dot_qalprit_dir()
    legacy_ws = get_legacy_workspace_dir()

    # 1. Node.js
    sys_node = shutil.which("node")
    if sys_node and _is_inside(sys_node, rt):
        sys_node = None
    if not sys_node and system == "Windows":
        for cand in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
        ):
            if cand.is_file():
                sys_node = str(cand)
                break
    bundled_node = find_node_bin()
    node_path = sys_node or bundled_node
    node_ver = ""
    if node_path:
        rc, out = _run_quick([node_path, "--version"], timeout=5.0)
        if rc == 0 and out:
            node_ver = out.splitlines()[0].strip()

    # 2. Python (system)
    sys_python = None
    for cmd_name in ("python3", "python", "py"):
        cand = shutil.which(cmd_name)
        if cand and not _is_inside(cand, Path(sys.executable).parent if getattr(sys, "frozen", False) else None):
            sys_python = cand
            break
    py_ver = platform.python_version() if not getattr(sys, "frozen", False) else ""
    if sys_python:
        rc, out = _run_quick([sys_python, "--version"], timeout=5.0)
        if rc == 0 and out:
            py_ver = out.splitlines()[0].strip()

    # 3. Java / JDK
    sys_java = None
    jh = os.environ.get("JAVA_HOME", "").strip()
    java_exe = "java.exe" if system == "Windows" else "java"
    if jh and jh.rstrip("/\\") != "/usr":
        cand = Path(jh) / "bin" / java_exe
        if cand.is_file() and not _is_inside(cand, rt):
            sys_java = str(cand)
    if not sys_java:
        which_j = shutil.which("java")
        if which_j and not _is_inside(which_j, rt) and not (system == "Darwin" and which_j == "/usr/bin/java"):
            sys_java = which_j
    bundled_java = find_java_bin()
    java_path = sys_java or bundled_java
    java_ver = ""
    if java_path:
        rc, out = _run_quick([java_path, "-version"], timeout=5.0)
        if out:
            java_ver = out.splitlines()[0].strip()

    # 4. Appium
    sys_appium = shutil.which("appium")
    if sys_appium and _is_inside(sys_appium, rt):
        sys_appium = None
    appium_cmd, appium_label = find_appium_entry()

    # 5. Android SDK (system vs QAlprit-managed)
    sys_sdk = find_system_android_sdk()
    active_sdk = find_android_sdk()
    avd_candidates: list[Path] = [avd_dir, data_dir / "avd", Path.home() / ".android" / "avd"]
    found_avds: list[dict[str, str]] = []
    seen_avds: set[str] = set()
    for avd_root in avd_candidates:
        if not avd_root.is_dir():
            continue
        try:
            for item in sorted(avd_root.iterdir()):
                if item.is_dir() and item.name.endswith(".avd") and (item / "config.ini").is_file():
                    name = item.name[:-4]
                    if name not in seen_avds:
                        seen_avds.add(name)
                        found_avds.append({"name": name, "path": str(item.resolve())})
        except OSError:
            continue

    return {
        "os": system,
        "paths": {
            "data_dir": str(data_dir),
            "sdk_dir": str(sdk_dir),
            "avd_dir": str(avd_dir),
            "android_user_home": str(get_android_user_home()),
            "workspace_dir": str(workspace_root.resolve()) if workspace_root else str(get_standard_workspace_dir()),
            "os_default_data_dir": str(get_os_app_data_root()),
            "os_default_workspace_dir": str(get_standard_workspace_dir()),
            "settings_path": str(get_settings_path()) if "get_settings_path" in globals() else "",
            "prefer_existing_system_sdk": bool(settings.get("prefer_existing_system_sdk", False)),
            "legacy_dot_qalprit_exists": legacy_dot.is_dir(),
            "legacy_dot_qalprit_path": str(legacy_dot),
            "legacy_workspace_exists": legacy_ws.is_dir(),
            "legacy_workspace_path": str(legacy_ws),
        },
        "software": [
            {
                "id": "node",
                "name": "Node.js",
                "source": "system" if sys_node else ("bundled" if bundled_node else "missing"),
                "found": bool(node_path),
                "path": node_path or "",
                "version": node_ver,
            },
            {
                "id": "python",
                "name": "Python",
                "source": "system" if sys_python else ("bundled" if getattr(sys, "frozen", False) else "system"),
                "found": bool(sys_python or py_ver),
                "path": sys_python or sys.executable,
                "version": py_ver,
            },
            {
                "id": "java",
                "name": "Java JDK",
                "source": "system" if sys_java else ("bundled" if bundled_java else "missing"),
                "found": bool(java_path),
                "path": java_path or "",
                "version": java_ver,
            },
            {
                "id": "appium",
                "name": "Appium 2",
                "source": "system" if sys_appium else ("bundled" if appium_cmd else "missing"),
                "found": bool(sys_appium or appium_cmd),
                "path": sys_appium or appium_label,
                "version": "",
            },
            {
                "id": "android_sdk",
                "name": "Android SDK & Build Tools",
                "source": (
                    "system"
                    if sys_sdk and active_sdk == sys_sdk
                    else ("system_available" if sys_sdk else ("qalprit" if active_sdk else "missing"))
                ),
                "found": bool(active_sdk or sys_sdk),
                "path": str(active_sdk or sys_sdk or ""),
                "system_sdk_path": str(sys_sdk) if sys_sdk else "",
                "system_sdk_has_emulator": bool(sys_sdk and (sys_sdk / "emulator").is_dir() and (sys_sdk / "system-images").is_dir()),
            },
            {
                "id": "android_avd",
                "name": "Android Virtual Devices (AVDs)",
                "source": "found" if found_avds else "missing",
                "found": bool(found_avds),
                "path": str(avd_dir),
                "version": ", ".join(a["name"] for a in found_avds),
                "avds": found_avds,
            },
        ],
    }
