# Local Windows runtime. No activation, global configuration, or database migration.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Start', 'Stop')][string]$Action = 'Check',
    [string]$PythonPath,
    [string]$ConfigPath,
    [ValidateRange(1, 65535)][int]$BackendPort = 8001,
    [ValidateRange(0, 65535)][int]$FrontendPort = 0,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$')][string]$Instance = 'local',
    [switch]$Preview,
    [ValidateRange(10, 120)][int]$TimeoutSeconds = 45,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:BackendRoot = Join-Path $script:ProjectRoot 'backend'
$script:FrontendRoot = Join-Path $script:ProjectRoot 'frontend'
$script:ViteScript = Join-Path $script:FrontendRoot 'node_modules\vite\bin\vite.js'
$script:Utf8 = [Text.UTF8Encoding]::new($false)
$OutputEncoding = $script:Utf8
try { [Console]::OutputEncoding = $script:Utf8 } catch { }

function Get-ProjectFile([string]$Value, [string]$Default) {
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = $Default }
    if (-not [IO.Path]::IsPathRooted($Value)) { $Value = Join-Path $script:ProjectRoot $Value }
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) { throw "文件不存在：$Value" }
    return (Get-Item -LiteralPath $Value).FullName
}

function Get-PythonExecutable([string]$Value) {
    $default = Join-Path $env:USERPROFILE 'miniconda3\envs\myenv12\python.exe'
    return Get-ProjectFile $Value $default
}

function Get-NodeExecutable {
    $command = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) { throw '未找到 Node.js；请先安装项目要求的 Node.js，脚本不会修改全局 PATH。' }
    return $command.Source
}

function Get-TextHash([string]$Value) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($script:Utf8.GetBytes($Value)))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Start-HiddenService([string]$Python, [string]$Role, [string[]]$Command, [string]$LogDir) {
    $helper = Join-Path $script:ProjectRoot 'scripts\start_hidden.py'
    $reply = & $Python -X utf8 -B $helper --role $Role --log-dir $LogDir -- @Command
    if ($LASTEXITCODE -ne 0) { throw "无法启动隐藏的 $Role 进程；未继承调用终端管道。" }
    $childId = [int](($reply -join "`n" | ConvertFrom-Json).pid)
    return Get-ProcessRecord ([Diagnostics.Process]::GetProcessById($childId)) $Role
}

function Get-ProcessRecord([Diagnostics.Process]$Process, [string]$Role) {
    $live = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction Stop
    if ($null -eq $live -or [string]::IsNullOrWhiteSpace($live.CommandLine)) { throw "无法核验 $Role 进程身份。" }
    return [pscustomobject]@{
        role = $Role
        pid = [int]$Process.Id
        createdUtc = $live.CreationDate.ToUniversalTime().ToString('o')
        executable = $live.ExecutablePath
        commandHash = Get-TextHash $live.CommandLine
    }
}

function Test-ProcessRecord($Record, [switch]$AllowReusedPid) {
    if ($Record.role -notin @('backend', 'frontend') -or [int]$Record.pid -le 0) { throw '运行记录的进程字段无效；不停止任何未知进程。' }
    $live = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Record.pid)" -ErrorAction Stop
    if ($null -eq $live) { return $false }
    $marker = if ($Record.role -eq 'backend') { $script:BackendRoot } else { $script:FrontendRoot }
    $entry = if ($Record.role -eq 'backend') { 'app.main:app' } else { $script:ViteScript }
    # PowerShell 7 JSON deserialization may convert ISO strings to DateTime.
    # Compare UTC instants, not a formatted string against a culture-formatted date.
    try {
        if ($null -eq $Record.createdUtc -or $null -eq $live.CreationDate) { throw 'missing creation time' }
        $recordCreated = ([DateTime]$Record.createdUtc).ToUniversalTime()
        $liveCreated = ([DateTime]$live.CreationDate).ToUniversalTime()
        if ($recordCreated.Year -lt 2000 -or $liveCreated.Year -lt 2000) { throw 'invalid creation time' }
    }
    catch { throw "PID $($Record.pid) 的身份已改变或记录时间无效；拒绝停止。" }
    # A strictly newer birth time proves the recorded process has exited. This
    # exception is Start-only: never inspect/stop the unrelated recycled PID.
    if ($AllowReusedPid -and $liveCreated -gt $recordCreated) {
        Write-Verbose "旧 $($Record.role) PID $($Record.pid) 已复用；仅按已退出处理，不操作当前进程。"
        return $false
    }
    if ($liveCreated -ne $recordCreated -or
        $live.ExecutablePath -ne $Record.executable -or
        [string]::IsNullOrWhiteSpace($live.CommandLine) -or
        (Get-TextHash $live.CommandLine) -ne $Record.commandHash -or
        $live.CommandLine.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $live.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "PID $($Record.pid) 的身份已改变；拒绝按旧记录停止或覆盖。请保留记录并人工核对。"
    }
    return $true
}

function Get-RuntimeState([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { $state = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "运行记录无法解析，已保留且不会覆盖：$Path" }
    if ($state.schema -ne 1 -or $state.root -ne $script:ProjectRoot -or $state.instance -ne $Instance) {
        throw "运行记录归属不匹配，拒绝操作：$Path"
    }
    return $state
}

function Write-RuntimeState([string]$Path, $State) {
    # Both paths are fixed files within this instance directory, never recursive targets.
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, ($State | ConvertTo-Json -Depth 6), $script:Utf8)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Save-PreviousRuntimeState([string]$Path, $ExpectedState) {
    # Preserve the original bytes before replacing stale metadata. Do not archive
    # a record another launcher changed after our first identity checks.
    $bytes = [IO.File]::ReadAllBytes($Path)
    try { $current = $script:Utf8.GetString($bytes).TrimStart([char]0xFEFF) | ConvertFrom-Json }
    catch { throw '运行记录已变化或无法解析；拒绝归档和覆盖。' }
    $expectedJson = $ExpectedState | ConvertTo-Json -Depth 8 -Compress
    $currentJson = $current | ConvertTo-Json -Depth 8 -Compress
    if ($currentJson -cne $expectedJson) { throw '运行记录已变化，与已核验状态不一致；拒绝归档和覆盖。' }
    $history = Join-Path (Split-Path -Parent $Path) 'history'
    New-Item -ItemType Directory -Path $history -Force | Out-Null
    $archive = Join-Path $history ('runtime-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N') + '.json')
    $stream = [IO.File]::Open($archive, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush() } finally { $stream.Dispose() }
    return $archive
}
function Assert-PortAvailable([int]$Port) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    $listener.Server.ExclusiveAddressUse = $true
    try { $listener.Start() }
    catch { throw "127.0.0.1:$Port 已占用或无法绑定；不会结束其他进程或静默换端口。" }
    finally { $listener.Stop() }
}

function Wait-LocalHttp([string]$Url, $Record, [bool]$Health) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    try {
        while ([DateTime]::UtcNow -lt $deadline) {
            if (-not (Test-ProcessRecord $Record)) { throw "$($Record.role) 进程在就绪前退出；请查看本次日志。" }
            $ready = $false
            try {
                $body = $client.GetStringAsync($Url).GetAwaiter().GetResult()
                $ready = if ($Health) { ($body | ConvertFrom-Json).status -eq 'ok' } else { $body -match '<html' }
            } catch { $ready = $false }
            if ($ready) { return }
            Start-Sleep -Milliseconds 350
        }
        throw "等待 $Url 就绪超时（${TimeoutSeconds}s），请查看本次日志。"
    } finally { $client.Dispose(); $handler.Dispose() }
}

function Assert-ListenerOwned([int]$Port, $Record) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    if (-not ($listeners | Where-Object { $_.OwningProcess -eq [int]$Record.pid -and $_.LocalAddress -eq '127.0.0.1' })) {
        throw "端口 $Port 未由已核验的本项目进程监听；拒绝报告启动成功。"
    }
}

function Invoke-LocalCheck([string]$Python, [string]$Config, [string]$Node) {
    $diagnostic = Join-Path $script:BackendRoot 'scripts\local_diagnostics.py'
    $lines = & $Python -X utf8 -B $diagnostic --config $Config --json
    $code = $LASTEXITCODE
    try { $result = ($lines -join "`n") | ConvertFrom-Json }
    catch { throw '后端诊断未返回有效 JSON；请直接运行 backend/scripts/local_diagnostics.py 查看错误。' }
    if (-not (Test-Path -LiteralPath $script:ViteScript -PathType Leaf)) {
        $result.errors = @($result.errors) + '前端 Vite 依赖缺失；请在 frontend 执行 npm.cmd ci。'
        $result.ok = $false
    }
    $nodeVersion = (& $Node --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 18) {
        $result.errors = @($result.errors) + 'Node.js 不可用或低于 Vite 5 要求的 v18。'
        $result.ok = $false
    }
    $result | Add-Member -NotePropertyName node -NotePropertyValue @{ executable = $Node; version = $nodeVersion }
    if ($code -ne 0) { $result.ok = $false }
    return $result
}

function Show-LocalCheck($Report) {
    if ($Json) { $Report | ConvertTo-Json -Depth 8; return }
    Write-Host "Python：$($Report.python.executable)"
    Write-Host "Node：$($Report.node.version)"
    Write-Host "配置：$($Report.config.path)"
    Write-Host "数据库：$($Report.database.path) [$($Report.database.state)]"
    foreach ($notice in @($Report.warnings)) { Write-Warning $notice }
    foreach ($problem in @($Report.errors)) { Write-Host "FAIL：$problem" -ForegroundColor Red }
    if ($Report.ok) { Write-Host 'PASS：本机必需依赖及数据库只读预检通过；未迁移或初始化业务库。' -ForegroundColor Green }
}

function Stop-RecordedProcesses($Records) {
    # Verify the entire set before the first stop, then recheck immediately before each stop.
    foreach ($record in @($Records)) { $null = Test-ProcessRecord $record }
    foreach ($record in @($Records | Sort-Object -Property role -Descending)) {
        if (Test-ProcessRecord $record) {
            Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
            Wait-Process -Id ([int]$record.pid) -Timeout 12 -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-LocalRuntime {
    $runtimeDir = Join-Path $script:ProjectRoot "storage\local-runtime\$Instance"
    $statePath = Join-Path $runtimeDir 'runtime.json'
    $previous = Get-RuntimeState $statePath
    if ($Action -eq 'Stop') {
        if ($null -eq $previous) { Write-Host '没有本脚本的运行记录；未停止任何进程。'; return }
        if ($previous.status -eq 'stopped') { Write-Host '此实例已停止；不按历史 PID 操作。'; return }
        Stop-RecordedProcesses $previous.processes
        $previous.status = 'stopped'
        $previous.stoppedUtc = [DateTime]::UtcNow.ToString('o')
        Write-RuntimeState $statePath $previous
        Write-Host "已停止本项目实例 $Instance。日志及数据完整保留。"
        return
    }
    $python = Get-PythonExecutable $PythonPath
    $config = Get-ProjectFile $ConfigPath (Join-Path $script:BackendRoot 'config.yaml')
    $node = Get-NodeExecutable
    $report = Invoke-LocalCheck $python $config $node
    Show-LocalCheck $report
    if (-not $report.ok) { throw '本机检查未通过；没有启动服务。' }
    if ($Action -eq 'Check') { return }
    if ($Json) { throw '-Json 仅适用于 Check。' }
    if ($null -ne $previous -and $previous.status -ne 'stopped') {
        foreach ($record in @($previous.processes)) {
            if (Test-ProcessRecord $record -AllowReusedPid) { throw "实例 $Instance 已有进程运行，请先执行停止本机.cmd -Instance $Instance。" }
        }
    }
    if ($FrontendPort -eq 0) { $FrontendPort = if ($Preview) { 4173 } else { 5173 } }
    if ($BackendPort -eq $FrontendPort) { throw '前后端不能使用同一端口。' }
    Assert-PortAvailable $BackendPort
    Assert-PortAvailable $FrontendPort
    if ($Preview -and -not (Test-Path -LiteralPath (Join-Path $script:FrontendRoot 'dist\index.html') -PathType Leaf)) {
        throw '尚无生产构建；请先在 frontend 执行 npm.cmd run build。'
    }
    if ($null -ne $previous -and $previous.status -ne 'stopped') {
        # Recheck the entire old set after preflight, before the first archive or
        # spawn. One surviving process still blocks the restart; no process kill.
        foreach ($record in @($previous.processes)) {
            if (Test-ProcessRecord $record -AllowReusedPid) { throw "实例 $Instance 已有进程运行，请先执行停止本机.cmd -Instance $Instance。" }
        }
        $archive = Save-PreviousRuntimeState $statePath $previous
        Write-Host "旧实例进程已退出，运行记录已原样归档：$archive"
        Write-Host '若旧 PID 已被其他进程复用，不会停止或修改该进程。'
    }
    $logDir = Join-Path $runtimeDir ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $savedConfig = [Environment]::GetEnvironmentVariable('APP_CONFIG', 'Process')
    $savedProxy = [Environment]::GetEnvironmentVariable('VITE_API_PROXY', 'Process')
    $records = [Collections.Generic.List[object]]::new()
    $state = [pscustomobject]@{
        schema = 1; root = $script:ProjectRoot; instance = $Instance; status = 'starting'
        config = $config; backendPort = $BackendPort; frontendPort = $FrontendPort
        preview = [bool]$Preview; logDir = $logDir; startedUtc = [DateTime]::UtcNow.ToString('o')
        stoppedUtc = $null; processes = @()
    }
    try {
        $env:APP_CONFIG = $config
        $env:VITE_API_PROXY = "http://127.0.0.1:$BackendPort"
        $backendArgs = @($python, '-X', 'utf8', '-B', '-m', 'uvicorn', 'app.main:app', '--app-dir', $script:BackendRoot, '--host', '127.0.0.1', '--port', "$BackendPort")
        $records.Add((Start-HiddenService $python 'backend' $backendArgs $logDir))
        $state.processes = $records.ToArray()
        Write-RuntimeState $statePath $state
        Wait-LocalHttp "http://127.0.0.1:$BackendPort/api/health" $records[0] $true
        Assert-ListenerOwned $BackendPort $records[0]
        $frontendArgs = @($node, $script:ViteScript)
        if ($Preview) { $frontendArgs += 'preview' }
        $frontendArgs += @($script:FrontendRoot, '--config', (Join-Path $script:FrontendRoot 'vite.config.js'), '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort')
        $records.Add((Start-HiddenService $python 'frontend' $frontendArgs $logDir))
        $state.processes = $records.ToArray()
        Write-RuntimeState $statePath $state
        Wait-LocalHttp "http://127.0.0.1:$FrontendPort/" $records[1] $false
        Assert-ListenerOwned $FrontendPort $records[1]
        # Also exercise the frontend's API proxy, not only the backend directly.
        Wait-LocalHttp "http://127.0.0.1:$FrontendPort/api/health" $records[1] $true
        $state.status = 'running'
        Write-RuntimeState $statePath $state
        Write-Host "已启动： http://127.0.0.1:$FrontendPort"
        Write-Host "API：    http://127.0.0.1:$BackendPort/api/health"
        Write-Host "日志：   $logDir"
        Write-Host "停止：   & '$script:ProjectRoot\停止本机.cmd' -Instance $Instance"
    } catch {
        $failure = $_
        $state.status = 'failed'
        $state.processes = $records.ToArray()
        try { Stop-RecordedProcesses $records.ToArray(); $state.stoppedUtc = [DateTime]::UtcNow.ToString('o') }
        catch { Write-Warning "部分进程身份无法安全核验，已保留记录供恢复：$($_.Exception.Message)" }
        Write-RuntimeState $statePath $state
        throw "启动失败：$($failure.Exception.Message)。日志：$logDir"
    } finally {
        [Environment]::SetEnvironmentVariable('APP_CONFIG', $savedConfig, 'Process')
        [Environment]::SetEnvironmentVariable('VITE_API_PROXY', $savedProxy, 'Process')
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-LocalRuntime; exit 0 }
    catch { Write-Error -Message $_.Exception.Message -ErrorAction Continue; exit 1 }
}