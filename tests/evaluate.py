"""Run and score the preregistered A/B/C comparison, preserving every attempt."""

import argparse
import asyncio
import hashlib
import json
import math
import random
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.agent import run_investigation
from src.cases import load_cases
from src.model import load_config
from src.paths import PROJECT_ROOT, WORK_ROOT


def score_report(report: dict, reference: dict, repeat: int) -> dict:
    answer = report.get("output", {})
    reasons = []
    if report["status"] != "completed":
        reasons.append(report.get("error_type", "run_failed"))
    if answer.get("decision") != reference["decision"]:
        reasons.append("decision_mismatch")
    if reference["decision"] == "recommend" and answer.get("recommended_mcs") not in reference["acceptable_mcs"]:
        reasons.append("mcs_mismatch")
    if report["case_id"] == "C06":
        value = answer.get("normalized_esn0_db")
        if value is None or not math.isclose(value, reference["normalized_esn0_db"], abs_tol=0.01):
            reasons.append("snr_conversion")
    if report["case_id"] == "C08" and answer.get("pilot_sufficient") is not False:
        reasons.append("pilot_uncertainty")
    known_ids = {"obs.case", *report.get("evidence", {})}
    known_ids.update(observation["id"] for observation in report["case"]["observations"])
    citations = set(answer.get("evidence_ids", []))
    for hypothesis in answer.get("hypotheses", []):
        citations.update(hypothesis["supporting_evidence"])
        citations.update(hypothesis["opposing_evidence"])
    tool_successes, tool_attempts = 0, 0
    for message in report.get("messages", []):
        for part in message.get("parts", []):
            if part.get("tool_name") in (None, "final_result"):
                continue
            if part.get("part_kind") == "tool-return":
                tool_attempts += 1
                content = part.get("content")
                successful = part.get("outcome", "success") == "success"
                if isinstance(content, dict):
                    successful = successful and content.get("status") != "error"
                tool_successes += int(successful)
            elif part.get("part_kind") == "retry-prompt":
                tool_attempts += 1
    usage = report.get("usage", {})
    return {
        "case_id": report["case_id"], "group": report["group"], "repeat": repeat,
        "completed": int(report["status"] == "completed"), "task_success": int(not reasons),
        "decision": answer.get("decision"), "recommended_mcs": answer.get("recommended_mcs"),
        "expected_decision": reference["decision"],
        "acceptable_mcs": ",".join(reference["acceptable_mcs"]),
        "failure_reasons": ",".join(reasons), "elapsed_seconds": report["elapsed_seconds"],
        "requests": usage.get("requests", 0),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "frames_used": report.get("frames_used", 0),
        "valid_citation_rate": len(citations & known_ids) / len(citations) if citations else 0,
        "tool_successes": tool_successes, "tool_attempts": tool_attempts,
        "tool_success_rate": tool_successes / tool_attempts if tool_attempts else None,
    }


def summarize(output: Path, reference_file: Path) -> pd.DataFrame:
    reference = json.loads(reference_file.read_text(encoding="utf-8"))["references"]
    rows = []
    for path in sorted((output / "runs").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        rows.append(score_report(report, reference[report["case_id"]], int(path.stem.rsplit("r", 1)[1])))
    data = pd.DataFrame(rows).sort_values(["group", "case_id", "repeat"])
    data.to_csv(output / "evaluation.csv", index=False, encoding="utf-8-sig")
    summary = data.groupby("group").agg(
        planned_runs=("task_success", "size"), completed=("completed", "sum"),
        successes=("task_success", "sum"), task_success_rate=("task_success", "mean"),
        mean_seconds=("elapsed_seconds", "mean"), mean_input_tokens=("input_tokens", "mean"),
        mean_output_tokens=("output_tokens", "mean"), mean_requests=("requests", "mean"),
        mean_frames=("frames_used", "mean"), valid_citation_rate=("valid_citation_rate", "mean"),
        tool_successes=("tool_successes", "sum"), tool_attempts=("tool_attempts", "sum"),
    )
    summary["tool_success_rate"] = summary["tool_successes"].div(summary["tool_attempts"].replace(0, float("nan")))
    repeats = data.groupby(["group", "repeat"])["task_success"].mean().unstack()
    summary["repeat_success_min"] = repeats.min(axis=1)
    summary["repeat_success_max"] = repeats.max(axis=1)
    summary.to_csv(output / "summary.csv", encoding="utf-8-sig")
    data.pivot_table(index="case_id", columns="group", values="task_success", aggfunc="sum").to_csv(output / "case_successes.csv", encoding="utf-8-sig")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    figure, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    colors = ["#8696a7", "#3c86a0", "#177665"]
    axes[0].bar(summary.index, summary["task_success_rate"] * 100, color=colors)
    axes[0].set(ylabel="Task success (%)", ylim=(0, 110), title="8 cases, 3 repetitions per group")
    for index, row in enumerate(summary.itertuples()):
        axes[0].text(index, row.task_success_rate * 100 + 2, f"{row.successes}/{row.planned_runs}", ha="center")
    axes[1].bar(summary.index, summary["mean_seconds"], color=colors)
    axes[1].set(ylabel="Mean wall-clock time (s)", title="API waiting and tool execution included")
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.15)
        axis.set_axisbelow(True)
    figure.savefig(output / "comparison.png", dpi=180)
    plt.close(figure)
    return summary


async def evaluate(output: Path, reference_file: Path, repeats: int, concurrency: int):
    output.mkdir(parents=True, exist_ok=False)
    (output / "runs").mkdir()
    snapshot = output / "snapshot"
    snapshot.mkdir()
    for name in ("src", "data", "skills", "tests"):
        shutil.copytree(PROJECT_ROOT / name, snapshot / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(reference_file, output / "reference.json")
    config = load_config()
    configuration = {
        "model": config.model_name, "thinking": config.thinking,
        "temperature": 0, "repeats": repeats, "concurrency": concurrency,
        "cases": [case["id"] for case in load_cases()],
        "source_hashes": {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in snapshot.rglob("*") if path.is_file()},
    }
    (output / "experiment_config.json").write_text(json.dumps(configuration, indent=2), encoding="utf-8")
    jobs = [(case, group, repeat, 20261113 + index * 100 + repeat * 10000)
            for index, case in enumerate(load_cases())
            for group in ("A", "B", "C") for repeat in range(1, repeats + 1)]
    random.Random(113).shuffle(jobs)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(case, group, repeat, seed):
        async with semaphore:
            path = output / "runs" / f"{case['id']}_{group}_r{repeat}.json"
            report = await run_investigation(case, group, seed, path)
            print(f"{path.stem} {report['status']} {report.get('output', {}).get('recommended_mcs')} {report['elapsed_seconds']:.1f}s", flush=True)

    await asyncio.gather(*(run_one(*job) for job in jobs))
    print(summarize(output, output / "reference.json").to_string(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=WORK_ROOT / "evidence" / f"evaluation_{datetime.now():%Y%m%d_%H%M%S}")
    parser.add_argument("--reference", type=Path, default=WORK_ROOT / "evidence" / "reference.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.summarize_only:
        print(summarize(args.output, args.reference).to_string())
    else:
        asyncio.run(evaluate(args.output, args.reference, args.repeats, args.concurrency))
