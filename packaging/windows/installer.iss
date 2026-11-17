; Inno Setup script for QAlprit Windows Installer (per-user, no admin required)

#ifndef QAlpritVersion
  #define QAlpritVersion "0.1.47"
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
    LocalApp + '\Programs\QAlprit\runtime\node\node.exe'
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
      LocalApp + '\Programs\QAlprit\runtime\jdk\bin\java.exe'
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
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\platform-tools\AdbWinApi.dll')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\platform-tools\AdbWinUsbApi.dll')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\cmdline-tools\latest\bin\sdkmanager.bat')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\cmdline-tools\latest\bin\android.exe')))
    or (not FileExists(ExpandConstant('{app}\runtime\android-sdk\build-tools\35.0.1\aapt2.exe')));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Rc: Integer;
begin
  Result := '';
  Exec('taskkill', '/F /IM QAlprit.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Exec('taskkill', '/F /IM qalprit-cli.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Exec('taskkill', '/F /IM adb.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Exec('taskkill', '/F /IM emulator.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Exec('taskkill', '/F /IM qemu-system-x86_64.exe /T', '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function NeedsJavaRuntime(): Boolean;
begin
  Result := ForceReinstallRuntime() or (not FileExists(ExpandConstant('{app}\runtime\jdk\bin\java.exe')));
end;

function NeedsEmulatorSetup(): Boolean;
var
  DataDir, LegacyRoot, SysSdk: String;
  HasCompleteSysSdk: Boolean;
begin
  DataDir := AddBackslash(ChosenDataDir());
  LegacyRoot := AddBackslash(GetEnv('USERPROFILE'));
  SysSdk := ExpandConstant('{localappdata}\Android\Sdk');
  HasCompleteSysSdk := FileExists(SysSdk + '\emulator\emulator.exe')
    and FileExists(SysSdk + '\platform-tools\AdbWinApi.dll')
    and DirExists(SysSdk + '\system-images\android-34');
  Result := ForceReinstallRuntime()
    or (
      (not FileExists(DataDir + 'android-sdk\emulator\emulator.exe'))
      and (not FileExists(LegacyRoot + '.qalprit\android-sdk\emulator\emulator.exe'))
      and (not HasCompleteSysSdk)
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
  SettingsDir, SettingsFile, JsonBody, ReuseVal, DetSdk: String;
begin
  SettingsDir := ExpandConstant('{localappdata}\QAlprit');
  ForceDirectories(SettingsDir);
  SettingsFile := SettingsDir + '\settings.json';
  DetSdk := AddBackslash(DetectedAndroidSdk);
  if (DetectedAndroidSdk <> '')
    and (Pos('\QAlprit\', DetectedAndroidSdk) = 0)
    and (Pos('\.qalprit\', DetectedAndroidSdk) = 0)
    and FileExists(DetSdk + 'emulator\emulator.exe')
    and FileExists(DetSdk + 'platform-tools\AdbWinApi.dll')
    and DirExists(DetSdk + 'system-images\android-34') then
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

{ ---- Selective Uninstall Cleanup (Settings, SDK, Virtual Phone, Workspace) ---- }
var
  UninstConfiguredWs: String;
  UninstConfiguredData: String;
  RemoveSettingsFlag: Boolean;
  RemoveSdkFlag: Boolean;
  RemoveAvdFlag: Boolean;
  RemoveWorkspaceFlag: Boolean;
  ChkUnSettings: TNewCheckBox;
  ChkUnSdk: TNewCheckBox;
  ChkUnAvd: TNewCheckBox;
  ChkUnWs: TNewCheckBox;

function ExtractJsonField(JsonText, KeyName: String): String;
var
  Marker, Tail, OutStr: String;
  P, Q, I: Integer;
begin
  Result := '';
  Marker := '"' + KeyName + '"';
  P := Pos(Marker, JsonText);
  if P = 0 then Exit;
  Tail := Copy(JsonText, P + Length(Marker), Length(JsonText));
  Q := Pos(':', Tail);
  if Q = 0 then Exit;
  Tail := Trim(Copy(Tail, Q + 1, Length(Tail)));
  if (Length(Tail) = 0) or (Tail[1] <> '"') then Exit;
  OutStr := '';
  I := 2;
  while I <= Length(Tail) do
  begin
    if (Tail[I] = '\') and (I < Length(Tail)) then
    begin
      OutStr := OutStr + Tail[I + 1];
      I := I + 2;
    end
    else if Tail[I] = '"' then
      Break
    else
    begin
      OutStr := OutStr + Tail[I];
      I := I + 1;
    end;
  end;
  Result := OutStr;
end;

procedure LoadConfiguredPathsForUninstall();
var
  SettingsFile: String;
  RawAnsi: AnsiString;
  JsonText: String;
begin
  UninstConfiguredData := ExpandConstant('{localappdata}\QAlprit');
  UninstConfiguredWs := ExpandConstant('{userdocs}\QAlpritWorkspace');
  SettingsFile := UninstConfiguredData + '\settings.json';
  if FileExists(SettingsFile) and LoadStringFromFile(SettingsFile, RawAnsi) then
  begin
    JsonText := String(RawAnsi);
    if ExtractJsonField(JsonText, 'data_dir') <> '' then
      UninstConfiguredData := ExtractJsonField(JsonText, 'data_dir');
    if ExtractJsonField(JsonText, 'workspace_dir') <> '' then
      UninstConfiguredWs := ExtractJsonField(JsonText, 'workspace_dir');
  end;
end;

procedure OnUninstSelectAllClick(Sender: TObject);
begin
  ChkUnSettings.Checked := True;
  ChkUnSdk.Checked := True;
  ChkUnAvd.Checked := True;
  ChkUnWs.Checked := True;
end;

procedure OnUninstAppOnlyClick(Sender: TObject);
begin
  ChkUnSettings.Checked := False;
  ChkUnSdk.Checked := False;
  ChkUnAvd.Checked := False;
  ChkUnWs.Checked := False;
end;

function PromptUninstallDataCleanup(): Boolean;
var
  Form: TSetupForm;
  IntroLbl, NoteLbl: TNewStaticText;
  BtnAll, BtnKeep, BtnOk, BtnCancel: TNewButton;
  TopY: Integer;
begin
  Result := False;
  LoadConfiguredPathsForUninstall();
  RemoveSettingsFlag := False;
  RemoveSdkFlag := False;
  RemoveAvdFlag := False;
  RemoveWorkspaceFlag := False;

  if UninstallSilent() then
    Exit;

  Form := CreateCustomForm(ScaleX(560), ScaleY(340), False, False);
  try
    Form.Caption := 'QAlprit Uninstalled — Choose Data to Remove or Keep';

    IntroLbl := TNewStaticText.Create(Form);
    IntroLbl.Parent := Form;
    IntroLbl.Left := ScaleX(16);
    IntroLbl.Top := ScaleY(14);
    IntroLbl.Width := ScaleX(528);
    IntroLbl.AutoSize := False;
    IntroLbl.Height := ScaleY(38);
    IntroLbl.WordWrap := True;
    IntroLbl.Caption := 'QAlprit Workbench application binaries were removed. Select any additional tool data, configs, emulator files, or workspaces you also want to delete (unchecked items are retained):';

    TopY := ScaleY(62);
    ChkUnSettings := TNewCheckBox.Create(Form);
    ChkUnSettings.Parent := Form;
    ChkUnSettings.Left := ScaleX(16);
    ChkUnSettings.Top := TopY;
    ChkUnSettings.Width := ScaleX(528);
    ChkUnSettings.Checked := True;
    ChkUnSettings.Caption := 'Settings, logs & Appium caches (settings.json, logs, android-home, appium_home)';

    TopY := TopY + ScaleY(30);
    ChkUnSdk := TNewCheckBox.Create(Form);
    ChkUnSdk.Parent := Form;
    ChkUnSdk.Left := ScaleX(16);
    ChkUnSdk.Top := TopY;
    ChkUnSdk.Width := ScaleX(528);
    ChkUnSdk.Checked := False;
    ChkUnSdk.Caption := 'QAlprit-downloaded Android SDK & Emulator (~2-5 GB, including legacy .qalprit)';

    TopY := TopY + ScaleY(30);
    ChkUnAvd := TNewCheckBox.Create(Form);
    ChkUnAvd.Parent := Form;
    ChkUnAvd.Left := ScaleX(16);
    ChkUnAvd.Top := TopY;
    ChkUnAvd.Width := ScaleX(528);
    ChkUnAvd.Checked := False;
    ChkUnAvd.Caption := 'QAlprit Virtual Android Phone (QAlprit_Pixel.avd only; preserves other AVDs)';

    TopY := TopY + ScaleY(30);
    ChkUnWs := TNewCheckBox.Create(Form);
    ChkUnWs.Parent := Form;
    ChkUnWs.Left := ScaleX(16);
    ChkUnWs.Top := TopY;
    ChkUnWs.Width := ScaleX(528);
    ChkUnWs.Checked := False;
    ChkUnWs.Caption := 'Test Workspace (recorded flows, specs, apps & HTML reports)';

    NoteLbl := TNewStaticText.Create(Form);
    NoteLbl.Parent := Form;
    NoteLbl.Left := ScaleX(16);
    NoteLbl.Top := TopY + ScaleY(34);
    NoteLbl.Width := ScaleX(528);
    NoteLbl.AutoSize := False;
    NoteLbl.Height := ScaleY(52);
    NoteLbl.WordWrap := True;
    NoteLbl.Caption := 'Data Folder: ' + UninstConfiguredData + #13#10 + 'Workspace: ' + UninstConfiguredWs;

    BtnAll := TNewButton.Create(Form);
    BtnAll.Parent := Form;
    BtnAll.Left := ScaleX(16);
    BtnAll.Top := ScaleY(246);
    BtnAll.Width := ScaleX(160);
    BtnAll.Height := ScaleY(26);
    BtnAll.Caption := 'Select All (Full Wipe)';
    BtnAll.OnClick := @OnUninstSelectAllClick;

    BtnKeep := TNewButton.Create(Form);
    BtnKeep.Parent := Form;
    BtnKeep.Left := ScaleX(184);
    BtnKeep.Top := ScaleY(246);
    BtnKeep.Width := ScaleX(170);
    BtnKeep.Height := ScaleY(26);
    BtnKeep.Caption := 'Uncheck All (Keep Data)';
    BtnKeep.OnClick := @OnUninstAppOnlyClick;

    BtnOk := TNewButton.Create(Form);
    BtnOk.Parent := Form;
    BtnOk.Left := ScaleX(322);
    BtnOk.Top := ScaleY(294);
    BtnOk.Width := ScaleX(116);
    BtnOk.Height := ScaleY(28);
    BtnOk.Caption := 'Delete Checked';
    BtnOk.ModalResult := mrOk;
    BtnOk.Default := True;

    BtnCancel := TNewButton.Create(Form);
    BtnCancel.Parent := Form;
    BtnCancel.Left := ScaleX(446);
    BtnCancel.Top := ScaleY(294);
    BtnCancel.Width := ScaleX(98);
    BtnCancel.Height := ScaleY(28);
    BtnCancel.Caption := 'Keep Everything';
    BtnCancel.ModalResult := mrCancel;
    BtnCancel.Cancel := True;

    Form.FlipAndCenterIfNeeded(False, UninstallProgressForm, False);
    if Form.ShowModal <> mrOk then
      Exit;

    RemoveSettingsFlag := ChkUnSettings.Checked;
    RemoveSdkFlag := ChkUnSdk.Checked;
    RemoveAvdFlag := ChkUnAvd.Checked;
    RemoveWorkspaceFlag := ChkUnWs.Checked;
    Result := True;
  finally
    Form.Free;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
end;

procedure SafeDelDir(Path: String);
begin
  if (Path <> '') and DirExists(Path) then
    DelTree(Path, True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserHome, LegacyQalprit, LegacyAndroidAvd, StandardData: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  if not PromptUninstallDataCleanup() then
    Exit;

  UserHome := AddBackslash(GetEnv('USERPROFILE'));
  LegacyQalprit := UserHome + '.qalprit';
  LegacyAndroidAvd := UserHome + '.android\avd';
  StandardData := ExpandConstant('{localappdata}\QAlprit');

  if RemoveSettingsFlag then
  begin
    if FileExists(StandardData + '\settings.json') then
      DeleteFile(StandardData + '\settings.json');
    SafeDelDir(UninstConfiguredData + '\logs');
    SafeDelDir(UninstConfiguredData + '\android-home');
    SafeDelDir(UninstConfiguredData + '\appium_home');
    SafeDelDir(LegacyQalprit + '\logs');
    SafeDelDir(LegacyQalprit + '\appium_home');
  end;

  if RemoveSdkFlag then
  begin
    SafeDelDir(UninstConfiguredData + '\android-sdk');
    SafeDelDir(LegacyQalprit + '\android-sdk');
  end;

  if RemoveAvdFlag then
  begin
    SafeDelDir(UninstConfiguredData + '\avd\QAlprit_Pixel.avd');
    if FileExists(UninstConfiguredData + '\avd\QAlprit_Pixel.ini') then
      DeleteFile(UninstConfiguredData + '\avd\QAlprit_Pixel.ini');
    SafeDelDir(LegacyAndroidAvd + '\QAlprit_Pixel.avd');
    if FileExists(LegacyAndroidAvd + '\QAlprit_Pixel.ini') then
      DeleteFile(LegacyAndroidAvd + '\QAlprit_Pixel.ini');
  end;

  if RemoveWorkspaceFlag then
  begin
    SafeDelDir(UninstConfiguredWs);
    SafeDelDir(UserHome + 'QAlpritWorkspace');
  end;

  { Remove empty container directories cleanly if nothing was kept inside them }
  RemoveDir(UninstConfiguredData + '\avd');
  RemoveDir(UninstConfiguredData);
  if StandardData <> UninstConfiguredData then
    RemoveDir(StandardData);
  RemoveDir(LegacyQalprit);
end;
