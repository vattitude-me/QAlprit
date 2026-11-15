; Inno Setup script for QAlprit Windows Installer (per-user, no admin required)

#ifndef QAlpritVersion
  #define QAlpritVersion "0.1.17"
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
var
  ScanPage: TOutputMsgMemoWizardPage;
  StoragePage: TInputDirWizardPage;
  DetectedNode: String;
  DetectedPython: String;
  DetectedJava: String;
  DetectedAndroidSdk: String;
  DetectedAvd: String;

function ForceReinstallRuntime(): Boolean;
begin
  Result := WizardIsTaskSelected('reinstall_runtime');
end;

function FirstExistingFile(Paths: array of String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Paths) - 1 do
  begin
    if (Paths[I] <> '') and FileExists(Paths[I]) then
    begin
      Result := Paths[I];
      Exit;
    end;
  end;
end;

procedure ScanSystemSoftware();
var
  LocalApp, ProgFiles, UserHome, JavaHomeEnv, AndroidHomeEnv: String;
begin
  LocalApp := ExpandConstant('{localappdata}');
  ProgFiles := ExpandConstant('{commonpf}');
  UserHome := GetEnv('USERPROFILE');
  JavaHomeEnv := GetEnv('JAVA_HOME');
  AndroidHomeEnv := GetEnv('ANDROID_HOME');
  if AndroidHomeEnv = '' then
    AndroidHomeEnv := GetEnv('ANDROID_SDK_ROOT');

  DetectedNode := FirstExistingFile([
    ProgFiles + '\nodejs\node.exe',
    LocalApp + '\Programs\nodejs\node.exe',
    ExpandConstant('{app}\runtime\node\node.exe')
  ]);

  DetectedPython := FirstExistingFile([
    'C:\Windows\py.exe',
    LocalApp + '\Programs\Python\Python312\python.exe',
    LocalApp + '\Programs\Python\Python311\python.exe',
    ProgFiles + '\Python312\python.exe',
    ProgFiles + '\Python311\python.exe'
  ]);

  DetectedJava := '';
  if JavaHomeEnv <> '' then
    DetectedJava := FirstExistingFile([AddBackslash(JavaHomeEnv) + 'bin\java.exe']);
  if DetectedJava = '' then
    DetectedJava := FirstExistingFile([
      ProgFiles + '\Android\Android Studio\jbr\bin\java.exe',
      ExpandConstant('{app}\runtime\jdk\bin\java.exe')
    ]);

  DetectedAndroidSdk := '';
  if (AndroidHomeEnv <> '') and DirExists(AndroidHomeEnv) then
    DetectedAndroidSdk := AndroidHomeEnv
  else if DirExists(LocalApp + '\QAlprit\android-sdk\emulator') then
    DetectedAndroidSdk := LocalApp + '\QAlprit\android-sdk'
  else if DirExists(LocalApp + '\Android\Sdk') then
    DetectedAndroidSdk := LocalApp + '\Android\Sdk'
  else if DirExists(AddBackslash(UserHome) + '.qalprit\android-sdk\emulator') then
    DetectedAndroidSdk := AddBackslash(UserHome) + '.qalprit\android-sdk';

  DetectedAvd := FirstExistingFile([
    LocalApp + '\QAlprit\avd\QAlprit_Pixel.avd\config.ini',
    AddBackslash(UserHome) + '.qalprit\avd\QAlprit_Pixel.avd\config.ini',
    AddBackslash(UserHome) + '.android\avd\QAlprit_Pixel.avd\config.ini'
  ]);
end;

function ChosenDataDir(): String;
begin
  if StoragePage <> nil then
    Result := RemoveBackslashUnlessRoot(Trim(StoragePage.Values[1]))
  else
    Result := ExpandConstant('{localappdata}\QAlprit');
end;

function ChosenWorkspaceDir(): String;
begin
  if StoragePage <> nil then
    Result := RemoveBackslashUnlessRoot(Trim(StoragePage.Values[0]))
  else
    Result := ExpandConstant('{userdocs}\QAlpritWorkspace');
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
  DataDir, LegacyRoot: String;
begin
  DataDir := AddBackslash(ChosenDataDir());
  LegacyRoot := AddBackslash(GetEnv('USERPROFILE'));
  Result := ForceReinstallRuntime()
    or (
      (not FileExists(DataDir + 'android-sdk\emulator\emulator.exe'))
      and (not FileExists(LegacyRoot + '.qalprit\android-sdk\emulator\emulator.exe'))
      and (not FileExists(ExpandConstant('{localappdata}\Android\Sdk\emulator\emulator.exe')))
    )
    or (
      (not FileExists(DataDir + 'avd\QAlprit_Pixel.avd\config.ini'))
      and (not FileExists(LegacyRoot + '.android\avd\QAlprit_Pixel.avd\config.ini'))
    );
end;

function JsonEscape(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure SaveQAlpritSettings();
var
  SettingsDir, SettingsFile, JsonBody, ReuseVal: String;
begin
  SettingsDir := ExpandConstant('{localappdata}\QAlprit');
  ForceDirectories(SettingsDir);
  SettingsFile := SettingsDir + '\settings.json';
  if (DetectedAndroidSdk <> '') and FileExists(AddBackslash(DetectedAndroidSdk) + 'emulator\emulator.exe') and (Pos('\QAlprit\', DetectedAndroidSdk) = 0) and (Pos('\.qalprit\', DetectedAndroidSdk) = 0) then
    ReuseVal := 'true'
  else
    ReuseVal := 'false';

  JsonBody := '{' + #13#10 +
    '  "workspace_dir": "' + JsonEscape(ChosenWorkspaceDir()) + '",' + #13#10 +
    '  "data_dir": "' + JsonEscape(ChosenDataDir()) + '",' + #13#10 +
    '  "prefer_existing_system_sdk": ' + ReuseVal + #13#10 +
    '}' + #13#10;
  SaveStringToFile(SettingsFile, JsonBody, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SaveQAlpritSettings();
  end;
end;

procedure InitializeWizard();
var
  ExistingExe, UserHome, DefaultWs, DefaultData, Report: String;
begin
  ScanSystemSoftware();
  UserHome := GetEnv('USERPROFILE');

  ExistingExe := ExpandConstant('{localappdata}\Programs\QAlprit\QAlprit.exe');
  if FileExists(ExistingExe) then
  begin
    WizardForm.WelcomeLabel2.Caption :=
      'An existing QAlprit installation was detected. Click Next to update QAlprit Workbench while keeping your existing Node.js, Appium, Java, Android SDK, and Virtual Emulator intact (only missing components will be installed).';
  end;

  if DirExists(AddBackslash(UserHome) + 'QAlpritWorkspace') then
    DefaultWs := AddBackslash(UserHome) + 'QAlpritWorkspace'
  else
    DefaultWs := ExpandConstant('{userdocs}\QAlpritWorkspace');

  if DirExists(AddBackslash(UserHome) + '.qalprit\android-sdk\emulator') and (not DirExists(ExpandConstant('{localappdata}\QAlprit\android-sdk\emulator'))) then
    DefaultData := AddBackslash(UserHome) + '.qalprit'
  else
    DefaultData := ExpandConstant('{localappdata}\QAlprit');

  Report := 'QAlprit scanned your PC for existing tools before installing:' + #13#10 + #13#10;
  if DetectedNode <> '' then
    Report := Report + '  [FOUND] Node.js: ' + DetectedNode + #13#10
  else
    Report := Report + '  [WILL BUNDLE] Node.js: portable v20 inside QAlprit' + #13#10;

  if DetectedPython <> '' then
    Report := Report + '  [FOUND] Python: ' + DetectedPython + #13#10
  else
    Report := Report + '  [WILL BUNDLE] Python: embedded cleanly inside QAlprit' + #13#10;

  if DetectedJava <> '' then
    Report := Report + '  [FOUND] Java JDK: ' + DetectedJava + #13#10
  else
    Report := Report + '  [WILL BUNDLE] Java 17 JRE: portable inside QAlprit' + #13#10;

  if DetectedAndroidSdk <> '' then
    Report := Report + '  [FOUND] Android SDK: ' + DetectedAndroidSdk + #13#10
  else
    Report := Report + '  [MISSING] Android SDK Emulator (~1.8 GB download / ~4-5 GB extracted)' + #13#10;

  if DetectedAvd <> '' then
    Report := Report + '  [FOUND] Virtual Device (QAlprit_Pixel): ' + DetectedAvd + #13#10
  else
    Report := Report + '  [MISSING] Virtual Device (QAlprit_Pixel): will be created in QAlprit AppData' + #13#10;

  Report := Report + #13#10 + 'Notice: On the next screen, choose or approve where QAlprit stores your Workspace and large SDK/Emulator files.';

  ScanPage := CreateOutputMsgMemoPage(
    wpWelcome,
    'System Pre-Check Results',
    'Review existing software already installed on this PC',
    'QAlprit avoids cluttering your C:\Users\<you> folder and checks for existing software first:',
    Report
  );

  StoragePage := CreateInputDirPage(
    ScanPage.ID,
    'Choose QAlprit Storage Locations',
    'Approve where your tests and ~2-5 GB SDK/Emulator files may be stored',
    'Using Windows standard folders keeps your home profile clean. Click Browse to move large emulator files to another drive if needed.',
    False,
    ''
  );
  StoragePage.Add('Test Workspace Folder (specs, flows, recorded screens, reports):');
  StoragePage.Add('Android SDK & Virtual Emulator Data (~2-5 GB if emulator is downloaded):');
  StoragePage.Values[0] := DefaultWs;
  StoragePage.Values[1] := DefaultData;
end;
