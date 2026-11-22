#define AppName "配网承载力智能体·天智五号"
#define AppShortName "HC-Agent"
#define Src "..\dist\HC-Agent"
[Setup]
AppId={{9B2E5C71-3D4A-4F82-A6E1-7C5D3B8F1E24}
AppName={#AppName}
AppVersion=1.1.0
DefaultDirName={localappdata}\Programs\{#AppShortName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release\setup
OutputBaseFilename=配网承载力智能体-1.1.0-试用版
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
Password=TZ5-HCAgent-2026
Encryption=yes
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce
[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppShortName}.exe"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppShortName}.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppShortName}.exe"; Description: "立即启动"; Flags: nowait postinstall skipifsilent
[Code]
function InitializeSetup(): Boolean;
var today: String;
begin
  Result := True;
  today := GetDateTimeString('yyyy/mm/dd', '-', ':');
  if today > '2026/12/31' then
  begin
    MsgBox('本版本已于 2026-12-31 到期，无法安装。', mbCriticalError, MB_OK);
    Result := False;
  end;
end;