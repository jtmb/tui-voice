#requires -Version 5.1
<#
.SYNOPSIS
Stages the AMD ROCm 10.1 Windows driver; installs only with -Install.
.DESCRIPTION
Default: inventory, download, validate the executable/signature, and save resume
instructions locally. -Install requests normal UAC and opens AMD's installer.
-Verify checks the active device driver after restart. No automatic reboot.
#>
[CmdletBinding(DefaultParameterSetName = 'Stage')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Install')]
    [switch]$Install,
    [Parameter(Mandatory = $true, ParameterSetName = 'Verify')]
    [switch]$Verify,
    [Parameter(ParameterSetName = 'Stage')]
    [Parameter(ParameterSetName = 'Install')]
    [string]$InstallerPath,
    [string]$StateDirectory,
    [string]$DevicePattern = '*RX 7900*'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script with Windows PowerShell.' }
$driverVersion = '26.10.41.05'
$expectedStoreVersion = [version]'32.0.31041.5005'
$fileName = 'amd-software-adrenalin-edition-26.10.41.05-win11-b-204865.exe'
$downloadUrl = 'https://drivers.amd.com/drivers/' + $fileName
$releaseUrl = 'https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-ROCM-10-01.html'

function Save-Json($Value, [string]$Path) {
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}
function Get-HostSnapshot {
    $display = @(Get-CimInstance Win32_VideoController | Select-Object Name, PNPDeviceID,
        DriverVersion, DriverDate, ConfigManagerErrorCode)
    $windows = Get-CimInstance Win32_OperatingSystem |
        Select-Object Caption, Version, BuildNumber, LastBootUpTime
    $wslVersion = 'wsl.exe not found'
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        try { $wslVersion = (& wsl.exe --version 2>&1 | Out-String).Trim() }
        catch { $wslVersion = 'Version query failed: ' + $_.Exception.Message }
    }
    return [ordered]@{
        recorded_utc = [DateTime]::UtcNow.ToString('o')
        windows = $windows
        display = $display
        wsl_version = $wslVersion
    }
}
function Save-InstallerLog([string]$Directory) {
    # AMD documents this location; retain its timestamp rather than claiming it is fresh.
    $programDirectory = $env:ProgramW6432
    if (-not $programDirectory) { $programDirectory = $env:ProgramFiles }
    $logPath = Join-Path $programDirectory 'AMD\CIM\Log\Install.log'
    try {
        if (-not (Test-Path -LiteralPath $logPath)) { return @{ status = 'not_found' } }
        $log = Get-Item -LiteralPath $logPath
        Copy-Item -LiteralPath $logPath -Destination (Join-Path $Directory 'amd-install.log')
        return @{ status = 'copied'; source = $logPath; last_write_utc = $log.LastWriteTimeUtc.ToString('o') }
    } catch { return @{ status = 'unavailable'; error = $_.Exception.Message } }
}
function Get-ValidatedInstaller([string]$Path) {
    $file = Get-Item -LiteralPath $Path
    if ($file.PSIsContainer -or $file.Length -lt 256) {
        throw 'The download is not a Windows installer.'
    }
    $stream = [IO.File]::OpenRead($file.FullName)
    $reader = New-Object IO.BinaryReader($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw 'Missing EXE header; AMD may have returned an HTML download-error page.'
        }
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        if ($peOffset -lt 64 -or $peOffset -gt ($stream.Length - 4)) {
            throw 'Invalid Windows executable header.'
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) { throw 'Invalid PE executable signature.' }
    } finally { $reader.Dispose() }
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
        throw "Installer Authenticode validation failed: $($signature.Status)"
    }
    $signer = $signature.SignerCertificate.GetNameInfo(
        [Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    if ($signer -notmatch '^Advanced Micro Devices(?:,?\s+Inc\.?)?$') {
        throw "Expected AMD's signature; found: $signer"
    }
    return [ordered]@{
        path = $file.FullName
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        signature_status = [string]$signature.Status
        signer = $signer
        certificate_thumbprint = $signature.SignerCertificate.Thumbprint
        file_version = $file.VersionInfo.FileVersion
        product_version = $file.VersionInfo.ProductVersion
    }
}

if (-not $StateDirectory) {
    $StateDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'tui-voice\gpu-remediation'
}
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$runDirectory = Join-Path $StateDirectory $runId
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$transcribing = $false
$stateLock = $null
$exitCode = 1
$partialPath = $null
$result = [ordered]@{
    mode = $PSCmdlet.ParameterSetName
    target_adrenalin = $driverVersion
    target_driver_store = [string]$expectedStoreVersion
    release_url = $releaseUrl
    download_url = $downloadUrl
    status = 'starting'
}
try {
    try {
        $stateLock = [IO.File]::Open((Join-Path $StateDirectory 'operation.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw "Cannot lock the staging directory; another operation may be running: $($_.Exception.Message)"
    }
    Start-Transcript -LiteralPath (Join-Path $runDirectory 'session.log') | Out-Null
    $transcribing = $true
    $snapshot = Get-HostSnapshot
    Save-Json $snapshot (Join-Path $runDirectory 'host.json')
    $devices = @($snapshot.display | Where-Object {
        $_.Name -like $DevicePattern -and $_.PNPDeviceID -match 'VEN_1002'
    })
    if ($devices.Count -eq 0) {
        throw "No AMD display device matched '$DevicePattern'. Inspect host.json and select -DevicePattern."
    }
    $devices | Format-Table Name, DriverVersion, ConfigManagerErrorCode | Out-Host

    # Retain a Windows-native copy so verification does not depend on WSL running.
    $resumeScript = Join-Path $StateDirectory 'prepare_amd_driver.ps1'
    if ([IO.Path]::GetFullPath($PSCommandPath) -ne $resumeScript) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $resumeScript -Force
    }
    $quotedScript = "'" + $resumeScript.Replace("'", "''") + "'"
    $quotedState = "'" + $StateDirectory.Replace("'", "''") + "'"
    $quotedDevice = "'" + $DevicePattern.Replace("'", "''") + "'"
    @(
        'Save all work before any Windows/WSL restart. This tool never schedules one.'
        'After a controlled restart, run in Windows PowerShell:'
        "& $quotedScript -Verify -StateDirectory $quotedState -DevicePattern $quotedDevice"
        'Then reopen the same WSL checkout and continue docs/AMD_GPU_REMEDIATION.md at Runtime verification.'
        'The driver check does not establish GPU execution. Preserve the pre-upgrade Python freeze separately.'
        "Current run evidence: $runDirectory"
        'Keep this host inventory and installer logs local.'
    ) | Set-Content -LiteralPath (Join-Path $StateDirectory 'RESUME.txt') -Encoding UTF8

    $atTarget = @($devices | Where-Object {
        $_.DriverVersion -ne [string]$expectedStoreVersion -or $_.ConfigManagerErrorCode -ne 0
    }).Count -eq 0
    $newer = @($devices | Where-Object { [version]$_.DriverVersion -gt $expectedStoreVersion }).Count -gt 0
    if ($Verify) {
        $result['installer_log'] = Save-InstallerLog $runDirectory
        if ($atTarget) {
            $result.status = 'target_driver_active'
            $exitCode = 0
            Write-Host 'Expected driver is active. Continue the WSL runtime and Kokoro execution gates.'
        } else {
            $result.status = 'driver_version_requires_review'
            $exitCode = 2
            Write-Warning 'Expected active driver was not verified. A newer version needs compatibility review, not an automatic downgrade.'
        }
    } elseif ($atTarget) {
        $result.status = 'target_driver_already_active'
        $exitCode = 0
        Write-Host 'Target driver is already active; installation skipped. Continue runtime verification.'
    } elseif ($newer) {
        $result.status = 'newer_driver_requires_review'
        $exitCode = 2
        Write-Warning 'A newer driver is active. No download or downgrade was attempted. Check AMD compatibility and test the runtime.'
    } else {
        $stagedPath = Join-Path $StateDirectory $fileName
        if ($InstallerPath) {
            $provided = (Get-Item -LiteralPath $InstallerPath).FullName
            if ([IO.Path]::GetFileName($provided) -ne $fileName) {
                throw "Use the exact package from the official release page, named $fileName."
            }
            $null = Get-ValidatedInstaller $provided
            if ($provided -ne $stagedPath) { Copy-Item -LiteralPath $provided -Destination $stagedPath -Force }
            $result['source'] = 'provided_package_check_release_origin'
        } elseif (-not (Test-Path -LiteralPath $stagedPath)) {
            $partialPath = Join-Path $StateDirectory ($runId + '.download.exe')
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $downloadUrl -OutFile $partialPath -UseBasicParsing -TimeoutSec 1800 -Headers @{ Referer = $releaseUrl }
            $null = Get-ValidatedInstaller $partialPath
            Move-Item -LiteralPath $partialPath -Destination $stagedPath
            $partialPath = $null
            $result['source'] = 'official_download'
        } else { $result['source'] = 'revalidated_staged_package' }
        $result['installer'] = Get-ValidatedInstaller $stagedPath
        Save-Json $result (Join-Path $runDirectory 'result.json')
        Write-Host "Validated installer: $stagedPath"
        Write-Host "Local SHA-256 audit identifier: $($result.installer.sha256)"
        if ($Install) {
            Write-Host 'Opening the normal AMD installer through UAC. Choose an ordinary update; save work before restarting.'
            $process = Start-Process -FilePath $stagedPath -Verb RunAs -Wait -PassThru
            $result['installer_exit_code'] = $process.ExitCode
            $result['installer_log'] = Save-InstallerLog $runDirectory
            $result.status = 'installer_exited_verify_after_restart'
            if ($process.ExitCode -eq 0 -or $process.ExitCode -eq 3010) { $exitCode = 0 }
            else { $exitCode = 1 }
            Write-Host "Installer exit code: $($process.ExitCode). This is not proof of an active driver."
        } else {
            $result.status = 'staged_not_installed'
            $exitCode = 0
            Write-Host 'Staging complete. Use -Install when ready for normal UAC and installer interaction.'
        }
    }
} catch {
    $result.status = 'failed'
    $result['error'] = $_.Exception.Message
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    Write-Host "If AMD blocks the download, obtain the exact package from $releaseUrl and rerun with -InstallerPath."
} finally {
    try {
        if ($partialPath -and (Test-Path -LiteralPath $partialPath)) {
            Remove-Item -LiteralPath $partialPath -Force
        }
        $result['recorded_utc'] = [DateTime]::UtcNow.ToString('o')
        Save-Json $result (Join-Path $runDirectory 'result.json')
        if ($transcribing) { Stop-Transcript | Out-Null }
        Write-Host "Local evidence: $runDirectory"
    } finally {
        if ($stateLock) { $stateLock.Dispose() }
    }
}
exit $exitCode
