# Auto Subtitle Plus 0.3.0-rc.2

Desktop help update with rebuilt Windows x64 GUI and CLI editions.

## Changes

- Explanatory hover help for all 45 settings controls and their field labels,
  including help on labels for individually disabled controls.
- Expanded help for queue actions, table headings, stage progress, outputs,
  activity logs and resource meters.
- Six clickable links to the relevant online user-guide sections: one for the
  queue and one in each settings tab. Tooltips work offline; guide links open
  the browser and require internet access.
- Updated README, website, user guide and desktop screenshots.
- Processing behavior, defaults, model catalogue and dependency pins are unchanged.

## Download And Upgrade

Download the GUI or CLI ZIP and its matching SHA256 file. Extract the whole
archive into a writable folder and keep the supplied files together; the EXE
depends on its accompanying application payload. No installed Python is needed.

Close the old application before upgrading. Preserve your `data` folder, or
reuse its location through `AUTO_SUBTITLE_PLUS_DATA_DIR`, to retain downloaded
models, runtimes and desktop state. Exported subtitles remain in their chosen
output folders. Do not delete `data` merely to update the application.

First use downloads pinned, verified app-local dependencies and any selected
missing models. CPU is the initial profile; CUDA remains an explicit setup step.
There are no dependency pin changes in this release.

## Validation And Limitations

- All 211 regression tests passed. The rebuilt CLI passed offline startup/help;
  the rebuilt GUI opened and captured its real window using the prepared test runtime.
- All 45 settings controls have hover help; disabled-field label hover and all
  six guide section links were checked, with rendered tooltip inspection.
- This is an unsigned Windows 10/11 x64 pre-release. Clean-Windows certification
  and the CUDA-bootstrap acquisition path remain unverified. The tooltip update
  does not change the previous release's processing validation scope.

See the [user guide](https://sectumsempra82.github.io/auto-subtitle-plus/guide/)
and the packaged README for setup, dependencies, credits and model licences.
