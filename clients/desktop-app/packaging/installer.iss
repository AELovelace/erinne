; Inno Setup script for Mnemerynne Desktop: a per-user installer (no admin rights), hand-installed (it's a privileged
; app: no Atera deployment). ps\build-windows.ps1 runs it with /DAppVersion=<version> after `dotnet publish`.
;
; Silent use (the app's own updater runs it like this):
;   Mnemerynne-Desktop-Setup-<version>.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1
; /RELAUNCH=1 starts the app again when the update is done.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B3E1C6F4-5A2D-4E8B-9C71-2F6D8A4E0B93}
AppName=Mnemerynne Desktop
AppVersion={#AppVersion}
AppVerName=Mnemerynne Desktop {#AppVersion}
AppPublisher=Sad Girls Club
DefaultDirName={localappdata}\Programs\Mnemerynne Desktop
DefaultGroupName=Mnemerynne
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
; x64 only: the Cloud Files interop is written for 64-bit Windows
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=Mnemerynne-Desktop-Setup-{#AppVersion}
SetupIconFile=..\src\Mnemerynne.Desktop\Assets\app.ico
UninstallDisplayIcon={app}\Mnemerynne.Desktop.exe
UninstallDisplayName=Mnemerynne Desktop
Compression=lzma2
SolidCompression=yes
; Closes the running app before replacing its files (an update), and doesn't restart Windows
CloseApplications=yes
RestartApplications=no
WizardStyle=modern

[Files]
Source: "..\publish\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\Mnemerynne"; Filename: "{app}\Mnemerynne.Desktop.exe"
Name: "{userdesktop}\Mnemerynne"; Filename: "{app}\Mnemerynne.Desktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Put an icon on the desktop"; Flags: unchecked

[Registry]
; Start with Windows, in the tray (the drive needs the app running). Settings in the app can turn it off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Mnemerynne Desktop"; ValueData: """{app}\Mnemerynne.Desktop.exe"" --background"; Flags: uninsdeletevalue

[Run]
; After a normal install: offer to open it. After a silent update with /RELAUNCH=1: open it again.
Filename: "{app}\Mnemerynne.Desktop.exe"; Description: "Open Mnemerynne"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Mnemerynne.Desktop.exe"; Flags: nowait; Check: WantRelaunch

[UninstallRun]
; Take the drive(s) out of File Explorer first. Files already downloaded stay in their folders.
Filename: "{app}\Mnemerynne.Desktop.exe"; Parameters: "--unregister-drive"; Flags: waituntilterminated runhidden; RunOnceId: "UnregisterDrive"

[Code]
// /RELAUNCH=1 on the command line: the app's updater wants the app back once the files are replaced.
function WantRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

// The pages are drawn by the Microsoft Edge WebView2 Runtime. Windows 11 always has it; on Windows 10 it may be
// missing, so say where to get it (the app itself also says so if a tab can't start).
function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version)
         or RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if (not WebView2Installed) and (not WizardSilent) then
    MsgBox('Mnemerynne needs the Microsoft Edge WebView2 Runtime, which this computer doesn''t seem to have.' + #13#10 +
           'Install it from https://developer.microsoft.com/microsoft-edge/webview2/ (Evergreen) and then open Mnemerynne.',
           mbInformation, MB_OK);
end;
