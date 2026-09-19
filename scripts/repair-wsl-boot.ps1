$ErrorActionPreference = 'Stop'
$taskDir = 'D:\TeachingAgent\.cache\runtime-setup'
$taskResult = [ordered]@{ started_at=(Get-Date -Format o); phase='starting'; success=$false; reboot_required=$false }
function Save-RepairStatus { $taskResult | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $taskDir 'wsl-boot-repair.json') -Encoding UTF8 }
Save-RepairStatus
try {
    $taskResult.boot_config_before = @(& bcdedit.exe /enum all 2>&1 | ForEach-Object { $_.ToString() })
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read boot configuration.' }
    $taskBackup = Join-Path $taskDir ('bcd-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    & bcdedit.exe /export $taskBackup | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot back up boot configuration.' }
    $taskResult.boot_backup = $taskBackup
    $taskResult.phase = 'enabling_wsl_component'; Save-RepairStatus
    $taskFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
    if ($taskFeature.State -ne 'Enabled') {
        $taskEnabled = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
        $taskResult.reboot_required = [bool]$taskEnabled.RestartNeeded
    }
    $taskResult.phase = 'enabling_hypervisor_boot'; Save-RepairStatus
    & bcdedit.exe /set hypervisorlaunchtype auto | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot enable hypervisor boot.' }
    $taskResult.reboot_required = $true
    $taskResult.boot_config_after = @(& bcdedit.exe /enum 2>&1 | ForEach-Object { $_.ToString() })
    $taskResult.success = $true
    $taskResult.phase = 'complete'
} catch {
    $taskResult.phase = 'failed'
    $taskResult.error = $_.Exception.Message
} finally {
    $taskResult.finished_at = Get-Date -Format o
    Save-RepairStatus
}
if (-not $taskResult.success) { exit 1 }
