#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef FileVersion
  #error FileVersion is required
#endif
#ifndef AppId
  #define AppId "local.autosubtitleplus.desktop"
#endif

[Setup]
AppId={#AppId}
AppName=Auto Subtitle Plus
AppVersion={#AppVersion}
AppPublisher=Auto Subtitle Plus contributors
AppPublisherURL=https://github.com/Sectumsempra82/auto-subtitle-plus
DefaultDirName={localappdata}\Programs\Auto Subtitle Plus
DefaultGroupName=Auto Subtitle Plus
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UsePreviousAppDir=yes
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
AppMutex=AutoSubtitlePlus.Running
SetupMutex=AutoSubtitlePlus.Setup
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\auto_subtitle_plus_gui.exe
VersionInfoVersion={#FileVersion}
OutputBaseFilename=AutoSubtitlePlus-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
InfoBeforeFile=WINDOWS-INSTALLER.txt

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Replace only the validated, application-owned Python package, never data.
Type: filesandordirs; Name: "{app}\app\auto_subtitle_plus"

[INI]
Filename: "{app}\installation.ini"; Section: "Application"; Key: "Id"; String: "{#AppId}"; Flags: uninsdeleteentry
Filename: "{app}\installation.ini"; Section: "Application"; Key: "Version"; String: "{#FileVersion}"; Flags: uninsdeleteentry

[UninstallDelete]
Type: files; Name: "{app}\installation.ini"

[Icons]
Name: "{group}\Auto Subtitle Plus"; Filename: "{app}\auto_subtitle_plus_gui.exe"; WorkingDir: "{app}"
Name: "{group}\User guide"; Filename: "https://sectumsempra82.github.io/auto-subtitle-plus/guide/"
Name: "{autodesktop}\Auto Subtitle Plus"; Filename: "{app}\auto_subtitle_plus_gui.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
function GetFileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function OpenExclusive(Name: String; Access, Share: LongWord; Security: Integer;
  Creation, Flags: LongWord; Template: Integer): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsLinked(Path: String): Boolean;
var Attributes: LongWord;
begin
  Attributes := GetFileAttributes(Path);
  Result := (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0);
end;

function ContainsLink(Path: String): Boolean;
var Found: TFindRec;
begin
  Result := IsLinked(Path);
  if Result or not DirExists(Path) then exit;
  if FindFirst(AddBackslash(Path) + '*', Found) then begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then begin
          Result := IsLinked(AddBackslash(Path) + Found.Name);
          if not Result and ((Found.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) then
            Result := ContainsLink(AddBackslash(Path) + Found.Name);
          if Result then exit;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

function DirectoryHasFiles(Path: String): Boolean;
var Found: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Path) + '*', Found) then begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then begin
          Result := True;
          exit;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Destination, Parent, InstalledVersion, Executable: String;
  Installed, Incoming, BinaryVersion: Int64;
  Handle: THandle;
  Index: Integer;
begin
  Result := '';
  Destination := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  Parent := Destination;
  repeat
    if IsLinked(Parent) then begin
      Result := 'Choose an installation folder without symbolic links or junctions.';
      exit;
    end;
    Executable := ExtractFileDir(Parent);
    if Executable = Parent then break;
    Parent := Executable;
  until Parent = '';
  if ContainsLink(Destination + '\app') then begin
    Result := 'The application code folder contains a symbolic link or junction. No files were changed.';
    exit;
  end;
  if DirectoryHasFiles(Destination) and
     (GetIniString('Application', 'Id', '', Destination + '\installation.ini') <> '{#AppId}') and
     not (FileExists(Destination + '\app\auto_subtitle_plus\__init__.py') and
          FileExists(Destination + '\dependencies-windows.json') and
          (FileExists(Destination + '\auto_subtitle_plus_gui.exe') or
           FileExists(Destination + '\auto_subtitle_plus.exe'))) then begin
    Result := 'Choose an empty folder or an existing Auto Subtitle Plus application folder.';
    exit;
  end;
  InstalledVersion := GetIniString('Application', 'Version', '0.0.0.0', Destination + '\installation.ini');
  if not StrToVersion(InstalledVersion, Installed) then begin
    Result := 'The installed version cannot be read. Repair the installation record before updating.';
    exit;
  end;
  StrToVersion('{#FileVersion}', Incoming);
  if GetVersionNumbersString(Destination + '\auto_subtitle_plus_gui.exe', Executable) and
     StrToVersion(Executable, BinaryVersion) then
    if ComparePackedVersion(BinaryVersion, Installed) > 0 then Installed := BinaryVersion;
  if GetVersionNumbersString(Destination + '\auto_subtitle_plus.exe', Executable) and
     StrToVersion(Executable, BinaryVersion) then
    if ComparePackedVersion(BinaryVersion, Installed) > 0 then Installed := BinaryVersion;
  if ComparePackedVersion(Installed, Incoming) > 0 then begin
    Result := 'A newer version is already installed. Downgrades are not supported.';
    exit;
  end;
  if CheckForMutexes('AutoSubtitlePlus.Running') then begin
    Result := 'Finish running jobs and close Auto Subtitle Plus before updating.';
    exit;
  end;
  for Index := 0 to 1 do begin
    if Index = 0 then Executable := Destination + '\auto_subtitle_plus.exe'
    else Executable := Destination + '\auto_subtitle_plus_gui.exe';
    if FileExists(Executable) then begin
      Handle := OpenExclusive(Executable, $40000000, 0, 0, 3, 0, 0);
      if Handle = THandle(-1) then begin
        Result := 'Close Auto Subtitle Plus and check write access to the installation folder before updating.';
        exit;
      end;
      CloseHandle(Handle);
    end;
  end;
end;
