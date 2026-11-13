"""Prepare isolated UI investigations from editable link parameters."""

from copy import deepcopy

from .investigation_tools import normalize_esn0


def prepare_ui_case(original: dict, question: str, conditions: dict) -> dict:
    case = deepcopy(original)
    case.update(conditions)
    case["incident"] = question
    changed = {name: {"original": original[name], "value": value}
               for name, value in conditions.items() if value != original[name]}
    if case["metric_kind"] is not None:
        esn0_db = normalize_esn0(case)
        if not -30 <= esn0_db <= 60:
            raise ValueError(f"换算后的 Es/N0 为 {esn0_db:.2f} dB，仿真支持范围为 -30 至 60 dB。")
    if changed:
        physical_change = any(name != "bler_limit" for name in changed)
        case["id"] = f"{original['id']}_custom"
        case["title"] = f"{original['title']}（自定义条件）"
        case["customization"] = {"source_case_id": original["id"], "changed_conditions": changed,
                                 "observations_reused": not physical_change}
        case["incident"] = (
            "请依据本次设置的链路参数和业务约束调查并比较候选配置。"
            "以下原始问题用于提供调查背景。\n\n" + question
        )
        if physical_change:
            case["incident"] = "链路参数已变更，历史数值和测量结论需要在新条件下重新验证。\n" + case["incident"]
            case["observations"] = [{
                "id": "obs.custom",
                "text": "链路条件由用户设置。本次证据从当前参数下的工具计算与新仿真建立，"
                        "原工单的历史观测保留在原始案例文件中。",
            }]
    return case
