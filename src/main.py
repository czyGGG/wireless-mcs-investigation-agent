"""Command-line entry point for a complete investigation."""

import argparse
import asyncio
import json
from datetime import datetime

from .agent import run_investigation
from .cases import get_case, load_cases
from .paths import WORK_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="无线调制编码调查 Agent")
    parser.add_argument("--case", default="C02", choices=[case["id"] for case in load_cases()])
    parser.add_argument("--group", default="C", choices=["A", "B", "C"])
    parser.add_argument("--seed", default=20261113, type=int)
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = WORK_ROOT / "evidence" / "investigations" / f"{timestamp}_{args.case}_{args.group}.json"
    report = asyncio.run(run_investigation(get_case(args.case), args.group, args.seed, path))
    print(json.dumps({key: report[key] for key in ("case_id", "group", "status", "elapsed_seconds", "usage")}, ensure_ascii=False, indent=2))
    print(json.dumps(report.get("output", {"error": report.get("error")}), ensure_ascii=False, indent=2))
    print(f"Evidence saved to {path}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
