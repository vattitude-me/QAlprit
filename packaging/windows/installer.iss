; Inno Setup script for QAlprit Windows Installer (per-user, no admin required)

#ifndef QAlpritVersion
  #define QAlpritVersion "0.1.16"
#endif

#ifndef DistDir
  #define DistDir "..\..\dist\QAlprit"
#endif

#ifndef OutputDir
  #define OutputDir "..\..\dist-win"
#endif

[Setup]
AppId={{8D6B4A3C-5B9E-4B18-8E5D-6F0C9C4F8D21}
AppName=QAlprit
AppVersion={#QAlpritVersion}
AppPublisher=vattitude.ca
AppPublisherURL=https://qalprit.vattitude.ca
AppSupportURL=https://github.com/vattitude-me/QAlprit/issues
DefaultDirName={localappdata}\Programs\QAlprit
DefaultGroupName=QAlprit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=QAlprit-Setup-{#QAlpritVersion}-win64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=QAlprit {#QAlpritVersion}
UsePreviousAppDir=yes
UsePreviousSetupType=no
UsePreviousTasks=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full Installation (Recommended — QAlprit + Appium + Virtual Android Emulator)"
Name: "compact"; Description: "QAlprit Workbench Core Only (Use existing system Node/Appium/Java/ADB)"
Name: "custom"; Description: "Custom Installation (Choose components)"; Flags: iscustom

[Components]
Name: "core"; Description: "QAlprit Workbench Desktop App, CLI & Python Engine"; Types: full compact custom; Flags: fixed
Name: "android_engine"; Description: "Android Runtime (Appium 2.19, UiAutomator2, Node.js v20, ADB, aapt2, SDK Manager)"; Types: full custom
Name: "java_runtime"; Description: "Eclipse Temurin Java 17 JRE (required for Android & SDK Manager)"; Types: full custom
Name: "android_emulator"; Description: "Virtual Android Emulator (QAlprit_Pixel API 34 — Primary path, downloads ~1.8 GB if missing)"; Types: full custom

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut for QAlprit Workbench"; GroupDescription: "Additional shortcuts:"
Name: "reinstall_runtime"; Description: "Force reinstall / repair bundled Node.js, Appium, Java & Android SDK (leave unchecked for fast updates)"; GroupDescription: "Upgrade options:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Excludes: "\runtime"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\runtime\node\*"; DestDir: "{app}\runtime\node"; Components: android_engine; Check: NeedsNodeRuntime; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\runtime\appium\*"; DestDir: "{app}\runtime\appium"; Components: android_engine; Check: NeedsAppiumRuntime; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\runtime\appium_home\*"; DestDir: "{app}\runtime\appium_home"; Components: android_engine; Check: NeedsAppiumHomeRuntime; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\runtime\android-sdk\*"; DestDir: "{app}\runtime\android-sdk"; Components: android_engine; Check: NeedsAndroidSdkRuntime; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\runtime\jdk\*"; DestDir: "{app}\runtime\jdk"; Components: java_runtime; Check: NeedsJavaRuntime; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\QAlprit Workbench"; Filename: "{app}\QAlprit.exe"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall QAlprit"; Filename: "{uninstallexe}"
Name: "{autodesktop}\QAlprit Workbench"; Filename: "{app}\QAlprit.exe"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Run]
Filename: "{app}\qalprit-cli.exe"; Parameters: "emulator setup --if-missing --pause-on-error"; Description: "Download & configure QAlprit_Pixel Virtual Android Phone (~1.8 GB if not already installed)"; Components: android_emulator; Check: NeedsEmulatorSetup; Flags: postinstall skipifsilent waituntilterminated
Filename: "{app}\QAlprit.exe"; Description: "Launch QAlprit Workbench"; Flags: nowait postinstall skipifsilent

[Code]
function ForceReinstallRuntime(): Boolean;
begin
  Result := WizardIsTaskSelected('reinstall_runtime');
end;

function NeedsNodeRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime() or (not FileExists(ExpandConstant('{app}\runtime\node\node.exe')));
end;

function NeedsAppiumRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime() or (not FileExists(ExpandConstant('{app}\runtime\appium\node_modules\appium\build\lib\main.js')));
end;

function NeedsAppiumHomeRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime() or (not FileExists(ExpandConstant('{app}\runtime\appium_home\.appium\node_modules\appium-uiautomator2-driver\package.json')));
end;

function NeedsAndroidSdkRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime()
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\platform-tools\adb.exe')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\cmdline-tools\latest\bin\sdkmanager.bat')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\build-tools\35.0.1\aapt2.exe')));
end;

function NeedsJavaRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime() or (not FileExists(ExpandConstant('{app}\runtime\jdk\bin\java.exe')));
end;

function NeedsEmulatorSetup(): Boolean;
var
  SdkRoot: String;
begin
  SdkRoot := AddBackslash(GetEnv('USERPROFILE')) + '.qalprit\android-sdk\';
  Result := ForceReinstallRuntime()
    or (not FileExists(SdkRoot + 'emulator\emulator.exe'))
    or (not FileExists(SdkRoot + 'avd\QAlprit_Pixel.avd\config.ini'));
end;

procedure InitializeWizard();
var
  ExistingExe: String;
begin
  ExistingExe := ExpandConstant('{localappdata}\Programs\QAlprit\QAlprit.exe');
  if FileExists(ExistingExe) then
  begin
    WizardForm.WelcomeLabel2.Caption :=
      'An existing QAlprit installation was detected. Click Next to update QAlprit Workbench while keeping your existing Node.js, Appium, Java, Android SDK, and Virtual Emulator intact (only missing components will be installed).';
  end;
end;
