; Glass Nova — Production Windows Installer Script (Inno Setup 6)
; Compiles dist/GlassNova/* (built via build_exe.py) into dist/installer/GlassNova-Setup-<version>.exe
;
; Guarantees:
;  - Installs without requiring users to install or configure Python
;  - Preserves user data (%APPDATA%\PC_Complete_Assistant: .env, workflows, DPAPI data, settings) on upgrades
;  - Never deletes user data on uninstall unless the user explicitly confirms
;  - Never logs or touches user secrets or API keys

#ifndef MyAppVersion
  #define MyAppVersion "1.1.0-beta"
#endif

#define MyAppName "Glass Nova"
#define MyAppPublisher "Glass Nova Project"
#define MyAppURL "https://tejaswaroop30.github.io/Glass-Nova/"
#define MyAppRepoURL "https://github.com/tejaswaroop30/Glass-Nova"
#define MyAppExeName "GlassNova.exe"
#define SourceDistDir "..\dist\GlassNova"

[Setup]
; Stable GUID ensures upgrades replace binaries cleanly while preserving user data
AppId={{7B4E6C18-9F3D-4A82-B1E5-2C8D94F1A0E3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppRepoURL}/issues
AppUpdatesURL={#MyAppRepoURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Per-user install by default (no admin UAC required); user may choose all-users if elevated
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=GlassNova-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Package only the verified PyInstaller runtime output (excludes dev files, tests, caches, .env)
Source: "{#SourceDistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Excludes: ".env,*.pyc,__pycache__\*,test_*.py"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only remove runtime cache files created inside the application install directory
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    UserDataDir := ExpandConstant('{userappdata}\PC_Complete_Assistant');
    if DirExists(UserDataDir) then
    begin
      if MsgBox(
        'Do you also want to remove your personal Glass Nova data (settings, workflows, tasks, memories, and .env API key) stored in:' + #13#10 +
        UserDataDir + #13#10#13#10 +
        'Choose No to keep your data for future upgrades or reinstallations.',
        mbConfirmation,
        MB_YESNO or MB_DEFBUTTON2
      ) = IDYES then
      begin
        DelTree(UserDataDir, True, True, True);
      end;
    end;
  end;
end;
