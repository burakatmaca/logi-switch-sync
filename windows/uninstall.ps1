#Requires -RunAsAdministrator
# logi-switch-sync uninstaller for Windows
$TaskName = "logi-switch-sync"
$RuleName = "logi-switch-sync clipboard"
$AppDir   = Join-Path $env:LOCALAPPDATA "logi-switch-sync"

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -like "*logi_switch.py*follow*" } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Scheduled task and firewall rule removed."

$del = Read-Host "Also delete $AppDir (config, log, Python environment)? [y/N]"
if ($del -match "^[Yy]") {
    Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
    Write-Host "Deleted $AppDir."
}
Write-Host "Done."
