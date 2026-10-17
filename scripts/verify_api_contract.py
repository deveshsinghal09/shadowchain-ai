"""Exercise the synthetic authenticated REST and real WebSocket API. No AWS SDK is used."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.sync.client import connect


def require(condition, message):
    # Verification must remain active when Python is launched with -O.
    if not condition:
        raise RuntimeError("API verification failed: " + message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if (
        target.scheme != "http"
        or target.hostname not in {"localhost", "127.0.0.1"}
        or target.username
        or target.password
    ):
        parser.error("Verification is restricted to an explicit local HTTP backend")
    token = args.token_file.read_text(encoding="utf-8").strip()
    examples = {
        "synthetic": True,
        "schema_version": "1",
        "requests": [],
        "responses": {},
    }
    api = args.base_url.rstrip("/") + "/api/v1"
    socket_url = "ws://" + target.netloc + "/api/v1/ws/live"
    with httpx.Client(
        headers={"Authorization": "Bearer " + token}, timeout=45, trust_env=False
    ) as client:

        def request(method, path, *, body=None, params=None, name=None):
            result = client.request(method, api + path, json=body, params=params)
            if result.status_code != 200:
                raise RuntimeError(
                    "Verification request failed: "
                    + method
                    + " "
                    + path
                    + " "
                    + str(result.status_code)
                )
            value = result.json()
            examples["requests"].append(
                {
                    "method": method,
                    "path": "/api/v1" + path,
                    **({"body": body} if body is not None else {}),
                    **({"query": params} if params else {}),
                }
            )
            if name:
                examples["responses"][name] = value
            return value

        require(
            client.get(
                api + "/graph/snapshot", headers={"Authorization": ""}
            ).status_code
            == 401,
            "unauthenticated graph request was accepted",
        )
        request("GET", "/health", name="health")
        request("GET", "/ready", name="ready")
        flags = request("GET", "/aws/status", name="aws_status")
        require(
            not flags["aws_enabled"] and not flags["cloudtrail_enabled"],
            "AWS integrations must be disabled for this synthetic proof",
        )
        request("POST", "/demo/seed", name="seed")
        state = request("GET", "/demo/scenarios/A")
        with connect(socket_url, open_timeout=5, max_size=65536) as ws:
            ws.send(
                json.dumps(
                    {"type": "authenticate", "token": token, "after": 0, "limit": 1}
                )
            )
            hello = json.loads(ws.recv(timeout=5))
            require(hello["type"] == "hello", "WebSocket did not authenticate")
            first = json.loads(ws.recv(timeout=5))
            require(
                first["type"] == "events", "WebSocket did not deliver persisted events"
            )
            examples["responses"]["websocket_hello"] = hello
            examples["responses"]["websocket_events"] = first
            replay = request(
                "POST",
                "/demo/replay",
                body={"scenario": "A", "expected_generation": state["generation"]},
                name="replay",
            )
            expected = len(replay["applied_event_ids"])
            seen = []
            while len(seen) < expected:
                frame = json.loads(ws.recv(timeout=10))
                if frame["type"] == "events":
                    seen.extend(frame["events"])

        def complete_edges(hour, name):
            query = {
                "at": f"2026-10-16T{hour:02d}:00:00Z",
                "scenario": "A",
                "collection": "edges",
                "limit": 100,
            }
            page = request("GET", "/graph/snapshot", params=query, name=name)
            edges = list(page["edges"])
            while page["has_more"]:
                query.update(
                    cursor=page["next_cursor"],
                    snapshot_sequence=page["snapshot_sequence"],
                )
                page = request("GET", "/graph/snapshot", params=query)
                edges.extend(page["edges"])
            return edges

        source_snapshots = {
            hour: complete_edges(hour, f"graph_{hour:02d}") for hour in range(9, 13)
        }

        def source_digest(edges):
            projection = [
                {
                    key: value
                    for key, value in edge.items()
                    if key
                    not in {
                        "evidence_ids",
                        "investigation_ids",
                        "risk_severity",
                        "associations_truncated",
                    }
                }
                for edge in edges
            ]
            return hashlib.sha256(
                json.dumps(projection, sort_keys=True).encode()
            ).hexdigest()

        before_digest = source_digest(source_snapshots[12])
        manifest = request("GET", "/demo/scenarios")
        source_event = manifest["scenarios"]["A"][-1]["event_id"]
        snapshot = request(
            "GET", "/demo/scenarios/A/snapshot", params={"at": "2026-10-16T12:00:00Z"}
        )
        event = next(n for n in snapshot["nodes"] if n.get("event_id") == source_event)
        workflow = request(
            "POST",
            "/agents/run",
            body={"request_id": "demo/phase10/api-proof", "event_id": event["id"]},
            name="agent_workflow",
        )
        require(workflow["status"] == "completed", "agent workflow did not complete")
        request("GET", "/agents", name="agents")
        request(
            "GET",
            "/agents/runs",
            params={"investigation_id": workflow["id"]},
            name="agent_runs",
        )
        request(
            "GET",
            "/agents/memory",
            params={"investigation_id": workflow["id"]},
            name="agent_memory",
        )
        cases = request("GET", "/investigations", name="investigations")
        case_id = cases["investigations"][0]["investigation_id"]
        request("GET", "/investigations/" + case_id, name="investigation")
        request(
            "POST",
            "/investigations/" + case_id + "/resume",
            name="resumed_investigation",
        )
        risks = request("GET", "/risks", name="risks")
        risk_id = risks["risks"][0]["risk_id"]
        risk = request("GET", "/risks/" + risk_id, name="risk")
        request("GET", "/graph/path", params={"risk_id": risk_id}, name="path")
        simulation = request(
            "POST",
            "/what-if/remove-grant",
            body={
                "at": "2026-10-16T12:00:00Z",
                "remove_relationship_ids": [risk["risk"]["relationship_ids"][0]],
            },
            name="simulation",
        )
        require(
            not simulation["graph_mutated"] and not simulation["after"]["findings"],
            "simulation mutated the source graph or failed to break the path",
        )
        after_digest = source_digest(complete_edges(12, "graph_after_simulation"))
        require(before_digest == after_digest, "simulation changed source permissions")
        request("GET", "/dashboard/summary", name="dashboard")
        request(
            "GET",
            "/graph/diff",
            params={
                "before": "2026-10-16T09:00:00Z",
                "after": "2026-10-16T12:00:00Z",
                "scenario": "A",
            },
            name="graph_diff",
        )
        request(
            "GET",
            "/graph/timeline",
            params={"before": "2026-10-16T09:00:00Z", "after": "2026-10-16T13:00:00Z"},
            name="graph_timeline",
        )
        cstate = request("GET", "/demo/scenarios/C")
        request(
            "POST",
            "/events/ingest",
            body={
                "event": manifest["scenarios"]["C"][0],
                "expected_generation": cstate["generation"],
            },
            name="event_receipt",
        )
        # Reconnect with the last processed event cursor; subsequent history comes from FalkorDB.
        after = first["cursor"]
        persisted = request(
            "GET",
            "/live/events",
            params={"after": after, "limit": 50},
            name="replay_events",
        )
        with connect(socket_url, open_timeout=5, max_size=65536) as ws:
            ws.send(
                json.dumps(
                    {
                        "type": "authenticate",
                        "token": token,
                        "after": after,
                        "limit": 50,
                    }
                )
            )
            require(
                json.loads(ws.recv(timeout=5))["type"] == "hello",
                "reconnect authentication",
            )
            require(
                json.loads(ws.recv(timeout=5)) == persisted,
                "reconnected event replay differs",
            )
        openapi = client.get(args.base_url.rstrip("/") + "/openapi.json").json()
    root = Path(__file__).resolve().parents[1]
    output = args.output_directory or root / "docs"
    output.mkdir(parents=True, exist_ok=True)
    (output / "OPENAPI_V1.json").write_text(
        json.dumps(openapi, indent=2) + "\n", encoding="utf-8"
    )
    (output / "api_contract_examples.json").write_text(
        json.dumps(examples, indent=2) + "\n", encoding="utf-8"
    )
    counts = {f"{hour:02d}:00": len(source_snapshots[hour]) for hour in range(9, 13)}
    proof = {
        "synthetic": True,
        "schema_version": "1",
        "authenticated": True,
        "aws_calls": 0,
        "graph_edge_counts": counts,
        "workflow_completed": True,
        "agents": len(examples["responses"]["agents"]),
        "replay_page_events": len(persisted["events"]),
        "replay_cursor": persisted["cursor"],
        "reconnected_events_equal": True,
        "live_high_watermark": persisted["high_watermark"],
        "what_if_breaks_path": True,
        "source_permissions_unchanged": True,
        "source_projection_sha256_before": before_digest,
        "source_projection_sha256_after": after_digest,
    }
    (output / "api_contract_proof.json").write_text(
        json.dumps(proof, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(proof))


if __name__ == "__main__":
    main()
