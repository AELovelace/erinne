; Inno Setup script: a per-user installer (no admin rights) for Mnemerynne Backup. build-windows.ps1 runs it.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3C1A52-2B7E-4C5B-9E57-0B5D1C7A9E21}
AppName=Mnemerynne Backup
AppVersion={#AppVersion}
AppPublisher=Sad Girls Club
DefaultDirName={localappdata}\Programs\Mnemerynne Backup
DefaultGroupName=Mnemerynne Backup
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Mnemerynne-Backup-Setup-{#AppVersion}
SetupIconFile=build\icon.ico
UninstallDisplayIcon={app}\mnemerynne-backup-tray.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=yes

[Files]
Source: "..\dist\mnemerynne-backup\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\Mnemerynne Backup"; Filename: "{app}\mnemerynne-backup-tray.exe"

[Registry]
; Start at sign-in (the app's own Settings can turn this off again).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Mnemerynne Backup"; ValueData: """{app}\mnemerynne-backup-tray.exe"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\mnemerynne-backup-tray.exe"; Description: "Start Mnemerynne Backup"; Flags: nowait postinstall
