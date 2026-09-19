$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\TeachingAgent'
$taskLogDir = Join-Path $taskRoot '.cache\runtime-setup'
New-Item -ItemType Directory -Force -Path $taskLogDir | Out-Null
$taskStatus = [ordered]@{ started_at = (Get-Date -Format o); phase = 'starting'; success = $false; reboot_required = $false }
function Save-Status { $taskStatus | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogDir 'wsl-setup.json') -Encoding UTF8 }
Save-Status
Start-Transcript -Path (Join-Path $taskLogDir 'wsl-setup.log') -Append
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator token required.' }
    $taskStatus.phase = 'enabling_virtual_machine_platform'; Save-Status
    $feature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
    if ($feature.State -ne 'Enabled') {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
        $taskStatus.reboot_required = [bool]$result.RestartNeeded
    }
    $taskStatus.phase = 'installing_wsl'; Save-Status
    $msi = Join-Path $taskRoot '.cache\installers\wsl.2.7.14.0.x64.msi'
    $signature = Get-AuthenticodeSignature -LiteralPath $msi
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'WSL installer signature is invalid.' }
    $install = Start-Process msiexec.exe -ArgumentList @('/i', $msi, '/qn', '/norestart', '/l*v', (Join-Path $taskLogDir 'wsl-msi.log')) -WindowStyle Hidden -Wait -PassThru
    $taskStatus.msi_exit_code = $install.ExitCode
    if ($install.ExitCode -notin @(0, 3010)) { throw "WSL MSI failed: $($install.ExitCode)" }
    if ($install.ExitCode -eq 3010) { $taskStatus.reboot_required = $true }
    $taskStatus.success = $true
    $taskStatus.phase = 'complete'
} catch {
    $taskStatus.phase = 'failed'
    $taskStatus.error = $_.Exception.Message
    Write-Output $_.Exception.Message
} finally {
    $taskStatus.finished_at = Get-Date -Format o
    Save-Status
    Stop-Transcript
}
if (-not $taskStatus.success) { exit 1 }
