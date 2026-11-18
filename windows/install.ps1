#Requires -RunAsAdministrator
# logi-switch-sync installer for Windows
# Installs into %LOCALAPPDATA%\logi-switch-sync and starts at logon via Task Scheduler (no console window).
$ErrorActionPreference = "Stop"

$TaskName = "logi-switch-sync"
$RuleName = "logi-switch-sync clipboard"
$AppDir   = Join-Path $env:LOCALAPPDATA "logi-switch-sync"
$SrcDir   = Split-Path -Parent $PSScriptRoot
$Config   = Join-Path $AppDir "config.json"
$Log      = Join-Path $AppDir "follow.log"
$Script   = Join-Path $AppDir "logi_switch.py"

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "xx  $m" -ForegroundColor Red; exit 1 }

function Stop-Running {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like "*logi_switch.py*follow*" } |
        ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }
}

# --- find Python 3 -----------------------------------------------------------
$py = $null
foreach ($cand in @(@{ exe = "py"; pre = @("-3") }, @{ exe = "python"; pre = @() })) {
    try {
        $pre = $cand.pre
        $v = & $cand.exe @pre -c "import sys; print(sys.version_info[0])" 2>$null
        if ($v -eq "3") { $py = $cand; break }
    } catch {}
}
if (-not $py) { Die "Python 3 not found. Install it with:  winget install Python.Python.3.12  (then reopen PowerShell)" }

Say "Installing to $AppDir"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item (Join-Path $SrcDir "logi_switch.py") $AppDir -Force
$Venv = Join-Path $AppDir ".venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    $pre = $py.pre
    & $py.exe @pre -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die "Could not create the Python virtual environment." }
}
$VPy  = Join-Path $Venv "Scripts\python.exe"
$VPyW = Join-Path $Venv "Scripts\pythonw.exe"
& $VPy -m pip install -q --disable-pip-version-check -r (Join-Path $SrcDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Die "pip install failed." }

Stop-Running
Say "Devices on the receiver (keyboard and mouse should be on THIS computer now):"
& $VPy $Script list
Write-Host ""

$keep = "n"
if (Test-Path $Config) {
    $keep = Read-Host "An existing configuration was found. Keep it? [Y/n]"
    if (-not $keep) { $keep = "y" }
}

if ($keep -notmatch "^[Yy]") {
    Write-Host "When you press an Easy-Switch key to LEAVE this computer, which channel"
    $to = Read-Host "should the mouse follow to? (the other computer's channel, 1-3)"
    if ($to -notmatch "^[123]$") { Die "Channel must be 1, 2 or 3." }
    $cfg = [ordered]@{ to = [int]$to }

    $clip = Read-Host "Enable clipboard text sync with the other computer? [y/N]"
    if ($clip -match "^[Yy]") {
        $peer = Read-Host "IP address of the other computer"
        if (-not $peer) { Die "IP address is required for clipboard sync." }
        $secret = Read-Host "Shared secret (leave empty to generate a new one)"
        if (-not $secret) {
            $bytes = New-Object byte[] 16
            [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $secret = -join ($bytes | ForEach-Object { $_.ToString("x2") })
        }
        $cfg.peer = $peer; $cfg.secret = $secret; $cfg.port = 47821
    }
    [IO.File]::WriteAllText($Config, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
}

$cfgNow = Get-Content $Config -Raw | ConvertFrom-Json

# --- firewall (only needed to RECEIVE clipboard text) -------------------------
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($cfgNow.peer) {
    $port = if ($cfgNow.port) { $cfgNow.port } else { 47821 }
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $port `
        -Action Allow -Profile Private,Domain | Out-Null
    $public = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" }
    if ($public) {
        Warn "Your network '$($public[0].Name)' is marked Public, so incoming clipboard text is blocked."
        Warn "If this is your home/office network, run:  Set-NetConnectionProfile -Name '$($public[0].Name)' -NetworkCategory Private"
    }
}

# --- scheduled task ----------------------------------------------------------
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$taskArgs = "`"$Script`" follow --config `"$Config`" --log `"$Log`" --forever"
$action    = New-ScheduledTaskAction -Execute $VPyW -Argument $taskArgs -WorkingDirectory $AppDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Logitech mouse follows the keyboard's Easy-Switch key" | Out-Null

if (Test-Path $Log) { Clear-Content $Log }
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

Write-Host ""
Say "Installed. Recent log ($Log):"
if (Test-Path $Log) { Get-Content $Log -Tail 5 }
Write-Host ""
$ip = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1).IPv4Address.IPAddress
Write-Host "  This PC's IP  : $ip"
if ($cfgNow.secret) {
    Write-Host "  Shared secret : $($cfgNow.secret)"
    Write-Host "  -> Use this IP and secret when installing on the other computer."
}
Write-Host ""
Write-Host "  Follow the log : Get-Content `"$Log`" -Wait"
Write-Host "  Uninstall      : $SrcDir\windows\uninstall.cmd"
