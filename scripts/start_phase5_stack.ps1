param(
    [int]$MonitorSeconds = 180,
    [int]$StartupWaitSeconds = 15,
    [switch]$NoMonitor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

function Test-ManagedProcess {
    param(
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        return $null
    }

    $rawPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $rawPid) {
        return $null
    }

    try {
        $pidValue = [int]$rawPid
        $proc = Get-Process -Id $pidValue -ErrorAction Stop
        return $proc
    }
    catch {
        return $null
    }
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$PidFile,
        [string]$Command
    )

    $existing = Test-ManagedProcess -PidFile $PidFile
    if ($null -ne $existing) {
        Write-Host "[$Name] already running with PID $($existing.Id)" -ForegroundColor Yellow
        return $existing
    }

    $psExe = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $proc = Start-Process -FilePath $psExe `
        -ArgumentList "-NoLogo", "-NoProfile", "-Command", $Command `
        -PassThru

    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii
    Write-Host "[$Name] started with PID $($proc.Id)" -ForegroundColor Green
    return $proc
}

function Invoke-LoggedProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$StdOutPath,
        [string]$StdErrPath
    )

    if (Test-Path $StdOutPath) {
        Remove-Item -Force $StdOutPath
    }
    if (Test-Path $StdErrPath) {
        Remove-Item -Force $StdErrPath
    }

    $proc = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdOutPath `
        -RedirectStandardError $StdErrPath `
        -NoNewWindow `
        -PassThru `
        -Wait

    return $proc.ExitCode
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeDir = Join-Path $repoRoot ".local\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$listenerPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$freqtradePython = Join-Path $repoRoot ".venv-freqtrade\Scripts\python.exe"
$freqtradeExe = Join-Path $repoRoot ".venv-freqtrade\Scripts\freqtrade.exe"
$mainPy = Join-Path $repoRoot "main.py"
$freqtradeDir = Join-Path $repoRoot "freqtrade"
$configPath = Join-Path $freqtradeDir "user_data\config.json"
$dynamicPairlistPath = Join-Path $freqtradeDir "user_data\dynamic_pairs.json"
$monitorScript = Join-Path $PSScriptRoot "monitor_phase5_flow.py"

foreach ($path in @($listenerPython, $freqtradePython, $freqtradeExe, $mainPy, $configPath, $monitorScript)) {
    if (-not (Test-Path $path)) {
        throw "Missing required file: $path"
    }
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$dbPath = [string]($config.bot_db_path)
if ([string]::IsNullOrWhiteSpace($dbPath)) {
    $dbPath = [string]($config.te_signal_bot_db_path)
}
if ([string]::IsNullOrWhiteSpace($dbPath)) {
    $dbPath = Join-Path $repoRoot "db\tele_signal_bot.sqlite3"
}
$dbPath = $dbPath.Replace("/", "\")

$apiEnabled = $false
$freqUiUrl = $null
if ($null -ne $config.api_server -and $config.api_server.enabled -eq $true) {
    $apiEnabled = $true
    $freqUiUrl = "http://{0}:{1}" -f $config.api_server.listen_ip_address, $config.api_server.listen_port
}

$remotePairList = $null
if ($null -ne $config.pairlists) {
    $remotePairList = @($config.pairlists | Where-Object { $_.method -eq "RemotePairList" } | Select-Object -First 1)
}
if ($remotePairList.Count -gt 0 -and $null -eq $remotePairList[0].number_assets) {
    throw "RemotePairList requires number_assets in $configPath"
}

Write-Host "[check] repo root: $repoRoot"
Write-Host "[check] DB path: $dbPath"
Write-Host "[check] config: $configPath"

$env:PYTHONPATH = $repoRoot
& $freqtradePython -c "import src; print('ok')" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "freqtrade venv cannot import src. Check PYTHONPATH."
}

$showConfigOutLog = Join-Path $runtimeDir "freqtrade_show_config.stdout.log"
$showConfigErrLog = Join-Path $runtimeDir "freqtrade_show_config.stderr.log"
$showConfigExit = Invoke-LoggedProcess `
    -FilePath $freqtradeExe `
    -ArgumentList @("show-config", "-c", $configPath) `
    -WorkingDirectory $freqtradeDir `
    -StdOutPath $showConfigOutLog `
    -StdErrPath $showConfigErrLog
if ($showConfigExit -ne 0) {
    throw "freqtrade show-config failed. See $showConfigOutLog and $showConfigErrLog"
}

$listenerLog = Join-Path $runtimeDir "listener_runtime.log"
$freqtradeLog = Join-Path $runtimeDir "freqtrade_runtime.log"
$listenerPidFile = Join-Path $runtimeDir "listener.pid"
$freqtradePidFile = Join-Path $runtimeDir "freqtrade.pid"

$listenerCommand = @"
`$env:DB_PATH = '$dbPath'
Set-Location '$repoRoot'
& '$listenerPython' '$mainPy' *>> '$listenerLog'
"@

$freqtradeCommand = @"
`$env:PYTHONPATH = '$repoRoot'
`$env:TELESIGNALBOT_DB_PATH = '$dbPath'
Set-Location '$freqtradeDir'
& '$freqtradeExe' trade -c '$configPath' --strategy SignalBridgeStrategy --dry-run *>> '$freqtradeLog'
"@

$listenerProc = Start-ManagedProcess -Name "listener" -PidFile $listenerPidFile -Command $listenerCommand
$freqtradeProc = Start-ManagedProcess -Name "freqtrade" -PidFile $freqtradePidFile -Command $freqtradeCommand

Write-Host "[check] waiting $StartupWaitSeconds seconds for startup..." -ForegroundColor Cyan
Start-Sleep -Seconds $StartupWaitSeconds

$listenerAlive = Test-ManagedProcess -PidFile $listenerPidFile
$freqtradeAlive = Test-ManagedProcess -PidFile $freqtradePidFile

if ($null -eq $listenerAlive) {
    throw "Listener is not running. See $listenerLog"
}
if ($null -eq $freqtradeAlive) {
    throw "Freqtrade is not running. See $freqtradeLog"
}

Write-Host "[check] listener alive: PID $($listenerAlive.Id)" -ForegroundColor Green
Write-Host "[check] freqtrade alive: PID $($freqtradeAlive.Id)" -ForegroundColor Green

if ($apiEnabled -and $freqUiUrl) {
    try {
        $response = Invoke-WebRequest -Uri $freqUiUrl -UseBasicParsing -TimeoutSec 5
        Write-Host "[check] FreqUI reachable: $freqUiUrl ($($response.StatusCode))" -ForegroundColor Green
    }
    catch {
        Write-Host "[check] FreqUI not reachable yet: $freqUiUrl" -ForegroundColor Yellow
    }
}

Write-Host "[check] listener log: $listenerLog"
Write-Host "[check] freqtrade log: $freqtradeLog"
Write-Host "[check] dynamic pairlist: $dynamicPairlistPath"

if (-not $NoMonitor) {
    Write-Host "[monitor] waiting up to $MonitorSeconds seconds for a real Telegram message..." -ForegroundColor Cyan
    & $listenerPython $monitorScript --db-path $dbPath --dynamic-pairlist-path $dynamicPairlistPath --timeout $MonitorSeconds
    $monitorExit = $LASTEXITCODE
    switch ($monitorExit) {
        0 { Write-Host "[result] PASS: data reached bridge/freqtrade." -ForegroundColor Green }
        2 { Write-Host "[result] READY: stack is up, but no new Telegram message arrived in time." -ForegroundColor Yellow }
        3 { Write-Host "[result] PARTIAL: listener/parser saw activity, but it did not reach signals/trades in time." -ForegroundColor Yellow }
        Default { Write-Host "[result] ERROR: monitor failed. Check logs." -ForegroundColor Red }
    }
}
else {
    Write-Host "[monitor] skipped."
}

Write-Host ""
Write-Host "Run this command from C:\TeleSignalBot:" -ForegroundColor Cyan
Write-Host ".\scripts\start_phase5_stack.ps1 -MonitorSeconds 300"
