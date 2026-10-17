"""Run all five proofs with owned FastAPI subprocesses: python -m app.demo.hackathon."""

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
from pydantic import SecretStr

from app.core.auth import ApiClient
from app.core.config import PROJECT_ROOT, Settings
from app.core.errors import DatabaseUnavailable
from app.core.logging import configure_logging
from app.db.cypher import fingerprint
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.demo.evaluation import (
    SCOPE,
    conflict_and_ablation_setup,
    finish,
    load_document,
    prepare,
    require,
    scenario_services,
    workflow_for,
)
from app.models.events import DEMO_TENANT, Scenario
from app.models.memory import MemoryQuery
from app.repositories.memory import MemoryRepository
from app.schemas.risk import AnalysisRequest
from app.services.memory import MemoryService

APPLICATION_ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def server_pid(log_path):
    # Windows venv launchers can wrap the server process. Record Uvicorn's real
    # PID separately from the subprocess handle used for safe lifecycle control.
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        match = re.fullmatch(r"Started server process \[(\d+)\]", record.get("message", ""))
        if record.get("logger") == "uvicorn.error" and match:
            return int(match.group(1))
    raise RuntimeError("Evaluation failed: owned server PID missing from startup log")


def wait_for_listener_exit(port):
    deadline = monotonic() + 5
    while True:
        with socket.socket() as probe:
            if os.name == "nt":
                # Windows loopback can drop SYNs rather than reject a closed port.
                # Exclusive binding proves the previous listener released ownership.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                probe.listen(1)
            except OSError:
                pass
            else:
                return
        require(monotonic() < deadline, "owned backend listener survived process termination")
        sleep(0.05)


def child_environment(settings, recovery):
    environment = os.environ.copy()
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            encoded = value.get_secret_value()
        elif value is None:
            encoded = ""
        elif isinstance(value, (list, dict)):
            encoded = json.dumps(settings.model_dump(mode="json")[name])
        elif isinstance(value, bool):
            encoded = str(value).lower()
        else:
            encoded = str(value)
        environment[name.upper()] = encoded
    environment.update(
        PYTHONPATH=str(APPLICATION_ROOT),
        PYTHONUNBUFFERED="1",
        WORKFLOW_RECOVERY_ENABLED=str(recovery).lower(),
        GEMINI_API_KEY="",
        LOG_LEVEL="INFO",
        AWS_EC2_METADATA_DISABLED="true",
    )
    return environment


@contextmanager
def backend_process(settings, port, log_path, *, recovery):
    # This handle identifies ONLY our child; shared/user-owned servers are never killed.
    with log_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(  # noqa: S603 -- fixed executable/module/args, no shell
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
                "--no-proxy-headers",
            ],
            cwd=APPLICATION_ROOT,
            env=child_environment(settings, recovery),
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False
            ) as client:
                deadline = monotonic() + 60
                while True:
                    require(process.poll() is None, "owned backend exited; inspect its log")
                    try:
                        ready = client.get("/api/v1/ready")
                        if ready.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    require(monotonic() < deadline, "backend readiness deadline")
                    sleep(0.2)
                yield process, client
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def ephemeral_ablation(fixture, settings):
    payload = fixture["input"]
    results = []
    for mode, data in (
        ("write-and-read", payload),
        ("read", {"query": payload["query"]}),
    ):
        result = subprocess.run(  # noqa: S603 -- fixed local module, data via stdin
            [sys.executable, "-m", "app.demo.ephemeral_baseline", mode],
            input=json.dumps(data),
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
            cwd=APPLICATION_ROOT,
            env=child_environment(settings, False),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        results.append(json.loads(result.stdout))
    return {
        "before_restart_count": results[0]["count"],
        "after_restart_count": results[1]["count"],
        "input_sha256": fingerprint(payload),
        "baseline_processes": 2,
        "persistent_storage": False,
        "graph_reproduced": False,
    }


def persistent_read(settings, query):
    result = subprocess.run(  # noqa: S603 -- fixed local module; lookup via stdin
        [sys.executable, "-m", "app.demo.persistent_reader"],
        input=json.dumps({"query": query}),
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
        cwd=APPLICATION_ROOT,
        env=child_environment(settings, False),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout)


def check_graph_unavailable(settings, fixture):
    # Reserve a loopback port without listening: reproducible refusal, no server stopped.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        offline = settings.model_copy(
            update={
                "falkordb_host": "127.0.0.1",
                "falkordb_port": reserved.getsockname()[1],
                "falkordb_tls": False,
                "falkordb_retry_attempts": 1,
                "falkordb_connect_timeout_seconds": 0.2,
                "falkordb_socket_timeout_seconds": 0.3,
                "falkordb_query_timeout_ms": 100,
            }
        )
        database = GraphDatabase(offline)
        try:
            _, risk = scenario_services(database)
            memory = MemoryService(
                MemoryRepository(database, SCOPE, fixture["space_id"], fixture["reader_agent_id"])
            )
            probes = {
                "readiness": database.check_ready,
                "attack_path_discovery": lambda: risk.discover(
                    AnalysisRequest(at=hour(12), scenario=Scenario.A)
                ),
                "shared_memory": lambda: memory.retrieve_related_memories(
                    MemoryQuery.model_validate(fixture["input"]["query"])
                ),
                "workflow_reconstruction": lambda: workflow_for(
                    database, offline
                ).repository.incomplete(),
            }
            results = []
            for name, probe in probes.items():
                try:
                    probe()
                except DatabaseUnavailable as exc:
                    results.append(
                        {
                            "operation": name,
                            "error_code": exc.code,
                            "test": "non-listening loopback dependency (refusal or timeout)",
                            "transport_failure": type(exc.__cause__).__name__,
                        }
                    )
                else:
                    raise RuntimeError("Graph-off operation unexpectedly succeeded: " + name)
            return results
        finally:
            database.close()


def run(settings, output, *, repetitions=20, progress=print):
    require(
        settings.falkordb_graph.startswith(("shadowchain_eval_", "test_shadowchain_")),
        "isolated graph name required",
    )
    require(
        settings.demo_mode
        and settings.app_env != "production"
        and settings.agent_provider == "mock"
        and not settings.aws_enabled
        and not settings.cloudtrail_enabled,
        "local synthetic mock configuration required",
    )
    require(not output.exists(), "report directory already exists; choose a new directory")
    require(5 <= repetitions <= 100, "latency repetition bound")
    output.mkdir(parents=True)
    token = secrets.token_urlsafe(48)
    settings = settings.model_copy(
        update={
            "api_clients": [
                ApiClient(
                    principal_id="evaluation",
                    tenant_id=DEMO_TENANT,
                    token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                    permissions=["read", "write"],
                )
            ],
            "api_query_limit_per_minute": 1000,
            "workflow_recovery_enabled": False,
        }
    )
    headers = {"Authorization": "Bearer " + token}
    database = GraphDatabase(settings)
    try:
        require(
            settings.falkordb_graph not in database._get_client().execute_command("GRAPH.LIST"),
            "evaluation graph exists; no reset performed",
        )
    finally:
        database.close()
    port = free_port()
    progress("1/5 Starting owned FastAPI; replaying the three synthetic changes.")
    with backend_process(settings, port, output / "backend-before.log", recovery=False) as (
        first,
        client,
    ):
        database = GraphDatabase(settings)
        try:
            receipt = prepare(database, settings)
            memory_fixture = conflict_and_ablation_setup(database)
        finally:
            database.close()
        response = client.get(
            "/api/v1/agents/investigations/" + receipt["workflow_id"], headers=headers
        )
        response.raise_for_status()
        require(response.json()["completed_steps"] == 3, "HTTP partial workflow state")
        baseline = ephemeral_ablation(memory_fixture["ablation"], settings)
        read = persistent_read(settings, memory_fixture["ablation"]["input"]["query"])
        require(read["input_sha256"] == baseline["input_sha256"], "persistent arm input digest")
        baseline["persistent_before_restart_count"] = read["count"]
        first_pid = server_pid(output / "backend-before.log")
    wait_for_listener_exit(port)
    # All service clients and first backend are closed. Only graph IDs identify state.
    progress("2/5 Restarting FastAPI; waiting for graph-backed startup recovery.")
    with backend_process(settings, port, output / "backend-after.log", recovery=True) as (
        second,
        client,
    ):
        deadline = monotonic() + 60
        while True:
            response = client.get(
                "/api/v1/agents/investigations/" + receipt["workflow_id"], headers=headers
            )
            response.raise_for_status()
            if response.json()["status"] == "completed":
                break
            require(monotonic() < deadline, "startup recovery deadline")
            sleep(0.2)
        process_proof = {
            "first_pid": first_pid,
            "second_pid": server_pid(output / "backend-after.log"),
            "first_launcher_pid": first.pid,
            "second_launcher_pid": second.pid,
            "first_listener_closed": True,
            "first_process_exited": first.poll() is not None,
            "completed_before_restart": 3,
            "completed_after_restart": 6,
            "startup_recovered_without_resume_request": True,
            "mode": "owned FastAPI process termination and new process; FalkorDB remains running",
        }
        response = client.post(
            "/api/v1/agents/investigations/" + receipt["workflow_id"] + "/resume", headers=headers
        )
        response.raise_for_status()
        require(response.json()["completed_steps"] == 6, "explicit HTTP resume retry")
        read = persistent_read(settings, memory_fixture["ablation"]["input"]["query"])
        require(read["input_sha256"] == baseline["input_sha256"], "recovered arm input digest")
        baseline["persistent_after_restart_count"] = read["count"]
        baseline["persistent_reader_processes"] = 2
        progress("3/5 Checking shared evidence, conflict quarantine and the ephemeral baseline.")
        unavailable = check_graph_unavailable(settings, memory_fixture["ablation"])
        progress("4/5 Verifying non-destructive remediation and measuring real graph operations.")
        database = GraphDatabase(settings)
        try:
            report = finish(database, settings, process_proof, baseline, unavailable, repetitions)
            require(load_document(database, "result") == report, "final persistent report")
        finally:
            database.close()
        # Actual HTTP outputs, redacted synthetic input only; no credentials serialized.
        for name, route in (
            ("investigation", "/api/v1/investigations"),
            ("dashboard", "/api/v1/dashboard/summary"),
        ):
            response = client.get(route, headers=headers)
            response.raise_for_status()
            (output / f"sample-{name}.json").write_text(response.text, encoding="utf-8")
            if name == "investigation":
                body = response.json()
                # Full detail contains the evidence and historical timeline.
                cases = body["investigations"]
                if cases:
                    detail = client.get(
                        "/api/v1/investigations/" + cases[0]["investigation_id"], headers=headers
                    )
                    detail.raise_for_status()
                    (output / "sample-investigation-detail.json").write_text(
                        detail.text, encoding="utf-8"
                    )
        (output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    progress("5/5 Passed. Reports exported; graph evidence retained in " + settings.falkordb_graph)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--repetitions", type=int, default=20, choices=range(5, 101), metavar="5..100"
    )
    args = parser.parse_args()
    graph = "shadowchain_eval_" + uuid4().hex
    settings = Settings(
        falkordb_graph=graph,
        aws_enabled=False,
        cloudtrail_enabled=False,
        export_mode="local",
        agent_provider="mock",
        gemini_api_key=None,
    )
    configure_logging(settings.log_level)
    output = args.output or PROJECT_ROOT / settings.export_local_dir / "hackathon" / graph
    try:
        report = run(settings, output.resolve(), repetitions=args.repetitions)
    except Exception as exc:
        # Do not echo exception arguments, environment, response bodies or credentials.
        message = "Evaluation failed (" + type(exc).__name__ + ")."
        if isinstance(exc, RuntimeError) and str(exc).startswith("Evaluation failed: "):
            # require() accepts trusted, static assertion descriptions only.
            message = str(exc) + "."
        print(
            message + " Inspect local backend logs and troubleshooting guide.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "graph": graph,
                "report": str(output.resolve() / "evaluation.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
