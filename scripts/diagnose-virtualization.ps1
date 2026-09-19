$ErrorActionPreference = 'Stop'
$taskOutput = 'D:\TeachingAgent\.cache\runtime-setup\virtualization-diagnostic.json'
$taskResult = [ordered]@{ checked_at=(Get-Date -Format o) }
try {
    $taskResult.features = @(Get-WindowsOptionalFeature -Online | Where-Object FeatureName -in @('VirtualMachinePlatform','Microsoft-Windows-Subsystem-Linux') | Select-Object FeatureName,@{Name='State';Expression={$_.State.ToString()}})
    $taskResult.boot_config = @(& bcdedit.exe /enum 2>&1 | ForEach-Object { $_.ToString() })
    $taskResult.boot_config_exit = $LASTEXITCODE
    $taskResult.hypervisor_events = @(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Hyper-V-Hypervisor';StartTime=(Get-Date).AddDays(-1)} -MaxEvents 8 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,Message)
} catch { $taskResult.error = $_.Exception.Message }
$taskResult | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskOutput -Encoding UTF8
