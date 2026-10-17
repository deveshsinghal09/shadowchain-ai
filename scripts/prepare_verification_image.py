"""Download digest-verified official image layers for daemon-free WSL verification.

This optional developer tool does not install a database substitute or change Docker.
Extraction and server launch are deliberately separate, explicit local operations.
"""

import hashlib
import json
from pathlib import Path

import httpx

IMAGE = "falkordb/falkordb-server"
DIGEST = "sha256:2e99eb1cfa3309ce338beebe24b5e9f44f9eb8ead6d6ef63dcaca436afbbaeec"
REGISTRY = "https://registry-1.docker.io/v2/"


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / ".verification" / "image"
    destination.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        token_response = client.get(
            "https://auth.docker.io/token",
            params={
                "service": "registry.docker.io",
                "scope": f"repository:{IMAGE}:pull",
            },
        )
        token_response.raise_for_status()
        headers = {
            "Authorization": f"Bearer {token_response.json()['token']}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
        }

        def manifest(digest: str) -> dict:
            response = client.get(f"{REGISTRY}{IMAGE}/manifests/{digest}", headers=headers)
            response.raise_for_status()
            if "sha256:" + hashlib.sha256(response.content).hexdigest() != digest:
                raise RuntimeError("Manifest digest mismatch")
            return response.json()

        root = manifest(DIGEST)
        if "manifests" in root:
            selected = next(
                item
                for item in root["manifests"]
                if item.get("platform", {}).get("os") == "linux"
                and item.get("platform", {}).get("architecture") == "amd64"
            )
            root = manifest(selected["digest"])
        filenames = []
        for position, layer in enumerate(root["layers"]):
            digest = layer["digest"]
            filename = f"{position:02}-{digest.split(':')[1]}.tar.gz"
            path = destination / filename
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest[7:]:
                with client.stream(
                    "GET", f"{REGISTRY}{IMAGE}/blobs/{digest}", headers=headers
                ) as response:
                    response.raise_for_status()
                    with path.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest[7:]:
                    raise RuntimeError("Layer digest mismatch")
            filenames.append(filename)
            print(
                f"Verified layer {position + 1}/{len(root['layers'])}: {layer['size']} bytes",
                flush=True,
            )
        (destination / "layers.json").write_text(json.dumps(filenames), encoding="utf-8")
        print("Official image layers ready; no Docker installation files changed.")


if __name__ == "__main__":
    main()
