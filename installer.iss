; Inno Setup script for SOLONA Pharmacy.
; Builds a single Setup.exe that installs the app, adds a Start Menu entry
; (searchable via Windows Search) and an optional Desktop shortcut, and
; registers a proper uninstaller in "Add or Remove Programs".
;
; Installs per-machine under Program Files (standard Windows convention --
; the target path resolution here is the most consistent/predictable across
; different PCs). Requires a one-time admin/UAC confirmation at install time,
; which is normal for desktop software and typical shop PCs are run as local
; admin anyway.

#define MyAppName "SOLONA Pharmacy"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "SOLONA Pharmacy"
#define MyAppExeName "SOLONA.exe"

[Setup]
AppId={{B9C1E7B4-2F3E-4C2A-9B3E-6D9B0B7A2F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PharmacyManagementSystem
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=SOLONA_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\SOLONA.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
