"""Build independently seeded simulation references, inaccessible to agent tools."""

import argparse
import hashlib
import json
from pathlib import Path

from src.cases import load_cases
from src.investigation_tools import config_for_case, normalize_esn0
from src.paths import PROJECT_ROOT, WORK_ROOT
from src.simulation import simulate_link


def build_reference(output: Path, frames: int = 8192) -> dict:
    references = {}
    for index, case in enumerate(load_cases()):
        if case["channel"] is None or case["metric_kind"] is None:
            references[case["id"]] = {"decision": "insufficient_evidence", "acceptable_mcs": [], "reason": "缺少信道类型与指标定义"}
            continue
        results = [simulate_link(config_for_case(case, name, frames, 700000 + index * 1000 + offset))
                   for offset, name in enumerate(case["candidate_mcs"])]
        feasible = [result for result in results if result["bler_ci95"][1] <= case["bler_limit"]
                    and result["goodput_ci95_mbps"][0] >= case["min_goodput_mbps"]]
        best = max((result["goodput_mbps"] for result in feasible), default=0)
        acceptable = [result["config"]["mcs"] for result in feasible if result["goodput_mbps"] >= 0.95 * best]
        rejected = all(result["bler_ci95"][0] > case["bler_limit"] or
                       result["goodput_ci95_mbps"][1] < case["min_goodput_mbps"] for result in results)
        decision = "recommend" if feasible else ("no_feasible_option" if rejected else "insufficient_evidence")
        references[case["id"]] = {
            "decision": decision, "acceptable_mcs": acceptable,
            "normalized_esn0_db": normalize_esn0(case), "results": results,
        }
        print(case["id"], decision, acceptable, flush=True)
    content = {
        "frames_per_candidate": frames,
        "case_sha256": hashlib.sha256((PROJECT_ROOT / "data" / "cases.json").read_bytes()).hexdigest(),
        "simulation_sha256": hashlib.sha256((PROJECT_ROOT / "src" / "simulation.py").read_bytes()).hexdigest(),
        "references": references,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return content


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=WORK_ROOT / "evidence" / "reference.json")
    parser.add_argument("--frames", type=int, default=8192)
    arguments = parser.parse_args()
    build_reference(arguments.output, arguments.frames)
