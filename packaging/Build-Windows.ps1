[CmdletBinding()]
param([string]$PythonExe, [string]$Output, [switch]$SkipTests, [switch]$PayloadOnly, [switch]$KeepPayload, [string]$InstallerCompiler)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $PythonExe = (& py -3.13 -c 'import sys; print(sys.executable)').Trim() }
    else { throw 'Build requires Python 3.13, or pass -PythonExe. End users need no installed Python.' }
}
if (-not $Output) { $Output = Join-Path $root 'dist/windows-light' }
Push-Location $root
try {
    if (-not $SkipTests) {
        if (-not $env:QT_QPA_FONTDIR) { $env:QT_QPA_FONTDIR = Join-Path $env:SystemRoot 'Fonts' }
        & $PythonExe -m unittest discover -s tests -q
        if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed. No build produced.' }
    }
    # Two payload folders are an internal build detail (one launcher per edition);
    # they are never the distributed artifact. --no-zip skips packaging them
    # individually since only the combined installer EXE below ships.
    & $PythonExe tools/build_bootstrap.py --output $Output --no-zip
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    if ($PayloadOnly) {
        Write-Host "Payload folders built in $Output for local inspection (-PayloadOnly). No installer EXE was produced."
        return
    }
    if (-not $InstallerCompiler) { $InstallerCompiler = & (Join-Path $PSScriptRoot 'Prepare-InstallerCompiler.ps1') }
    & $PythonExe -m tools.package_windows --output $Output --compiler $InstallerCompiler
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    if (-not $KeepPayload) {
        Remove-Item -Recurse -Force (Join-Path $Output 'AutoSubtitlePlus-CLI'), (Join-Path $Output 'AutoSubtitlePlus-GUI') -ErrorAction SilentlyContinue
    }
    Write-Host "Single Windows EXE built in $Output. Running it asks Install or Portable, then a destination folder."
} finally { Pop-Location }
