# 停止之前会核验 PID、创建时间、可执行文件、命令摘要及项目路径。
[CmdletBinding()]
param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$')][string]$Instance = 'local')
& (Join-Path $PSScriptRoot 'scripts\local-runtime.ps1') -Action Stop @PSBoundParameters
exit $LASTEXITCODE
