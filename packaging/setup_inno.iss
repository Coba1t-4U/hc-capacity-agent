; 配网承载力智能体（天智五号）安装脚本
; 编译：ISCC.exe setup_inno.iss
; 特点：按用户安装（无需管理员）、开始菜单 + 桌面快捷方式、标准卸载项、可选附带 lic.json

#define AppName "天智五号·配网承载力智能体"
#define AppShortName "HC-Agent"
#define AppVersion "1.1.0"
#define AppPublisher "配网承载力智能体项目组"
#define SourceDir "..\dist\HC-Agent"

[Setup]
AppId={{8F3A61B2-6C4D-4E1A-9B77-3C8E1D5A2F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release\setup
OutputBaseFilename=HC-Agent-{#AppVersion}-Setup-Inno
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppShortName}.exe
LicenseFile=
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 主程序与全部依赖
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 若安装包旁放了授权文件，一并安装（没有则跳过）
Source: "..\lic.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppShortName}.exe"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppShortName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppShortName}.exe"; Description: "立即启动"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runs"
Type: filesandordirs; Name: "{app}\logs"
