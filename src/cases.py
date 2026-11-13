"""Load only public observations. Evaluation references are held separately."""

import json

from .paths import PROJECT_ROOT


def load_cases() -> list[dict]:
    data = json.loads((PROJECT_ROOT / "data" / "cases.json").read_text(encoding="utf-8"))
    return [{**data["shared"], **case} for case in data["cases"]]


def get_case(case_id: str) -> dict:
    for case in load_cases():
        if case["id"] == case_id:
            return case
    raise ValueError(f"Unknown case {case_id}.")
