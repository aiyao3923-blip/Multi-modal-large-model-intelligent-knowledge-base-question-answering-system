# Standalone PowerShell regressions; mocks never stop a real process.
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'local-runtime.ps1')
$script:Passed = 0
function Assert-Local([bool]$Condition, [string]$Name) {
    if (-not $Condition) { throw "FAIL: $Name" }
    $script:Passed++
    Write-Output "PASS: $Name"
}
function Assert-Rejected([scriptblock]$Call, [string]$Pattern, [string]$Name) {
    $rejected = $false
    try { & $Call | Out-Null }
    catch { if ($_.Exception.Message -notmatch $Pattern) { throw }; $rejected = $true }
    Assert-Local $rejected $Name
}
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $env:TEMP
try {
    Assert-Local ((Get-ProjectFile 'backend\requirements.txt' '') -eq (Join-Path $expectedRoot 'backend\requirements.txt')) 'relative paths resolve from project, not caller cwd'
    Assert-Local ((Get-PythonExecutable '') -eq (Join-Path $env:USERPROFILE 'miniconda3\envs\myenv12\python.exe')) 'registered Miniconda myenv12 is the default'
} finally { Pop-Location }
Assert-Rejected { Get-ProjectFile 'nonexistent-file-for-runtime-check' '' } '文件不存在' 'missing files fail without creating them'
Assert-Local (-not (Test-Path -LiteralPath (Join-Path $expectedRoot 'nonexistent-file-for-runtime-check'))) 'read-only resolution leaves no file'
foreach ($relative in @('scripts\local-runtime.ps1', '启动本机.ps1', '停止本机.ps1', '检查本机.ps1')) {
    $tokens = $null; $problems = $null
    $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $expectedRoot $relative), [ref]$tokens, [ref]$problems)
    Assert-Local (@($problems).Count -eq 0) "PowerShell parses: $relative"
}
$script:MockLive = [pscustomobject]@{
    ProcessId = 198765
    CreationDate = [DateTime]::Parse('2026-09-15T01:02:03Z').ToUniversalTime()
    ExecutablePath = Get-PythonExecutable ''
    CommandLine = 'python.exe -m uvicorn app.main:app --app-dir "' + $script:BackendRoot + '" --host 127.0.0.1 --port 8001'
}
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction); return $script:MockLive }
$record = [pscustomobject]@{
    role = 'backend'; pid = 198765
    createdUtc = $script:MockLive.CreationDate.ToUniversalTime().ToString('o')
    executable = $script:MockLive.ExecutablePath
    commandHash = Get-TextHash $script:MockLive.CommandLine
}
Assert-Local (Test-ProcessRecord $record) 'matching process identity accepted'
$roundtrip = $record | ConvertTo-Json | ConvertFrom-Json
Assert-Local (Test-ProcessRecord $roundtrip) 'JSON DateTime roundtrip retains the exact process identity'
$script:MockLive.CreationDate = $script:MockLive.CreationDate.AddSeconds(1)
Assert-Rejected { Test-ProcessRecord $record } '身份已改变' 'PID reuse rejected by creation time'
$script:MockLive.CreationDate = $script:MockLive.CreationDate.AddSeconds(-1)
$originalCommand = $script:MockLive.CommandLine
$script:MockLive.CommandLine = $originalCommand + ' --unexpected'
Assert-Rejected { Test-ProcessRecord $record } '身份已改变' 'command mismatch rejected'
$script:MockLive.CommandLine = 'python.exe -m uvicorn app.main:app --app-dir C:\unrelated-project'
$record.commandHash = Get-TextHash $script:MockLive.CommandLine
Assert-Rejected { Test-ProcessRecord $record } '身份已改变' 'matching digest cannot bypass project boundary'
$script:MockLive.CommandLine = $originalCommand
$record.commandHash = Get-TextHash $originalCommand
$originalExe = $script:MockLive.ExecutablePath
$script:MockLive.ExecutablePath = 'C:\not-the-registered-python.exe'
Assert-Rejected { Test-ProcessRecord $record } '身份已改变' 'executable mismatch rejected'
$script:MockLive.ExecutablePath = $originalExe
$bad = [pscustomobject]@{ role = 'backend'; pid = 198765; createdUtc = 'wrong'; executable = $record.executable; commandHash = $record.commandHash }
$script:StopCalls = 0
function Stop-Process { param($Id, $ErrorAction); $script:StopCalls++ }
function Wait-Process { param($Id, $Timeout, $ErrorAction) }
Assert-Rejected { Stop-RecordedProcesses @($record, $bad) } '身份已改变' 'all identities checked before stopping first process'
Assert-Local ($script:StopCalls -eq 0) 'identity failure causes zero stop calls'
$script:MockLive = $null
Assert-Local (-not (Test-ProcessRecord $record)) 'exited process is not a candidate for stopping'
Stop-RecordedProcesses @($record)
Assert-Local ($script:StopCalls -eq 0) 'already exited process is not stopped'
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
try { Assert-Rejected { Assert-PortAvailable $listener.LocalEndpoint.Port } '已占用|无法绑定' 'occupied loopback port rejected without killing owner' }
finally { $listener.Stop() }
# A reboot may recycle a stale PID for a protected Windows process. Only Start
# may classify a strictly newer creation time as an exited historical process.
$script:MockLive = [pscustomobject]@{
    ProcessId = $record.pid; CreationDate = ([DateTime]$record.createdUtc).AddDays(1)
    ExecutablePath = $null; CommandLine = $null
}
Assert-Local (-not (Test-ProcessRecord $record -AllowReusedPid)) 'Start recognizes proven PID reuse even when protected process command is unreadable'
Assert-Rejected { Stop-RecordedProcesses @($record) } '身份已改变' 'Stop still refuses reused PID rather than killing unrelated process'
Assert-Local ($script:StopCalls -eq 0) 'reused PID never reaches Stop-Process'
$script:MockLive.CreationDate = ([DateTime]$record.createdUtc)
Assert-Rejected { Test-ProcessRecord $record -AllowReusedPid } '身份已改变' 'Start rejects same-birth identity with inaccessible executable/command'
$script:MockLive.CreationDate = ([DateTime]$record.createdUtc).AddSeconds(-1)
Assert-Rejected { Test-ProcessRecord $record -AllowReusedPid } '身份已改变' 'older live timestamp is not silently treated as safe reuse'
$script:MockLive.CreationDate = $null
Assert-Rejected { Test-ProcessRecord $record -AllowReusedPid } '时间|身份' 'missing live creation time fails closed'
$script:MockLive.CreationDate = ([DateTime]$record.createdUtc).AddDays(1)
Assert-Rejected { Test-ProcessRecord $bad -AllowReusedPid } '时间|身份' 'invalid stored timestamp cannot authorize recovery'
$script:MockLive.CreationDate = ([DateTime]$record.createdUtc)
$script:MockLive.ExecutablePath = $record.executable
$script:MockLive.CommandLine = $originalCommand
Assert-Local (Test-ProcessRecord $record -AllowReusedPid) 'Start still detects an exactly matching live process'

$testDir = Join-Path $expectedRoot ('docs\runtime-recovery-20261001\archive-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
$testStatePath = Join-Path $testDir 'runtime.json'
$originalBytes = [Text.Encoding]::UTF8.GetBytes("{`r`n  `"schema`": 1, `"status`": `"running`", `"note`": `"原记录保全`"`r`n}")
[IO.File]::WriteAllBytes($testStatePath, $originalBytes)
$testState = Get-Content -LiteralPath $testStatePath -Raw | ConvertFrom-Json
$archive = Save-PreviousRuntimeState $testStatePath $testState
Assert-Local ((Get-FileHash -LiteralPath $archive).Hash -eq (Get-FileHash -LiteralPath $testStatePath).Hash) 'archive preserves exact source bytes including line endings'
Assert-Local ($archive.StartsWith($testDir + [IO.Path]::DirectorySeparatorChar)) 'archive stays inside the instance directory'
$again = Save-PreviousRuntimeState $testStatePath $testState
Assert-Local ($again -ne $archive -and (Test-Path -LiteralPath $archive)) 'repeat archive never overwrites the prior evidence'
$changed = $testState | ConvertTo-Json | ConvertFrom-Json
$changed.status = 'changed-by-other-task'
Assert-Rejected { Save-PreviousRuntimeState $testStatePath $changed } '变化|一致' 'record drift refuses archive and replacement'
Assert-Local ((Get-ChildItem -LiteralPath (Split-Path $archive -Parent) -File).Count -eq 2) 'drift rejection creates no extra archive'
Assert-Local ($script:StopCalls -eq 0) 'classification and archival never stop a process'
Write-Output ("LOCAL_RUNTIME_TESTS=" + (@{ success = $true; passed = $script:Passed; scope = 'PowerShell unit checks with mocked process stop; actual startup verified separately' } | ConvertTo-Json -Compress))
