# SHADOWCHAIN AI: deterministic backend demonstration

This demonstration uses the versioned, synthetic AWS fixture and mock agent provider.
It needs FalkorDB and Python 3.12, with no AWS account, AWS credentials, or LLM API key.
FalkorDB performs the bounded temporal traversals and stores agent memory, evidence,
task ownership, handoffs, investigations, and evaluation receipts. No frontend is included.

## One command

From the repository root, with the existing virtual environment and a reachable,
authenticated FalkorDB configured through `.env` or environment variables:

```powershell
.\scripts\demo-hackathon.ps1
```

On this verified workstation the existing native verification server listens on loopback
port 16379. The repository `.env` retains the Compose hostname, so select the native
endpoint explicitly when reproducing that run:

```powershell
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16379'
.\scripts\demo-hackathon.ps1
```

The existing `.env` supplies the local database secret; no secret appears in this command.
These overrides apply to the current PowerShell session. Other machines should use their
own configured private endpoint or the Compose command below.

The script creates a **new** `shadowchain_eval_<uuid>` graph each time. It does not reset
existing graphs, restart your running backend, or stop FalkorDB. It starts two private
loopback FastAPI child processes in succession, terminates only its own processes, and
retains the evaluation graph. An ephemeral API credential is held in process memory;
only its digest is sent to the child configuration. Credentials are not exported.

Optional output destination and sample count:

```powershell
.\scripts\demo-hackathon.ps1 -Repetitions 20 -OutputDirectory artifacts/hackathon/my-demo
```

Choose a new directory for each run. The default is
`artifacts/reports/hackathon/shadowchain_eval_<uuid>/`. Reports are derived artifacts;
the workflow never reads them for restart recovery. The report itself is also saved
as bounded Evidence nodes linked by DERIVED_FROM in FalkorDB.

Python equivalent:

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
.\backend\.venv\Scripts\python.exe -u -m app.demo.hackathon --repetitions 20
```

For a new setup, `.\scripts\setup.ps1` installs the pinned dependencies and retains an
existing `.env`. The shipped Compose database is private: it has no host port mapping.
Do not publish it merely to run this command from the host. Run the evaluator on the
Compose private network instead:

```powershell
.\scripts\setup.ps1 -SkipPython
docker compose build backend
docker compose --profile evaluation run --rm evaluation
```

The evaluation service uses the same image and persistent graph/report volumes, forces
AWS off and mock mode, has no published ports, and needs no external network. Its
report location is printed at completion. Docker execution remains unverified on this
workstation because the Docker Desktop engine is unavailable; the native command has
been exercised against the pinned FalkorDB server. See [verification](phase11_verification.md).

## Five demonstrations and suggested narration

1. **Invisible access path.** The runner queries 09:00 before applying any scenario
   changes. It replays one change each at 10:00, 11:00 and 12:00 and executes real
   Cypher traversal at each boundary. Expected potential path counts are `0, 0, 0, 1`.
   Show `demo1.timeline`, then `demo1.finding.relationship_ids`, the linked contributing
   events and `demo1.introduction`. The final change creates first modeled reachability;
   source-event links explain the change chain. Correlation alone is not causality.
2. **Shared memory.** Risk Oracle stores an episodic finding with source evidence in
   the authorized workflow memory space. After restart, Memory Keeper reads its memory
   ID and continues. Show `demo2_and_3.shared_memory` and `reader_run`, including the
   finding IDs, evidence references and `retrieved_memory_ids`.
3. **Actual backend restart.** Exactly three of the six agent stages finish initially.
   Pending tasks and handoffs remain in FalkorDB. The first FastAPI process exits;
   another process starts and its normal recovery worker discovers the incomplete run.
   No resume request is issued until recovery has finished. Show the distinct PIDs,
   `completed_before_restart: 3`, `completed_after_restart: 6`, three unchanged run
   records, six completed step tasks and seven unique task IDs. A final explicit resume
   and duplicate trigger are idempotent. One human recommendation review remains pending.
4. **Conflicting memory.** A suspicious public-access claim and newer disputing policy
   evidence are stored by different agents. Both observations and evidence survive.
   Show `demo4.conflicts`, both `observations`, and `definitive_fact_count: 0`.
   The evaluator does not auto-review or resolve either observation. Verification is required.
5. **What-if remediation.** Show the exact excluded relationship, supporting evidence,
   path counts and maximum heuristic score before/after exclusion. The same simulation
   is executed twice. Matching source-graph hashes verify the real synthetic permissions
   and historical records are unchanged. No AWS SDK session or IAM writer is needed.

All findings are **POTENTIAL ATTACK PATHS** in a documented IAM subset. Zero paths is
not a safety certification. The score is a prioritization heuristic, not an exploitation
probability. The simple three-change fixture has no remaining sensitive path after the
chosen exclusion; the separate [remediation proof](remediation_proof.json) and integration
tests exercise alternate paths and collateral access.

## Outputs

| File | Contents |
| --- | --- |
| `evaluation.json` | Actual assertions, timeline, finding, memories, handoffs, ablation, raw latency samples |
| `sample-investigation.json` | Actual authenticated case-list HTTP response |
| `sample-investigation-detail.json` | Actual case detail, source evidence, tasks, timeline and response |
| `sample-dashboard.json` | Actual dashboard HTTP response |
| `backend-before.log`, `backend-after.log` | Structured logs for the owned child processes |

Committed execution examples are [evaluation](hackathon_evaluation.json),
[investigation detail](hackathon_investigation_example.json), and
[test report](phase11_test_report.json). [Evaluation methodology](EVALUATION.md) defines
the denominators and limitations. [API requests](HACKATHON_API_REQUESTS.md) work against
an ordinary authenticated backend. [Troubleshooting](TROUBLESHOOTING.md) covers failures.

## Inspect the retained graph

The command prints its unique graph name. Point a separate local backend at that graph
with `FALKORDB_GRAPH=<printed-name>` and use your normal API credential from
`scripts/setup-api-auth.ps1`; the evaluation's temporary credential is not reusable.
Do not change the graph of an already running process and expect it to switch databases.
The new backend will read the persisted cases, runs, tasks and memories directly.

The evaluator can reconstruct its receipt with a fresh GraphDatabase connection:

```python
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.demo.evaluation import load_document

db = GraphDatabase(Settings())  # FALKORDB_GRAPH selects the printed evaluation graph
try:
    report = load_document(db, "result")
    print(report["metrics"])
finally:
    db.close()
```

Evaluation graphs are retained deliberately. Cleanup is an explicit administrative
operation against an exact graph name, after preserving any needed evidence. Never use
FLUSHDB, FLUSHALL, wildcard graph deletion or `docker compose down --volumes` for replay.
