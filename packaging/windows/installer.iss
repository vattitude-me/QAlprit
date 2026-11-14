; Inno Setup script for QAlprit Windows Installer (per-user, no admin required)

#ifndef QAlpritVersion
  #define QAlpritVersion "0.1.3"
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut for QAlprit Workbench"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\QAlprit Workbench"; Filename: "{app}\QAlprit.exe"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall QAlprit"; Filename: "{uninstallexe}"
Name: "{autodesktop}\QAlprit Workbench"; Filename: "{app}\QAlprit.exe"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Run]
Filename: "{app}\QAlprit.exe"; Description: "Launch QAlprit Workbench"; Flags: nowait postinstall skipifsilent
