param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$publisherRoot = $PSScriptRoot
$publisherPython = Join-Path $publisherRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $publisherPython)) { throw '请先安装项目依赖，详见 docs/本机发布与MCP.md' }
if (-not (Test-Path -LiteralPath (Join-Path $publisherRoot 'sau_frontend\dist\index.html'))) { throw '请先在 sau_frontend 运行 npm run build' }
$publisherListener = Get-NetTCPConnection -State Listen -LocalPort 5409 -ErrorAction SilentlyContinue
if (-not $publisherListener) {
    $publisherLogRoot = Join-Path $publisherRoot 'db'
    New-Item -ItemType Directory -Force -Path $publisherLogRoot | Out-Null
    Start-Process -FilePath $publisherPython -ArgumentList @('"' + (Join-Path $publisherRoot 'publisher_app.py') + '"') -WorkingDirectory $publisherRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $publisherLogRoot 'publisher-app.log') -RedirectStandardError (Join-Path $publisherLogRoot 'publisher-app-error.log') | Out-Null
}
Write-Output '管理页面：http://127.0.0.1:5409'
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5409' }
