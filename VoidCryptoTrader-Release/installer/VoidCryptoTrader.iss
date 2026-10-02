; ============================================================================
;   VOID CRYPTO TRADER - Inno Setup installer script
;
;   Builds a real Windows setup program (Setup.exe) that installs the app,
;   creates Start Menu + optional Desktop shortcuts, and runs the native
;   desktop application afterwards. No web server, no IP/port - the app is
;   a normal windowed program.
;
;   HOW TO USE
;   ----------
;   1. Install Inno Setup 6 (free):  https://jrsoftware.org/isdl.php
;   2. Build the exe first (one of these):
;         - double-click Void.bat and pick [5] Build VoidCryptoTrader.exe
;         - or run build_exe.bat in this folder
;         - or manually:
;             py -3 -m pip install pyinstaller
;             py -3 -m PyInstaller --noconfirm --onefile --windowed ^
;                --name VoidCryptoTrader ..\app\void_launcher.py
;            then copy dist\VoidCryptoTrader.exe to
;                 VoidCryptoTrader-Release\installer\files\
;   3. Open this .iss in the Inno Setup Compiler and press F9 (Compile),
;      or from a command prompt:
;         "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" VoidCryptoTrader.iss
;   4. Your installer is at:  installer\Output\VoidCryptoTrader-Setup-<version>.exe
;
;   The output .exe goes into the folder named by OutputDir below.
; ============================================================================

#define MyAppName      "Void Crypto Trader"
#define MyAppVersion   "1.4.1"
#define MyAppPublisher "VOID"
#define MyAppURL       "https://github.com/void/void-crypto-trader"
#define MyAppExeName   "VoidCryptoTrader.exe"

; Fail early with a clear message instead of a cryptic Inno error if the
; exe has not been built yet. Run ..\build_installer.bat first, or build
; manually with PyInstaller and copy dist\VoidCryptoTrader.exe into
; installer\files\.
#if !FileExists(AddBackslash(SourceDir) + "files\" + MyAppExeName)
  #error VoidCryptoTrader.exe is missing from the "installer\files" folder. Build it first: run ..\build_installer.bat (or build with PyInstaller inside the app folder and copy dist\VoidCryptoTrader.exe to installer\files\).
#endif

[Setup]
; NOTE: AppId uniquely identifies this app for upgrades - do not change it
; once shipped.
AppId={{8E0F7A3C-5D2B-4E9A-9C61-7A1B2C3D4E5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\VoidCryptoTrader
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.txt
OutputDir=Output
OutputBaseFilename=VoidCryptoTrader-Setup-{#MyAppVersion}
SetupIconFile=VoidCryptoTrader.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
; The app stores portfolio state next to the exe, so allow writing there.
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"

[Files]
; --- the built single-file exe (see header for how to build it) -----------
; NOTE: this file must exist BEFORE compiling - run ..\build_installer.bat
; (or build with PyInstaller and copy dist\VoidCryptoTrader.exe here).
; If it is missing, Inno would otherwise fail with a confusing error later.
Source: "files\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; --- bundled data the launcher expects NEXT TO the exe --------------------
; keys template, README, license, version marker, default config/state seeds.
Source: "..\app\README.md";                 DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\app\VERSION";                   DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\LICENSE.txt";                   DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\keys\*";                    DestDir: "{app}\keys"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist uninsneveruninstall
Source: "..\app\config_exchange.json";      DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\gui_theme.json";            DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\accounts.json";             DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\portfolio_state.json";      DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\copy_follows.json";         DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\copy_state.json";           DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\price_history.json";        DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\equity_history.json";       DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
Source: "..\app\session_meta.json";         DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
; --- strategies package (read at runtime by auto_trader/copy strategies) --
Source: "..\app\strategies\*.py";           DestDir: "{app}\strategies"; Flags: ignoreversion skipifsourcedoesntexist
; --- anything else the user dropped into installer\files besides the exe --
Source: "files\*";                          DestDir: "{app}"; Excludes: "{#MyAppExeName}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}";               Filename: "{app}\{#MyAppExeName}"; \
    Comment: "Open the Void desktop trading app"
Name: "{autodesktop}\{#MyAppName}";         Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
; Launch the native desktop app straight after installing.
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime state the app writes next to the exe (kept out of Program Files
; clutter on uninstall). User's keys/api_keys.env is preserved above.
Type: files; Name: "{app}\logs\*"
Type: files; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\__pycache__"
