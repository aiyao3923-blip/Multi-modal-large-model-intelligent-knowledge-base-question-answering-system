# 只读预检；不激活 Conda、不初始化/迁移数据库、不启动服务。
[CmdletBinding()]
param([string]$PythonPath, [string]$ConfigPath, [switch]$Json)
& (Join-Path $PSScriptRoot 'scripts\local-runtime.ps1') -Action Check @PSBoundParameters
exit $LASTEXITCODE
