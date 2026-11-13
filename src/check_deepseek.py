"""Exercise DeepSeek, local tool execution, and validated structured output."""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from importlib.metadata import version

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, ModelAPIError
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.usage import UsageLimits

from .model import create_model, load_config
from .paths import WORK_ROOT
from .tools import calculate_shannon_capacity


class CapacityAnswer(BaseModel):
    capacity_mbps: float = Field(gt=0, allow_inf_nan=False)
    explanation: str = Field(min_length=1)


def run_check() -> dict:
    config = load_config()
    agent = Agent(
        create_model(config),
        output_type=CapacityAnswer,
        instructions=(
            "你正在验证无线通信调查 Agent 的工具接口。"
            "必须调用 calculate_shannon_capacity 获取计算结果，并根据实际返回值回答。"
            "explanation 用一句中文说明数值及其理想 AWGN 信道容量上界的含义。"
        ),
        tools=[calculate_shannon_capacity],
        model_settings=OpenAIChatModelSettings(
            thinking=config.thinking,
            temperature=0,
            max_tokens=1200,
            timeout=45,
            max_retries=1,
        ),
        retries=1,
    )
    result = agent.run_sync(
        "信道带宽为 10 MHz，带宽内接收 SNR 为 10 dB，请调用工具计算香农容量。",
        usage_limits=UsageLimits(request_limit=4, tool_calls_limit=4),
    )
    parts = [part for message in result.all_messages() for part in message.parts]
    tool_calls = [
        part for part in parts
        if isinstance(part, ToolCallPart)
        and part.tool_name == "calculate_shannon_capacity"
    ]
    tool_results = [
        part for part in parts
        if isinstance(part, ToolReturnPart)
        and part.tool_name == "calculate_shannon_capacity"
        and part.outcome == "success"
    ]
    expected_capacity_mbps = 34.59431618637297
    matching_calls = [
        part for part in tool_calls
        if part.args_as_dict() == {"bandwidth_mhz": 10.0, "snr_db": 10.0}
    ]
    matching_results = [
        part for part in tool_results
        if isinstance(part.content, dict)
        and any(call.tool_call_id == part.tool_call_id for call in matching_calls)
        and math.isclose(
            part.content["capacity_mbps"], expected_capacity_mbps, rel_tol=1e-12
        )
    ]
    checks = {
        "correct_tool_arguments": bool(matching_calls),
        "actual_tool_result": bool(matching_results),
        "correct_structured_answer": math.isclose(
            result.output.capacity_mbps, expected_capacity_mbps, rel_tol=1e-5
        ),
    }
    usage = result.usage
    report = {
        "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "status": "passed" if all(checks.values()) else "failed",
        "model": config.model_name,
        "thinking": config.thinking,
        "python": sys.version.split()[0],
        "pydantic_ai": version("pydantic-ai-slim"),
        "checks": checks,
        "answer": result.output.model_dump(),
        "usage": {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        },
        "messages": json.loads(result.all_messages_json()),
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = WORK_ROOT / "evidence" / "connection_checks" / f"{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    try:
        report = run_check()
    except ModelAPIError as error:
        print(f"DeepSeek 请求失败，错误类型 {type(error).__name__}。")
        print("请检查本地 API 配置、账户状态和网络连接。")
        return 1
    except (AgentRunError, ValueError, OSError) as error:
        print(f"接入检查失败，错误类型 {type(error).__name__}。")
        return 1
    summary = {name: value for name, value in report.items() if name != "messages"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
