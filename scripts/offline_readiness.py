#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from clinic_copilot.offline_readiness import evaluate_offline_readiness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clinic Copilot offline readiness checker with optional Ollama pre-pull."
    )
    parser.add_argument("--workspace", default=".", help="Workspace root path")
    parser.add_argument("--prepull", action="store_true", help="Pre-pull required Ollama models")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    payload = evaluate_offline_readiness(workspace=workspace, prepull=args.prepull)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("Offline readiness report")
        print(f"Workspace: {payload['workspace']}")
        print(f"Requested models: {', '.join(requested_models) if requested_models else 'none'}")
        for item in checks:
            status = "PASS" if item.ok else "FAIL"
            print(f"- [{status}] {item.name}: {item.detail}")
        print(f"Ready: {payload['ready']}")

    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
