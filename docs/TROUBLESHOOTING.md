# Backend demonstration troubleshooting

| Symptom | Check / resolution |
| --- | --- |
| `dependency_unavailable`, readiness timeout | Verify the configured authenticated FalkorDB host/port and server graph module. A Redis PING alone is insufficient. Confirm the persistent volume is mounted. Never substitute an in-memory store. |
| `graph_query_failed` during dependency-off checks | Deliberate dependency unavailability/timeout probes log dependency failures. The final report requires all four operations to fail with the expected code. Unexpected unhandled failures abort the command. |
| Docker named-pipe / engine connection error | Start Docker Desktop's engine, then run `docker version`, `docker compose config --quiet`, and retry. Compose configuration validation does not verify image builds or runtime. |
| Native Python cannot reach Compose FalkorDB | The shipped database intentionally has no published port. Use the evaluation Compose profile on its private network, or an already configured private/local test server. Do not publish the database publicly. |
| Missing Python or libraries | Run `scripts/setup.ps1` with Python 3.12 installed. Then run `backend/.venv/Scripts/python.exe -m pip check`. No additional Phase 11 dependency is needed. |
| Existing report directory | Choose another `-OutputDirectory`; the runner refuses to overwrite a previous result. A fresh graph name is generated each run. |
| Existing graph / fixture not fresh | Do not reset a shared graph to make a test pass. Use the runner, which generates an isolated name and rejects an existing graph. |
| Agent recovery deadline | Inspect `backend-after.log` for typed dependency/lease/provider failures. Confirm mock mode, graph availability and no other worker is using the evaluation graph. Completed transitions remain persisted. |
| Run stops after three stages | This is the intended pre-restart boundary. After successful startup recovery all six are complete. If the command fails, evidence remains in the isolated graph; local reports are not recovery checkpoints. |
| 401 / 403 on manual API calls | Run `scripts/setup-api-auth.ps1` and load `scripts/api-client.ps1`. Use the demo tenant with read/write permissions. Evaluation child credentials expire with the owned process and are never printed. |
| 429 on repeated manual graph requests | Honor Retry-After. The evaluator uses a private scoped principal with a test quota; the ordinary API default stays 30 expensive queries/minute. |
| 409 during paginated graph reads | Start a new snapshot and repeat explicit `at` plus `snapshot_sequence`. Persisted writes invalidate old pagination fences. |
| Conflicting memories are not established facts | Expected. Both observations are untrusted data and need evidence review. Do not edit the report or promote a claim merely to obtain a passing demo. |
| No sensitive path after a custom event | Check explicit denies, missing constraints, timestamps, source evidence, tenant and bounded depth. Zero findings can mean unresolved authorization; see the analysis limitations. |
| Report missing after failed command | A final success report is written only after every required check passes. Preserve child logs and the isolated graph; fix the error and run a new isolated evaluation. |
| High or varying p95 | Inspect raw samples, VM/CPU contention and network conditions. Repeat with the same manifest. Do not replace measured values with target numbers. |

The runner terminates only subprocess handles it created. It leaves the shared FalkorDB,
other backends and evaluation graph intact. It does not restart the database or prove
crash/power-loss recovery. AOF `everysec` is not zero-loss durability or a backup strategy.

For a safe health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
docker compose logs --tail 100 backend
docker compose logs --tail 100 falkordb
```

Check [API contract](API_CONTRACT.md) for typed errors, replay cursors and scope rules.
Do not share `.env`, resolved Compose configuration, API token files or unredacted live
AWS reports. Synthetic evaluation JSON contains fabricated account/resource identifiers.
