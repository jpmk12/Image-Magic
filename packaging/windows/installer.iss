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

[Files]
Source: "..\..\dist\ImageMagic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Image Magic"; Filename: "{app}\ImageMagic.exe"
Name: "{group}\Uninstall Image Magic"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Image Magic"; Filename: "{app}\ImageMagic.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ImageMagic.exe"; Description: "Open Image Magic now"; Flags: nowait postinstall skipifsilent

; Uninstalling removes the program only. Your photos and settings are kept.
