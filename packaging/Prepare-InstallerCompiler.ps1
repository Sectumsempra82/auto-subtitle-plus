$ErrorActionPreference = 'Stop'
$toolRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.build/installer-tools'
$compilerDir = Join-Path $toolRoot 'inno-6.7.3'
$compiler = Join-Path $compilerDir 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null
    $archive = Join-Path $toolRoot 'innosetup-6.7.3.exe'
    $expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Compiler archive checksum mismatch' }
    $signature = Get-AuthenticodeSignature -LiteralPath $archive
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Pyrsys') { throw 'Compiler signature verification failed' }
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS', '/TASKS=', ('/DIR="' + $compilerDir + '"'))
    $process = Start-Process -FilePath $archive -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $compiler)) { throw 'Compiler preparation failed' }
}
Write-Output $compiler
