[CmdletBinding()]
param([string]$PythonExe, [string]$Output, [switch]$SkipTests, [switch]$NoZip, [switch]$Installer, [string]$InstallerCompiler)
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
    $buildArgs = @('tools/build_bootstrap.py', '--output', $Output)
    if ($NoZip) { $buildArgs += '--no-zip' }
    & $PythonExe @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    if ($Installer) {
        if (-not $InstallerCompiler) { $InstallerCompiler = & (Join-Path $PSScriptRoot 'Prepare-InstallerCompiler.ps1') }
        & $PythonExe -m tools.package_windows --output $Output --compiler $InstallerCompiler
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    }
    Write-Host "Small CLI and GUI editions built in $Output. First run prepares app-local dependencies."
} finally { Pop-Location }
