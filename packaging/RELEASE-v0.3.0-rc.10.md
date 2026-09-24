# v0.3.0-rc.10 — One Windows EXE for Install and Portable

Windows now ships as a single EXE instead of two separate downloads (a portable ZIP pair and a separate installer). The Mac desktop and CLI are unchanged in this release; this build number exists to publish both platforms from the same tag going forward.

## Windows: Install or Portable, one download

Running `AutoSubtitlePlus-Windows-x64.exe` now asks:

1. **Install** (Start Menu shortcut, optional desktop shortcut, uninstaller) or **Portable** (copies the application files only — nothing else on the computer changes; for example, onto a USB drive). Install is the default.
2. A destination folder, defaulting to `%ProgramFiles%\Auto Subtitle Plus` either way. Browse to pick anywhere else.
3. If that folder already has files, whether to update/overwrite it in place.

Because Program Files is the default destination for both choices, Setup always asks for one Windows administrator elevation, even when Portable is chosen; a Portable run still only copies files into the folder you picked. There is no more separate `AutoSubtitlePlus-CLI-Windows-x64.zip` / `AutoSubtitlePlus-GUI-Windows-x64.zip` / `AutoSubtitlePlus-Setup-Windows-x64.exe` set of downloads.

Upgrading an existing copy, Install or Portable, from any earlier release: point Setup at its existing application folder (the one containing `auto_subtitle_plus_gui.exe` or `auto_subtitle_plus.exe`). Its `data` folder, settings, queue, models, and cached downloads are preserved exactly as before. Same-version reinstall is supported; downgrades remain blocked.

See the [packaging notes](https://github.com/Sectumsempra82/auto-subtitle-plus/blob/v0.3.0-rc.10/packaging/windows-installer.iss) and [Windows update guide](https://sectumsempra82.github.io/auto-subtitle-plus/guide/#windows-updates).

## Every release now publishes both platforms

Pushing a `v*` tag now runs a Windows build and a macOS build in parallel, and both publish their executables to the same GitHub Release. Releases going forward always include the Windows EXE and the Mac PKG/ZIP together.

The package version is `0.3.0rc10`, Git tag `v0.3.0-rc.10`. Mac bundle/installer build number `10` (unchanged app; rebuilt to accompany this tag, and the first build produced by the new automated macOS workflow). The Windows EXE is unsigned; verify its SHA-256 before running it.
