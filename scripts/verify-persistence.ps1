# Recreates only this project's backend and DB containers; the named volume remains.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $before = docker compose exec -T backend python -m app.demo.verify_foundation
    if ($LASTEXITCODE -ne 0) { throw 'Could not read foundation record before restart.' }
    $graphBefore = docker compose exec -T backend python -m app.demo.verify_graph seed
    if ($LASTEXITCODE -ne 0) { throw 'Could not seed synthetic graph verification records.' }
    docker compose up -d --force-recreate --wait --wait-timeout 120 falkordb backend
    if ($LASTEXITCODE -ne 0) { throw 'Container recreation failed.' }
    $after = docker compose exec -T backend python -m app.demo.verify_foundation
    if ($LASTEXITCODE -ne 0) { throw 'Could not read foundation record after restart.' }
    if (($before | ConvertFrom-Json).created_at -ne ($after | ConvertFrom-Json).created_at) {
        throw 'Foundation timestamp changed: persistence recovery failed.'
    }
    $graphAfter = docker compose exec -T backend python -m app.demo.verify_graph read
    if ($LASTEXITCODE -ne 0) { throw 'Could not read graph records after restart.' }
    if (($graphBefore -join "`n") -ne ($graphAfter -join "`n")) {
        throw 'Graph nodes or relationships changed after container recreation.'
    }
    Write-Host "Persistent graph record survived container recreation: $after"
    Write-Host 'Synthetic nodes, event provenance and temporal relationship also survived.'
} finally { Pop-Location }
