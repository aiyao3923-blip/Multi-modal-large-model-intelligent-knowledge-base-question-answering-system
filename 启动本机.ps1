# 在项目根或任意目录运行；后台窗口隐藏，停止时只匹配本项目进程。
[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$ConfigPath,
    [ValidateRange(1, 65535)][int]$BackendPort = 8001,
    [ValidateRange(0, 65535)][int]$FrontendPort = 0,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$')][string]$Instance = 'local',
    [switch]$Preview,
    [ValidateRange(10, 120)][int]$TimeoutSeconds = 45
)
& (Join-Path $PSScriptRoot 'scripts\local-runtime.ps1') -Action Start @PSBoundParameters
exit $LASTEXITCODE
