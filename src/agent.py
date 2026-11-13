"""A/B/C investigation configurations with actual, inspectable tool traces."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.usage import UsageLimits

from .investigation_tools import (
    InvestigationContext, calculate_link_limits, compare_mcs_results,
    get_link_case, simulate_mcs,
)
from .model import create_model, load_config
from .paths import PROJECT_ROOT
from .simulation import MCSName


class Hypothesis(BaseModel):
    explanation: str
    status: Literal["supported", "refuted", "undetermined"]
    supporting_evidence: list[str]
    opposing_evidence: list[str]


class InvestigationResult(BaseModel):
    decision: Literal["recommend", "no_feasible_option", "insufficient_evidence"]
    recommended_mcs: MCSName | None
    normalized_esn0_db: float | None = Field(allow_inf_nan=False)
    summary: str
    hypotheses: list[Hypothesis] = Field(min_length=2)
    evidence_ids: list[str]
    pilot_sufficient: bool | None
    uncertainty: str
    missing_information: list[str]
    next_actions: list[str]

    @model_validator(mode="after")
    def validate_recommendation(self):
        if (self.decision == "recommend") != (self.recommended_mcs is not None):
            raise ValueError("A recommendation requires an MCS; other decisions require null.")
        return self


BASE_INSTRUCTIONS = """你是无线链路测试工程师的调查助手。根据给定工单调查原因并选择配置。
目标是在满足工单 BLER 和最低有效吞吐约束的候选中选择高有效吞吐方案。
使用中文说明，提出至少两个有意义的候选解释，区分支持证据、反面证据和仍待确认的问题。
引用原始数据时使用 obs.case 或工单 observations 中已有的 id。工具证据使用其返回的 evidence_id。
输出证据编号应与实际可获得的材料对应。pilot_sufficient 表示原始先导样本本身是否足以保证业务约束，
工单无先导样本时填 null。normalized_esn0_db 表示固定物理条件下的符号能量比，信息不足时填 null。
如缺少形成结论的必要证据，请说明具体缺口及下一步行动。
"""


async def run_investigation(
    case: dict, group: Literal["A", "B", "C"] = "C", seed: int = 20261113,
    output_path: Path | None = None,
) -> dict:
    config = load_config()
    if config.model_name != "deepseek-flash":
        raise ValueError("This project uses deepseek-flash for all groups.")
    skill_text = ""
    if group == "C":
        skill_text = (PROJECT_ROOT / "skills" / "wireless-mcs-investigation" / "SKILL.md").read_text(encoding="utf-8")
    instructions = BASE_INSTRUCTIONS + ("\n" + skill_text if skill_text else "")
    tools = [] if group == "A" else [get_link_case, calculate_link_limits, simulate_mcs, compare_mcs_results]
    agent = Agent(
        create_model(config), name=f"wireless_{group}", deps_type=InvestigationContext,
        instructions=instructions, output_type=InvestigationResult, tools=tools,
        model_settings=OpenAIChatModelSettings(
            thinking=config.thinking, temperature=0, max_tokens=3000, timeout=90,
            max_retries=1, parallel_tool_calls=False,
        ), retries=2,
    )
    state = InvestigationContext(case=case, seed=seed)
    prompt = "请完成这次调查。以下为本次实验全部原始公开数据。\n" + json.dumps(case, ensure_ascii=False)
    started = time.perf_counter()
    report = {
        "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "case_id": case["id"], "group": group, "model": config.model_name,
        "thinking": config.thinking, "temperature": 0, "seed": seed,
        "max_requests": 16, "max_tool_calls": 24, "frame_budget": state.frame_budget,
        "skill_loaded": bool(skill_text),
        "skill_sha256": hashlib.sha256(skill_text.encode()).hexdigest() if skill_text else None,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "case": case,
    }
    with capture_run_messages() as messages:
        try:
            result = await agent.run(
                prompt, deps=state,
                usage_limits=UsageLimits(request_limit=16, tool_calls_limit=24, total_tokens_limit=90000),
            )
            usage = result.usage
            report.update(
                status="completed", output=result.output.model_dump(),
                usage={"requests": usage.requests, "input_tokens": usage.input_tokens,
                       "output_tokens": usage.output_tokens},
            )
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__,
                          error=str(error).replace(config.api_key.get_secret_value(), "[redacted]"))
    report["elapsed_seconds"] = time.perf_counter() - started
    report["frames_used"] = state.frames_used
    report["evidence"] = state.evidence
    report["messages"] = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    if "usage" not in report:
        responses = [message for message in report["messages"] if message.get("kind") == "response"]
        report["usage"] = {
            "requests": len(responses),
            "input_tokens": sum(message.get("usage", {}).get("input_tokens", 0) for message in responses),
            "output_tokens": sum(message.get("usage", {}).get("output_tokens", 0) for message in responses),
        }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output_path)
    return report
