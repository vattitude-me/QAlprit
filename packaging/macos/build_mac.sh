#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [ -z "${VERSION}" ]; then
  VERSION="$(grep -E '^version\s*=' pyproject.toml | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')"
fi
export QALPRIT_VERSION="${VERSION}"

HOST_ARCH="$(uname -m)"
if [ "${HOST_ARCH}" = "arm64" ]; then
  NODE_ARCH="darwin-arm64"
  ADOPTIUM_ARCH="aarch64"
  DMG_ARCH="arm64"
else
  NODE_ARCH="darwin-x64"
  ADOPTIUM_ARCH="x64"
  DMG_ARCH="x64"
fi

if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

echo "==> Building QAlprit macOS app & DMG v${VERSION} (${HOST_ARCH}) using ${PYTHON_BIN}"

BUILD_WORK="${REPO_ROOT}/build/mac-runtime-${DMG_ARCH}"
ICON_DIR="${REPO_ROOT}/build/mac-icon"
DIST_APP="${REPO_ROOT}/dist/QAlprit.app"
DIST_MAC="${REPO_ROOT}/dist-mac"
DMG_STAGING="${REPO_ROOT}/build/dmg-staging-${DMG_ARCH}"
DMG_NAME="QAlprit-${VERSION}-macos-${DMG_ARCH}.dmg"
DMG_PATH="${DIST_MAC}/${DMG_NAME}"

mkdir -p "${BUILD_WORK}" "${ICON_DIR}" "${DIST_MAC}"

echo "==> Ensuring QAlprit desktop dependencies and PyInstaller are installed..."
if ! "${PYTHON_BIN}" -c "import PIL, PyInstaller, qalprit, webview, objc, WebKit" >/dev/null 2>&1; then
  if "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
    "${PYTHON_BIN}" -m pip install -e ".[desktop]" pyinstaller >/dev/null
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "${PYTHON_BIN}" -e ".[desktop]" pyinstaller >/dev/null
  else
    "${PYTHON_BIN}" -m ensurepip --upgrade >/dev/null
    "${PYTHON_BIN}" -m pip install -e ".[desktop]" pyinstaller >/dev/null
  fi
fi

# 0. Generate QAlprit.icns icon via Pillow + macOS iconutil
ICONSET_DIR="${ICON_DIR}/QAlprit.iconset"
ICNS_PATH="${ICON_DIR}/QAlprit.icns"
if [ ! -f "${ICNS_PATH}" ]; then
  echo "==> Generating macOS .icns app icon..."
  rm -rf "${ICONSET_DIR}"
  mkdir -p "${ICONSET_DIR}"
  "${PYTHON_BIN}" - "${ICONSET_DIR}" <<'PYEOF'
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])
S = 1024  # QAlprit mark: teal tile, white Q lens, mint check (matches src/qalprit/ui/qalprit-icon.svg)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for i in range(2 * S):
    t = i / (2 * S - 1)
    gd.line([(i, 0), (0, i)], fill=(round(13 + t * 6), round(148 - t * 70), round(136 - t * 62), 255), width=2)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([64, 64, 960, 960], radius=196, fill=255)
base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
base.paste(grad, (0, 0), mask)
draw = ImageDraw.Draw(base)
white, mint = (248, 250, 252, 255), (94, 234, 212, 255)


def stroke(points, color, width):
    draw.line(points, fill=color, width=width, joint="curve")
    r = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


draw.ellipse([229, 229, 739, 739], outline=white, width=76)
stroke([(645, 645), (771, 771)], white, 76)
stroke([(392, 492), (460, 561), (582, 420)], mint, 59)

for size in (16, 32, 64, 128, 256, 512):
    base.resize((size, size), Image.Resampling.LANCZOS).save(out / f"icon_{size}x{size}.png")
    base.resize((size * 2, size * 2), Image.Resampling.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")
PYEOF
  iconutil -c icns "${ICONSET_DIR}" -o "${ICNS_PATH}"
fi

# 1. Portable Node.js v20 LTS
NODE_VERSION="v20.18.1"
NODE_DEST="${BUILD_WORK}/node"
if [ ! -x "${NODE_DEST}/bin/node" ]; then
  echo "==> Downloading Node.js ${NODE_VERSION} (${NODE_ARCH})..."
  NODE_TGZ="${BUILD_WORK}/node-${NODE_VERSION}-${NODE_ARCH}.tar.gz"
  curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-${NODE_ARCH}.tar.gz" -o "${NODE_TGZ}"
  rm -rf "${BUILD_WORK}/node-extract" "${NODE_DEST}"
  mkdir -p "${BUILD_WORK}/node-extract"
  tar -xzf "${NODE_TGZ}" -C "${BUILD_WORK}/node-extract"
  INNER_NODE="$(find "${BUILD_WORK}/node-extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  mv "${INNER_NODE}" "${NODE_DEST}"
  rm -rf "${BUILD_WORK}/node-extract"
fi

NODE_BIN="${NODE_DEST}/bin/node"
NPM_BIN="${NODE_DEST}/bin/npm"

# 2. Bundle Appium 2.19.0 + UiAutomator2 4.2.9 + XCUITest 9.10.5
APPIUM_DIR="${BUILD_WORK}/appium"
APPIUM_HOME_DIR="${BUILD_WORK}/appium_home"
APPIUM_MAIN_JS="${APPIUM_DIR}/node_modules/appium/build/lib/main.js"
if [ ! -f "${APPIUM_MAIN_JS}" ]; then
  echo "==> Installing Appium 2.19.0 into bundled runtime..."
  mkdir -p "${APPIUM_DIR}" "${APPIUM_HOME_DIR}"
  (
    cd "${APPIUM_DIR}"
    PATH="${NODE_DEST}/bin:${PATH}" "${NPM_BIN}" init -y >/dev/null
    PATH="${NODE_DEST}/bin:${PATH}" "${NPM_BIN}" install --no-audit --no-fund appium@2.19.0
  )
fi

echo "==> Installing Appium drivers (uiautomator2 + xcuitest) into APPIUM_HOME..."
mkdir -p "${APPIUM_HOME_DIR}"
INSTALLED_JSON="$(APPIUM_HOME="${APPIUM_HOME_DIR}" PATH="${NODE_DEST}/bin:${PATH}" "${NODE_BIN}" "${APPIUM_MAIN_JS}" driver list --installed --json 2>/dev/null || true)"
if ! echo "${INSTALLED_JSON}" | grep -q '"uiautomator2"'; then
  APPIUM_HOME="${APPIUM_HOME_DIR}" PATH="${NODE_DEST}/bin:${PATH}" "${NODE_BIN}" "${APPIUM_MAIN_JS}" driver install uiautomator2@4.2.9
fi
if ! echo "${INSTALLED_JSON}" | grep -q '"xcuitest"'; then
  APPIUM_HOME="${APPIUM_HOME_DIR}" PATH="${NODE_DEST}/bin:${PATH}" "${NODE_BIN}" "${APPIUM_MAIN_JS}" driver install xcuitest@9.10.5
fi

# 3. Portable Eclipse Temurin JRE 17
JDK_DEST="${BUILD_WORK}/jdk"
if [ ! -x "${JDK_DEST}/bin/java" ]; then
  echo "==> Downloading Eclipse Temurin JRE 17 (mac/${ADOPTIUM_ARCH})..."
  JRE_TGZ="${BUILD_WORK}/temurin-jre17.tar.gz"
  curl -fsSL "https://api.adoptium.net/v3/binary/latest/17/ga/mac/${ADOPTIUM_ARCH}/jre/hotspot/normal/eclipse" -o "${JRE_TGZ}"
  rm -rf "${BUILD_WORK}/jre-extract" "${JDK_DEST}"
  mkdir -p "${BUILD_WORK}/jre-extract"
  tar -xzf "${JRE_TGZ}" -C "${BUILD_WORK}/jre-extract"
  INNER_JRE="$(find "${BUILD_WORK}/jre-extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  if [ -d "${INNER_JRE}/Contents/Home" ]; then
    mv "${INNER_JRE}/Contents/Home" "${JDK_DEST}"
  else
    mv "${INNER_JRE}" "${JDK_DEST}"
  fi
  rm -rf "${BUILD_WORK}/jre-extract"
fi

# 4. Android SDK platform-tools (adb) & build-tools (aapt2 / apksigner / zipalign)
ANDROID_SDK_DEST="${BUILD_WORK}/android-sdk"
if [ ! -x "${ANDROID_SDK_DEST}/platform-tools/adb" ]; then
  echo "==> Downloading Android SDK platform-tools (darwin)..."
  PT_ZIP="${BUILD_WORK}/platform-tools-latest-darwin.zip"
  curl -fsSL "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip" -o "${PT_ZIP}"
  rm -rf "${ANDROID_SDK_DEST}"
  mkdir -p "${ANDROID_SDK_DEST}"
  unzip -q -o "${PT_ZIP}" -d "${ANDROID_SDK_DEST}"
fi

BUILD_TOOLS_VER="35.0.1"
BUILD_TOOLS_DEST="${ANDROID_SDK_DEST}/build-tools/${BUILD_TOOLS_VER}"
if [ ! -x "${BUILD_TOOLS_DEST}/aapt2" ]; then
  echo "==> Downloading Android SDK build-tools ${BUILD_TOOLS_VER} (darwin)..."
  BT_ZIP="${BUILD_WORK}/build-tools_r${BUILD_TOOLS_VER}-macosx.zip"
  curl -fsSL "https://dl.google.com/android/repository/build-tools_r35.0.1_macosx.zip" -o "${BT_ZIP}"
  BT_EXTRACT="${BUILD_WORK}/bt-extract"
  rm -rf "${BT_EXTRACT}" "${BUILD_TOOLS_DEST}"
  mkdir -p "${BT_EXTRACT}" "$(dirname "${BUILD_TOOLS_DEST}")"
  unzip -q -o "${BT_ZIP}" -d "${BT_EXTRACT}"
  if [ -f "${BT_EXTRACT}/aapt2" ]; then
    mv "${BT_EXTRACT}" "${BUILD_TOOLS_DEST}"
  else
    INNER_BT="$(find "${BT_EXTRACT}" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    mv "${INNER_BT}" "${BUILD_TOOLS_DEST}"
    rm -rf "${BT_EXTRACT}"
  fi
  chmod +x "${BUILD_TOOLS_DEST}/aapt2" "${BUILD_TOOLS_DEST}/aapt" "${BUILD_TOOLS_DEST}/zipalign" 2>/dev/null || true
fi

CMD_TOOLS_DEST="${ANDROID_SDK_DEST}/cmdline-tools/latest"
if [ ! -x "${CMD_TOOLS_DEST}/bin/sdkmanager" ]; then
  echo "==> Downloading Android SDK cmdline-tools (darwin)..."
  CT_ZIP="${BUILD_WORK}/commandlinetools-mac-latest.zip"
  curl -fsSL "https://dl.google.com/android/repository/commandlinetools-mac_arm64-16111833_latest.zip" -o "${CT_ZIP}"
  CT_EXTRACT="${BUILD_WORK}/ct-extract"
  rm -rf "${CT_EXTRACT}" "${CMD_TOOLS_DEST}"
  mkdir -p "${CT_EXTRACT}" "$(dirname "${CMD_TOOLS_DEST}")"
  unzip -q -o "${CT_ZIP}" -d "${CT_EXTRACT}"
  if [ -d "${CT_EXTRACT}/cmdline-tools/bin" ]; then
    mv "${CT_EXTRACT}/cmdline-tools" "${CMD_TOOLS_DEST}"
  elif [ -d "${CT_EXTRACT}/bin" ]; then
    mv "${CT_EXTRACT}" "${CMD_TOOLS_DEST}"
  else
    INNER_CT="$(find "${CT_EXTRACT}" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    mv "${INNER_CT}" "${CMD_TOOLS_DEST}"
  fi
  rm -rf "${CT_EXTRACT}"
  chmod +x "${CMD_TOOLS_DEST}/bin/"* 2>/dev/null || true
fi

# 5. Build QAlprit.app with PyInstaller
rm -rf "${DIST_APP}" "${REPO_ROOT}/dist/QAlprit"
"${PYTHON_BIN}" -m PyInstaller --clean --noconfirm "${SCRIPT_DIR}/qalprit.spec"

# 6. Attach bundled runtime/ inside QAlprit.app/Contents/Resources/runtime/
RUNTIME_TARGET="${DIST_APP}/Contents/Resources/runtime"
rm -rf "${RUNTIME_TARGET}"
mkdir -p "${RUNTIME_TARGET}"
for folder in node appium appium_home jdk android-sdk; do
  echo "==> Copying runtime/${folder}..."
  ditto "${BUILD_WORK}/${folder}" "${RUNTIME_TARGET}/${folder}"
done

# 7. Ensure user-writable permissions on bundled runtime files (0444 breaks xattr -dr) and ad-hoc sign
echo "==> Ad-hoc code-signing QAlprit.app..."
chmod -R u+w "${RUNTIME_TARGET}"
codesign --force --sign - "${RUNTIME_TARGET}/node/bin/node" || true
codesign --force --sign - "${RUNTIME_TARGET}/android-sdk/platform-tools/adb" || true
codesign --force --sign - "${RUNTIME_TARGET}/android-sdk/build-tools/${BUILD_TOOLS_VER}/aapt2" || true
codesign --force --sign - "${RUNTIME_TARGET}/android-sdk/build-tools/${BUILD_TOOLS_VER}/aapt" || true
codesign --force --sign - "${RUNTIME_TARGET}/android-sdk/build-tools/${BUILD_TOOLS_VER}/zipalign" || true
codesign --force --deep --sign - "${DIST_APP}"

# 8. Smoke-test QAlprit.app strictly using its bundled runtime/ without mutating the .app seal
echo "==> Smoke-testing bundled QAlprit.app..."
QALPRIT_APP_BIN="${DIST_APP}/Contents/MacOS/QAlprit"
SMOKE_DATA_DIR="${BUILD_WORK}/smoke-data"
rm -rf "${SMOKE_DATA_DIR}"
mkdir -p "${SMOKE_DATA_DIR}"
VER_OUT="$("${QALPRIT_APP_BIN}" --version)"
echo "Version output: ${VER_OUT}"
DOC_JSON="$(env -i HOME="${HOME}" PATH="/usr/bin:/bin" QALPRIT_DATA_DIR="${SMOKE_DATA_DIR}" "${QALPRIT_APP_BIN}" doctor --json-output || true)"
echo "${DOC_JSON}"
echo "${DOC_JSON}" | "${PYTHON_BIN}" -c '
import json, sys
items = json.load(sys.stdin)
by_name = {i["name"]: i for i in items}
for req in ("Python", "Appium Python client", "Node.js", "Appium", "Appium driver: uiautomator2", "Appium driver: xcuitest", "Java (JDK)", "Android SDK", "Android adb", "Android build-tools (aapt2)", "Android cmdline-tools (sdkmanager)"):
    assert req in by_name, f"Missing check: {req}"
    assert by_name[req]["status"] == "ok", f"Expected {req} ok, got {by_name[req]}"
print("All bundled runtime checks OK")
'

echo "==> Verifying bundled pywebview native window runtime..."
if [ ! -d "${DIST_APP}/Contents/Frameworks/webview" ] || [ ! -d "${DIST_APP}/Contents/Frameworks/objc" ]; then
  echo "ERROR: Expected bundled webview/objc frameworks inside ${DIST_APP}/Contents/Frameworks" >&2
  exit 1
fi
env -i HOME="${HOME}" PATH="/usr/bin:/bin" "${QALPRIT_APP_BIN}" --check-desktop

echo "==> Verifying QAlprit.app code signature seal..."
codesign --verify --deep --strict --verbose=2 "${DIST_APP}"

# 9. Package DMG with Applications symlink + first-launch note
rm -rf "${DMG_STAGING}" "${DIST_MAC}/QAlprit.app" "${DMG_PATH}"
mkdir -p "${DMG_STAGING}"
ditto "${DIST_APP}" "${DMG_STAGING}/QAlprit.app"
codesign --verify --deep --strict "${DMG_STAGING}/QAlprit.app"
ln -s /Applications "${DMG_STAGING}/Applications"
cat > "${DMG_STAGING}/HOW-TO-OPEN.txt" <<'TXT'
QAlprit for macOS (Ad-hoc Signed, Un-notarized Build)
=====================================================

1. Drag "QAlprit.app" into the "Applications" folder shortcut.
2. First time opening QAlprit on macOS Sequoia (15+) / Tahoe (26+):
   - Recommended (Terminal, 1 command):
       xattr -dr com.apple.quarantine /Applications/QAlprit.app
     Then double-click QAlprit in /Applications.
   - Or via System Settings:
     Double-click QAlprit in /Applications -> click "Done" ->
     open System Settings -> Privacy & Security -> scroll to Security ->
     click "Open Anyway".
   (Note: Apple removed Control-click -> Open in macOS 15+.)

Your QAlprit workspace lives in: ~/Documents/QAlpritWorkspace (or ~/QAlpritWorkspace if upgraded)
TXT

hdiutil create -volname "QAlprit-${VERSION}" -srcfolder "${DMG_STAGING}" -ov -format UDZO "${DMG_PATH}"
ditto "${DIST_APP}" "${DIST_MAC}/QAlprit.app"
(
  cd "${DIST_MAC}"
  shasum -a 256 "${DMG_NAME}" > "SHA256SUMS-mac.txt"
)
echo "==> Success: ${DMG_PATH}"
cat "${DIST_MAC}/SHA256SUMS-mac.txt"
