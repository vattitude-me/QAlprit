param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if (-not $Version) {
    $pyproject = Get-Content (Join-Path $RepoRoot "pyproject.toml") -Raw
    if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "Could not determine version from pyproject.toml"
    }
}
Write-Host "==> Building QAlprit Windows Installer v$Version"

$BuildWork = Join-Path $RepoRoot "build\win-runtime"
$DistApp = Join-Path $RepoRoot "dist\QAlprit"
$DistWin = Join-Path $RepoRoot "dist-win"
New-Item -ItemType Directory -Force -Path $BuildWork, $DistWin | Out-Null

# 1. Portable Node.js v20 LTS (win-x64)
$NodeVersion = "v20.18.1"
$NodeZip = Join-Path $BuildWork "node-$NodeVersion-win-x64.zip"
$NodeDest = Join-Path $BuildWork "node"
if (-not (Test-Path (Join-Path $NodeDest "node.exe"))) {
    Write-Host "==> Downloading Node.js $NodeVersion (win-x64)..."
    Invoke-WebRequest -Uri "https://nodejs.org/dist/$NodeVersion/node-$NodeVersion-win-x64.zip" -OutFile $NodeZip
    $NodeExtract = Join-Path $BuildWork "node-extract"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $NodeExtract, $NodeDest
    Expand-Archive -Path $NodeZip -DestinationPath $NodeExtract -Force
    $InnerNode = Get-ChildItem -Directory $NodeExtract | Select-Object -First 1
    Move-Item -Path $InnerNode.FullName -Destination $NodeDest
    Remove-Item -Recurse -Force $NodeExtract
}

$NodeExe = Join-Path $NodeDest "node.exe"
$NpmCmd = Join-Path $NodeDest "npm.cmd"

# 2. Bundle Appium 2 + UiAutomator2 driver
$AppiumDir = Join-Path $BuildWork "appium"
$AppiumHome = Join-Path $BuildWork "appium_home"
$AppiumMainJs = Join-Path $AppiumDir "node_modules\appium\build\lib\main.js"
if (-not (Test-Path $AppiumMainJs)) {
    Write-Host "==> Installing Appium 2 into bundled runtime..."
    New-Item -ItemType Directory -Force -Path $AppiumDir, $AppiumHome | Out-Null
    $env:PATH = "$NodeDest;$env:PATH"
    Push-Location $AppiumDir
    try {
        & $NpmCmd init -y | Out-Null
        & $NpmCmd install --no-audit --no-fund appium@2.19.0
        if ($LASTEXITCODE -ne 0) { throw "npm install appium@2.19.0 failed" }
    } finally {
        Pop-Location
    }
}

Write-Host "==> Installing UiAutomator2 driver into APPIUM_HOME..."
New-Item -ItemType Directory -Force -Path $AppiumHome | Out-Null
$env:APPIUM_HOME = $AppiumHome
$env:PATH = "$NodeDest;$env:PATH"
$installedDrivers = & $NodeExe $AppiumMainJs driver list --installed --json 2>$null | Out-String
if ($installedDrivers -notmatch '"uiautomator2"') {
    & $NodeExe $AppiumMainJs driver install uiautomator2@4.2.9
    if ($LASTEXITCODE -ne 0) { throw "appium driver install uiautomator2@4.2.9 failed" }
}

# 3. Portable Eclipse Temurin JRE 17 (win-x64)
$JdkDest = Join-Path $BuildWork "jdk"
if (-not (Test-Path (Join-Path $JdkDest "bin\java.exe"))) {
    Write-Host "==> Downloading Eclipse Temurin JRE 17 (win-x64)..."
    $JreZip = Join-Path $BuildWork "temurin-jre17.zip"
    Invoke-WebRequest -Uri "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jre/hotspot/normal/eclipse" -OutFile $JreZip
    $JreExtract = Join-Path $BuildWork "jre-extract"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $JreExtract, $JdkDest
    Expand-Archive -Path $JreZip -DestinationPath $JreExtract -Force
    $InnerJre = Get-ChildItem -Directory $JreExtract | Select-Object -First 1
    Move-Item -Path $InnerJre.FullName -Destination $JdkDest
    Remove-Item -Recurse -Force $JreExtract
}

# 4. Android platform-tools (adb.exe)
$AndroidSdkDest = Join-Path $BuildWork "android-sdk"
if (-not (Test-Path (Join-Path $AndroidSdkDest "platform-tools\adb.exe"))) {
    Write-Host "==> Downloading Android SDK platform-tools..."
    $PtZip = Join-Path $BuildWork "platform-tools-latest-windows.zip"
    Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $PtZip
    New-Item -ItemType Directory -Force -Path $AndroidSdkDest | Out-Null
    Expand-Archive -Path $PtZip -DestinationPath $AndroidSdkDest -Force
}

# 5. Build QAlprit.exe (windowed) and qalprit-cli.exe (console) with PyInstaller
Write-Host "==> Installing QAlprit desktop dependencies and PyInstaller..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[desktop]" "pythonnet>=3.0.0" pyinstaller
python -c "import webview, clr, webview.platforms.edgechromium; print('pywebview:', webview.__version__)"
if ($LASTEXITCODE -ne 0) { throw "pywebview / pythonnet verification failed" }

$WinIconDir = Join-Path $RepoRoot "build\win-icon"
$WinIconPath = Join-Path $WinIconDir "QAlprit.ico"
New-Item -ItemType Directory -Force -Path $WinIconDir | Out-Null
if (-not (Test-Path $WinIconPath)) {
    Write-Host "==> Generating Windows .ico app icon..."
    @'
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])
base = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
draw = ImageDraw.Draw(base)
draw.rounded_rectangle([32, 32, 480, 480], radius=110, fill=(15, 118, 110, 255))
draw.rounded_rectangle([112, 112, 400, 400], radius=80, outline=(248, 250, 252, 255), width=42)
draw.line([(192, 256), (240, 304), (352, 184)], fill=(94, 234, 212, 255), width=38, joint="curve")
draw.line([(296, 296), (392, 392)], fill=(248, 250, 252, 255), width=42)
base.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
'@ | python - $WinIconPath
    if ($LASTEXITCODE -ne 0) { throw "Windows icon generation failed" }
}

Write-Host "==> Running PyInstaller..."
pyinstaller --clean --noconfirm (Join-Path $PSScriptRoot "qalprit.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

# 6. Attach bundled runtime/ next to QAlprit.exe
$RuntimeTarget = Join-Path $DistApp "runtime"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $RuntimeTarget
New-Item -ItemType Directory -Force -Path $RuntimeTarget | Out-Null
foreach ($folder in @("node", "appium", "appium_home", "jdk", "android-sdk")) {
    Write-Host "==> Copying runtime/$folder..."
    Copy-Item -Recurse -Force (Join-Path $BuildWork $folder) (Join-Path $RuntimeTarget $folder)
}

# 7. Smoke test frozen bundle without external node/appium/java/sdk env vars
Write-Host "==> Smoke-testing frozen QAlprit bundle..."
$QAlpritGuiExe = Join-Path $DistApp "QAlprit.exe"
$QAlpritCliExe = Join-Path $DistApp "qalprit-cli.exe"
if (-not (Test-Path $QAlpritGuiExe)) { throw "Expected GUI executable not found at $QAlpritGuiExe" }
if (-not (Test-Path $QAlpritCliExe)) { throw "Expected CLI executable not found at $QAlpritCliExe" }

$wvFiles = Get-ChildItem -Recurse -Path $DistApp -Filter "*WebView2*" -ErrorAction SilentlyContinue
if (-not $wvFiles) { throw "Expected WebView2 runtime assemblies in $DistApp" }
Write-Host "Bundled WebView2 files: $($wvFiles.Count)"

$verOut = & $QAlpritCliExe --version
if ($LASTEXITCODE -ne 0) { throw "qalprit-cli.exe --version failed" }
Write-Host "Version output: $verOut"

$origPath = $env:PATH
$origAndroidHome = $env:ANDROID_HOME
$origJavaHome = $env:JAVA_HOME
$origAppiumHome = $env:APPIUM_HOME
$env:PATH = "C:\Windows\System32;C:\Windows"
$env:ANDROID_HOME = ""
$env:JAVA_HOME = ""
$env:APPIUM_HOME = ""
$docJson = & $QAlpritCliExe doctor --json-output
$docRc = $LASTEXITCODE
$env:PATH = $origPath
$env:ANDROID_HOME = $origAndroidHome
$env:JAVA_HOME = $origJavaHome
$env:APPIUM_HOME = $origAppiumHome
Write-Host $docJson
if ($docRc -ne 0) { throw "qalprit-cli.exe doctor failed when relying strictly on bundled runtime/" }

# 8. Build Inno Setup installer
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
if (-not $Iscc) {
    foreach ($cand in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $cand) { $Iscc = $cand; break }
    }
}
if (-not $Iscc) {
    Write-Host "==> Installing Inno Setup 6 via choco..."
    choco install innosetup -y --no-progress
    $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}

& $Iscc "/DQAlpritVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

$InstallerExe = Join-Path $DistWin "QAlprit-Setup-$Version-win64.exe"
if (-not (Test-Path $InstallerExe)) {
    throw "Expected installer not found at $InstallerExe"
}
$Hash = (Get-FileHash $InstallerExe -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumLine = "$Hash  QAlprit-Setup-$Version-win64.exe"
Set-Content -Path (Join-Path $DistWin "SHA256SUMS.txt") -Value $ChecksumLine -Encoding utf8
Write-Host "==> Success: $InstallerExe ($ChecksumLine)"
