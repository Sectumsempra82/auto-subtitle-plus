$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = $PSScriptRoot
$data = if ($env:AUTO_SUBTITLE_PLUS_DATA_DIR) { [IO.Path]::GetFullPath($env:AUTO_SUBTITLE_PLUS_DATA_DIR) } else { Join-Path $root 'data' }
$env:AUTO_SUBTITLE_PLUS_DATA_DIR = $data
$manifest = Get-Content -LiteralPath (Join-Path $root 'dependencies-windows.json') -Raw | ConvertFrom-Json
function Write-Status([string]$Message) { [Console]::Error.WriteLine($Message) }
$pythonRoot = Join-Path $data ('bootstrap/' + $manifest.python.sha256.Substring(0,20))
$python = Join-Path $pythonRoot 'python.exe'
try {
    $arguments = if ($env:ASP_ARGUMENTS) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:ASP_ARGUMENTS)) | ConvertFrom-Json } else { @() }
    $offline = $arguments -contains '--offline'
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(10)
    $announced = $false
    while ($true) {
        try { $lock = [IO.File]::Open((Join-Path $data 'python-setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None'); break }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Another Python setup is still running; retry later.' }
            if (-not $announced) { Write-Status 'Waiting for app-local Python setup...'; $announced = $true }
            Start-Sleep -Seconds 1
        }
    }
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $pythonRoot 'complete'))) {
            if ($arguments -contains '--check-dependencies') { Write-Output '{"ready":false,"reason":"Python runtime is not prepared"}'; exit 2 }
            $cache = Join-Path $data 'downloads'
            New-Item -ItemType Directory -Force -Path $cache | Out-Null
            $archive = Join-Path $cache $manifest.python.filename
            $valid = (Test-Path -LiteralPath $archive) -and ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $manifest.python.sha256)
            if (-not $valid) {
                if ($offline) { throw 'Offline: Python runtime archive is missing or corrupt.' }
                $uri = [Uri]$manifest.python.url
                if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'www.python.org') { throw 'Unapproved Python download source.' }
                Write-Status 'Downloading official app-local Python (11 MiB)...'
                $partial = $archive + '.partial'
                for ($attempt = 0; $attempt -lt 3; $attempt++) {
                    try {
                        $request = [Net.HttpWebRequest]::Create($uri)
                        $request.Timeout = 60000
                        $request.ReadWriteTimeout = 60000
                        $response = $request.GetResponse()
                        try {
                            if ($response.ContentLength -gt $manifest.python.size) { throw 'Python download exceeds expected size.' }
                            $inputStream = $response.GetResponseStream()
                            $outputStream = [IO.File]::Create($partial)
                            try {
                                $buffer = New-Object byte[] 1048576
                                $count = 0L
                                while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                                    $count += $read
                                    if ($count -gt $manifest.python.size) { throw 'Python download exceeds expected size.' }
                                    $outputStream.Write($buffer, 0, $read)
                                }
                                if ($count -ne $manifest.python.size) { throw 'Python download is incomplete.' }
                            } finally { $outputStream.Dispose(); $inputStream.Dispose() }
                        } finally { $response.Dispose() }
                        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.python.sha256) { throw 'Python download checksum mismatch.' }
                        Move-Item -LiteralPath $partial -Destination $archive -Force
                        break
                    } catch { if ($attempt -eq 2) { throw }; Start-Sleep -Seconds ($attempt + 1) }
                }
            }
            $stage = $pythonRoot + '.pending-' + [Guid]::NewGuid().ToString('N')
            Expand-Archive -LiteralPath $archive -DestinationPath $stage
            & (Join-Path $stage 'python.exe') -I -c 'import ssl, zipfile, hashlib'
            if ($LASTEXITCODE -ne 0) { throw 'App-local Python cannot start; no system installation was attempted.' }
            Set-Content -LiteralPath (Join-Path $stage 'complete') -Value $manifest.python.sha256
            if (Test-Path -LiteralPath $pythonRoot) { Move-Item -LiteralPath $pythonRoot -Destination ($pythonRoot + '.previous-' + [Guid]::NewGuid().ToString('N')) }
            Move-Item -LiteralPath $stage -Destination $pythonRoot
        }
    } finally { $lock.Dispose() }
    & $python -I -u (Join-Path $root 'bootstrap.py')
    exit $LASTEXITCODE
} catch {
    Write-Status ('Setup failed: ' + $_.Exception.Message)
    Write-Status 'No system-wide installation was attempted. Check write access/network and retry.'
    exit 1
}
