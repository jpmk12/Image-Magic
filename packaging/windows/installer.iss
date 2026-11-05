; Inno Setup script: builds ImageMagic-Setup.exe from dist\ImageMagic\
; Installs per-user (no admin prompt), adds Start menu + desktop shortcuts.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7B3E4A52-1C8D-4F0E-9A61-2D5C8E9B4F17}
AppName=Image Magic
AppVersion={#AppVersion}
AppPublisher=Image Magic
DefaultDirName={localappdata}\Programs\Image Magic
DefaultGroupName=Image Magic
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=ImageMagic-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ImageMagic.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "firewall"; Description: "Block network access to the iCloud sign-in helper (recommended; asks for administrator approval)"; GroupDescription: "Security:"

[Files]
Source: "..\..\dist\ImageMagic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Image Magic"; Filename: "{app}\ImageMagic.exe"
Name: "{group}\Uninstall Image Magic"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Image Magic"; Filename: "{app}\ImageMagic.exe"; Tasks: desktopicon

[Run]
; icloudpd's sign-in page listens on all network interfaces while it waits for
; your password. This inbound block rule keeps it reachable only from this PC.
; Outgoing connections to Apple are not affected. Block rules override "Allow".
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""Image Magic sign-in helper"" >nul 2>&1 & netsh advfirewall firewall add rule name=""Image Magic sign-in helper"" dir=in action=block program=""{app}\icloudpd.exe"" enable=yes profile=any"; Verb: runas; Flags: shellexec runhidden waituntilterminated; Tasks: firewall; StatusMsg: "Adding firewall protection..."
Filename: "{app}\ImageMagic.exe"; Description: "Open Image Magic now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""Image Magic sign-in helper"""; Verb: runas; Flags: shellexec runhidden waituntilterminated; Tasks: firewall; RunOnceId: "RemoveFirewallRule"

; Uninstalling removes the program only. Your photos and settings are kept.
