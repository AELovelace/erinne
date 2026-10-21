<#
.SYNOPSIS
  Start (or stop) the two llama-server processes Mnemerynne needs on Windows (dev PC).

.DESCRIPTION
  Generator: OpenAI-compatible chat server on 127.0.0.1:8081
  Embedder:  OpenAI-compatible embeddings server on 127.0.0.1:8082

  Both bind to localhost only: llama-server has no authentication.
  Logs go to <LlamaDir>\logs.

.EXAMPLE
  .\scripts\start-llama.ps1
  .\scripts\start-llama.ps1 -Generator C:\llama.cpp\models\LFM2.5-2.6B-Q4_K_M.gguf
  .\scripts\start-llama.ps1 -Stop
#>
param(
    [string]$LlamaDir = "C:\llama.cpp",
    [string]$Generator = "C:\llama.cpp\models\Qwen3-8B-Q4_K_M.gguf",
    [string]$Embedder = "C:\llama.cpp\models\Qwen3-Embedding-0.6B-Q8_0.gguf",
    [int]$ChatPort = 8081,
    [int]$EmbedPort = 8082,
    # Total generator context, split evenly across $Parallel request slots.
    [int]$ContextSize = 16384,
    [int]$Parallel = 2,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$server = Join-Path $LlamaDir "bin\llama-server.exe"
$logDir = Join-Path $LlamaDir "logs"

function Stop-LlamaServers {
    foreach ($port in @($ChatPort, $EmbedPort)) {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conn) {
            Write-Host "Stopping llama-server on port $port (pid $($c.OwningProcess))"
            Stop-Process -Id $c.OwningProcess -Force -Confirm:$false
        }
    }
}

function Start-LlamaServer([string]$Name, [int]$Port, [string[]]$ServerArgs) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "$Name already listening on port $Port; leaving it running."
        return
    }
    $args = @("--host", "127.0.0.1", "--port", "$Port", "-ngl", "99") + $ServerArgs
    Start-Process -FilePath $server -ArgumentList $args -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "$Name.out.log") `
        -RedirectStandardError (Join-Path $logDir "$Name.err.log") | Out-Null
    Write-Host "Started $Name on port $Port"
}

function Wait-Healthy([string]$Name, [int]$Port, [int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 | Out-Null
            Write-Host "$Name is ready."
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "$Name did not become healthy within $TimeoutSeconds s; see $logDir\$Name.err.log"
}

if ($Stop) {
    Stop-LlamaServers
    return
}

foreach ($path in @($server, $Generator, $Embedder)) {
    if (-not (Test-Path $path)) { throw "Not found: $path" }
}
New-Item -ItemType Directory -Force $logDir | Out-Null

Start-LlamaServer "generator" $ChatPort @(
    "-m", $Generator,
    "-c", "$ContextSize", "-np", "$Parallel",
    "--jinja", "--reasoning-format", "deepseek"
)
# Embeddings need each whole input in one micro-batch, so -ub matches the
# per-slot context (8192 / 4 slots = 2048 tokens, far above a 400-char chunk).
Start-LlamaServer "embedder" $EmbedPort @(
    "-m", $Embedder,
    "--embedding", "--pooling", "last",
    "-c", "8192", "-np", "4", "-b", "2048", "-ub", "2048"
)

Wait-Healthy "generator" $ChatPort
Wait-Healthy "embedder" $EmbedPort
