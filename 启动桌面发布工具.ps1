param([ValidateRange(1, 65535)][int]$Port = 5409, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$publisherRoot = $PSScriptRoot
$publisherPython = Join-Path $publisherRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $publisherPython)) { throw '请先安装项目依赖，详见 docs/本机发布与MCP.md' }
if (-not (Test-Path -LiteralPath (Join-Path $publisherRoot 'sau_frontend\dist\index.html'))) { throw '请先在 sau_frontend 运行 npm run build' }
$publisherEntry = Join-Path $publisherRoot 'publisher_desktop.py'
if (-not (Test-Path -LiteralPath $publisherEntry)) { throw '桌面入口 publisher_desktop.py 不存在' }
& $publisherPython -X utf8 -c 'import webview, pystray'
if ($LASTEXITCODE -ne 0) { throw '桌面依赖缺失，请在项目目录运行 uv sync --extra web --extra mcp --extra desktop' }
# The web service and desktop window share the ledger; keep an existing service running.
$publisherCandidate = $Port
while (Get-NetTCPConnection -State Listen -LocalPort $publisherCandidate -ErrorAction SilentlyContinue) {
    $publisherCandidate++
    if ($publisherCandidate -gt [Math]::Min($Port + 20, 65535)) { throw '附近端口均被占用，请用 -Port 指定空闲端口' }
}
if ($CheckOnly) {
    Write-Output "启动检查通过；可用本机端口：$publisherCandidate"
    exit 0
}
$publisherLogRoot = Join-Path $publisherRoot 'db'
New-Item -ItemType Directory -Force -Path $publisherLogRoot | Out-Null
# Separate logs prevent a second click from truncating the active window's log.
$publisherRunName = 'desktop-launch-' + [Guid]::NewGuid().ToString('N')
$publisherErrorLog = Join-Path $publisherLogRoot ($publisherRunName + '.error.log')
$publisherStartedAt = [DateTime]::UtcNow
$publisherProcess = Start-Process -FilePath $publisherPython -ArgumentList @('-X', 'utf8', ('"' + $publisherEntry + '"'), '--port', $publisherCandidate) -WorkingDirectory $publisherRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $publisherLogRoot ($publisherRunName + '.log')) -RedirectStandardError $publisherErrorLog -PassThru
$null = $publisherProcess.Handle
Add-Type -AssemblyName System.Net.Http
$publisherHandler = New-Object System.Net.Http.HttpClientHandler
$publisherHandler.UseProxy = $false
$publisherClient = New-Object System.Net.Http.HttpClient($publisherHandler)
$publisherClient.Timeout = [TimeSpan]::FromSeconds(2)
try {
    for ($publisherAttempt = 0; $publisherAttempt -lt 40; $publisherAttempt++) {
        $publisherProcess.Refresh()
        if ($publisherProcess.HasExited) {
            $publisherProcess.WaitForExit()
            if ($publisherProcess.ExitCode -eq 0) { Write-Output '已唤醒桌面工作台'; exit 0 }
            throw "桌面工具启动失败，请查看日志：$publisherErrorLog"
        }
        $publisherSessionPath = Join-Path $publisherLogRoot 'desktop-session.json'
        if ((Test-Path -LiteralPath $publisherSessionPath) -and (Get-Item -LiteralPath $publisherSessionPath).LastWriteTimeUtc -ge $publisherStartedAt) {
            try {
                $publisherSession = Get-Content -LiteralPath $publisherSessionPath -Raw | ConvertFrom-Json
                $publisherResponse = $publisherClient.GetAsync("http://127.0.0.1:$($publisherSession.port)/").GetAwaiter().GetResult()
                try {
                    if ($publisherResponse.IsSuccessStatusCode) { Write-Output '桌面工作台已启动'; exit 0 }
                } finally { $publisherResponse.Dispose() }
            } catch [System.Net.Http.HttpRequestException] { } catch [System.Threading.Tasks.TaskCanceledException] { }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "桌面工具尚未就绪，请查看日志：$publisherErrorLog"
} finally {
    $publisherClient.Dispose()
    $publisherHandler.Dispose()
}
