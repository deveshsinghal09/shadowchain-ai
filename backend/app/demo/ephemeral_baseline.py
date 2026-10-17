"""Evaluation-only negative control. No database, files, graph, SDK or recovery code.

Each invocation is a fresh process. The write invocation retains one observation only
until it exits. The read invocation receives the identical lookup, without a write.
This deliberately weak baseline measures retention, not security analysis quality.
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write-and-read", "read"))
    args = parser.parse_args()
    payload = json.loads(sys.stdin.read(65537))
    observations = []
    if args.mode == "write-and-read":
        observations.append(payload["write"])
    subjects = set(payload["query"]["subject_ids"])
    matched = [m for m in observations if subjects.intersection(m["subject_ids"])]
    print(json.dumps({"count": len(matched), "persistent_storage": False}))


if __name__ == "__main__":
    main()
