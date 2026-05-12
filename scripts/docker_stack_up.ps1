# 로컬 실사용 스택: Postgres + Redis + Chroma (Docker Compose) 기동 후 헬스 확인.
# 사용 (프로젝트 루트에서):
#   .\scripts\docker_stack_up.ps1
# 선택 인자:
#   -Seed   시드까지 실행 (python 필요)

param([switch]$Seed)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

# Load .env into process env (same vars as Makefile `include .env`)
$envFile = Join-Path (Get-Location) ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
        $pair = $_ -split '=', 2
        if ($pair.Length -eq 2) {
            $k = $pair[0].Trim()
            $v = $pair[1].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
}

$pgUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "sisicallcall" }
$pgDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "sisicallcall" }

Write-Host "[docker_stack] docker compose up -d ..."
docker compose up -d

$chromaPort = $env:CHROMA_PORT
if (-not $chromaPort) { $chromaPort = "8001" }

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $null = docker compose exec -T postgres pg_isready -U $pgUser -d $pgDb 2>$null
        $redis = docker compose exec -T redis redis-cli ping 2>$null
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$chromaPort/api/v1/heartbeat" -UseBasicParsing -TimeoutSec 5
            $chromaOk = ($r.StatusCode -eq 200)
        } catch {
            $chromaOk = $false
        }
        if ($LASTEXITCODE -eq 0 -and $redis -match "PONG" -and $chromaOk) {
            Write-Host "[docker_stack] Postgres ready | Redis PONG | Chroma heartbeat OK"
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}

docker compose exec -T postgres pg_isready -U $pgUser -d $pgDb
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL not ready" }

docker compose exec -T redis redis-cli ping | Select-String -Pattern "PONG" -Quiet | Out-Null
if (-not $?) { throw "Redis not ready" }

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$chromaPort/api/v1/heartbeat" -UseBasicParsing -TimeoutSec 10 | Out-Null
} catch {
    throw "ChromaDB not ready on port $chromaPort"
}

Write-Host "[docker_stack] All three services are up."

if ($Seed) {
    Write-Host "[docker_stack] running seeds..."
    $py = $env:PYTHON
    if (-not $py) { $py = "python" }
    & $py db/seed/seed_postgres.py
    if ($LASTEXITCODE -ne 0) { throw "seed_postgres failed" }
    & $py db/seed/seed_redis.py
    if ($LASTEXITCODE -ne 0) { throw "seed_redis failed" }
    & $py db/seed/seed_chromadb.py
    if ($LASTEXITCODE -ne 0) { throw "seed_chromadb failed" }
    Write-Host "[docker_stack] Seeds done."
}
