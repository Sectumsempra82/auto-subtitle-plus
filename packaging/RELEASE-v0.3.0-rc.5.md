# v0.3.0-rc.5 — Windows installer and text transcription

The Windows desktop and CLI now ship together in a small per-user installer.
Separate portable GUI and CLI ZIPs remain available. The Mac release remains
at v0.3.0-rc.4.

## Install or update

Download `AutoSubtitlePlus-Setup-Windows-x64.exe` and its `.sha256` file.
Verify the checksum, close Auto Subtitle Plus and run the installer. It needs
no administrator rights and defaults to `%LOCALAPPDATA%\Programs\Auto Subtitle Plus`.

For an existing installer installation, run the newer installer without
uninstalling first. It reuses the registered folder, replaces application code,
removes obsolete Python modules and preserves `data`, including settings,
queue, model caches, downloaded archives and prepared runtimes. Reinstalling
the same version is supported; downgrades are blocked.

For an existing portable ZIP, select its extracted application folder in Setup.
The existing `data` stays there. Other copies are not moved or deleted.
`AUTO_SUBTITLE_PLUS_DATA_DIR` overrides continue to work. Uninstall preserves
user data and exported files.

Setup does not start the application, download dependencies or install updates
automatically. First launch prepares the pinned app-local dependencies; CPU is
the default and CUDA remains opt-in. Model weights download separately.

## Changes

- Native Windows installer with a stable installation identity, manual upgrades,
  same-version repair, downgrade protection and running-application guards.
- Text transcript shortcut, clearer TXT output guidance and current hardware help
  in the compiled Windows desktop; recognition and translation defaults unchanged.
- More useful bootstrap download diagnostics, retry delays and offline recovery
  instructions; verified cached archives remain reusable.
- Website, user guide, screenshots and rebuild instructions updated together.

## Validation and limits

- Python regression suite: 227 tests, 3 macOS-specific skips; all runnable tests passed.
- Actual Windows installer tests: fresh installation, ZIP migration, registered-folder
  upgrade, same-version reinstall, downgrade rejection, running-app and legacy-file
  lock rejection, obsolete module removal and uninstall.
- Settings, queue, model/runtime/download fixtures and an exported transcript were
  preserved byte-for-byte across upgrades and uninstall.
- Compiled CLI offline startup and compiled GUI launch checked against cached
  app-local dependencies; screenshots refreshed using example files.
- Windows/Mac release-selection tests and local website links/anchors checked.

Installer behavior follows Inno Setup's [stable AppId upgrade support](https://jrsoftware.org/isfaq.php)
and [application mutex checks](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm).
The repeatable build uses the verified official Inno Setup 6.7.3 compiler.

The installer and launchers are unsigned. Windows SmartScreen may warn; verify
the source and checksum before running. Do not disable system protection.
Validation on this Windows development machine is not clean-machine certification
or validation of every hardware, model and output combination.

[Downloads and guide](https://sectumsempra82.github.io/auto-subtitle-plus/)
