"""Streamlit UI for live investigations and reproducible evaluation results."""

import asyncio
import json
from datetime import datetime

import pandas as pd
import streamlit as st

from src.agent import run_investigation
from src.cases import get_case, load_cases
from src.paths import PROJECT_ROOT, WORK_ROOT
from src.ui_inputs import prepare_ui_case

st.set_page_config(page_title="无线链路调查", page_icon="📡", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp {font-family: Inter, 'Microsoft YaHei', sans-serif;}
.stApp {background:#ffffff;color:#000000;}
h1,h2,h3,h4,p,label,small,summary {color:#000000 !important;}
div[data-testid="stCaptionContainer"], div[data-testid="stMetricLabel"], div[data-testid="stMetricValue"] {color:#000000 !important;}
div[data-testid="stMetric"] {background:white;border:1px solid #cccccc;border-radius:12px;padding:16px;}
.block-container {padding-top:2.3rem;max-width:1400px;}
div[data-testid="stSidebar"] {background:#f5f5f5;}
code, pre, a, [data-testid="stJson"] * {color:#000000 !important;}
[data-testid="stJson"] .object-key {opacity:1 !important;}
button[kind="primary"] p {color:#ffffff !important;}
</style>
""", unsafe_allow_html=True)

cases = load_cases()
titles = {case["id"]: f"{case['id']} · {case['title']}" for case in cases}
with st.sidebar:
    st.header("调查设置")
    case_id = st.selectbox("工单", list(titles), index=1, format_func=titles.get)
    group = st.selectbox("系统配置", ["C", "B", "A"], format_func=lambda item: {
        "A": "A · 直接 LLM", "B": "B · Agent + Tools", "C": "C · Agent + Tools + Skill"
    }[item])
    st.caption("模型 deepseek-flash")
    st.caption("六种调制编码配置 · AWGN / 瑞利块衰落")
    st.divider()
    st.caption("每次在线调查会产生 API 调用。完整记录保存在独立开发证据目录。")

st.title("无线链路调制与编码调查")
st.write("结合通信计算、受控仿真和专业 Skill，检查性能退化原因并比较配置。")
investigation_tab, evaluation_tab = st.tabs(["调查工作台", "对照评测"])

with investigation_tab:
    original_case = get_case(case_id)
    left, right = st.columns([1.5, 1])
    with left:
        question = st.text_area("调查问题", value=original_case["incident"], height=135, key=f"incident_{case_id}",
                                help="仿真参数由右侧链路条件设置，问题文本用于说明调查目标。")
    with right:
        st.markdown("#### 链路条件")
        mcs_names = {"qpsk_r12": "QPSK 1/2", "qpsk_r34": "QPSK 3/4",
                     "qam16_r12": "16-QAM 1/2", "qam16_r34": "16-QAM 3/4",
                     "qam64_r12": "64-QAM 1/2", "qam64_r34": "64-QAM 3/4"}
        channels = {"awgn": "AWGN", "rayleigh_block": "瑞利块衰落", "unknown": "待确认"}
        metrics = {"esn0_db": "Es/N0", "ebn0_information_db": "信息比特 Eb/N0",
                   "snr_db": "带宽内 SNR", "unknown": "待确认"}
        mcs = st.selectbox("当前调制与编码", list(mcs_names),
                           index=list(mcs_names).index(original_case["current_mcs"]),
                           format_func=mcs_names.get, key=f"mcs_{case_id}")
        channel = st.selectbox("信道类型", list(channels),
                               index=list(channels).index(original_case["channel"] or "unknown"),
                               format_func=channels.get, key=f"channel_{case_id}")
        metric_kind = st.selectbox("指标定义", list(metrics),
                                   index=list(metrics).index(original_case["metric_kind"] or "unknown"),
                                   format_func=metrics.get, key=f"metric_kind_{case_id}")
        metric_value = st.number_input("指标数值（dB）", value=float(original_case["metric_value_db"]), step=0.5,
                                       key=f"metric_value_{case_id}")
        bler_percent = st.number_input("BLER 上限（%）", min_value=0.0, max_value=100.0,
                                       value=original_case["bler_limit"] * 100, step=1.0,
                                       key=f"bler_{case_id}")
    conditions = {"current_mcs": mcs, "channel": None if channel == "unknown" else channel,
                  "metric_kind": None if metric_kind == "unknown" else metric_kind,
                  "metric_value_db": metric_value, "bler_limit": bler_percent / 100}
    input_error = None
    try:
        case = prepare_ui_case(original_case, question, conditions)
    except ValueError as error:
        input_error = str(error)
        st.error(input_error)
        case = None
    if case is not None and "customization" in case:
        if case["customization"]["observations_reused"]:
            st.caption("本次采用自定义业务约束，沿用原工单的链路观测。")
        else:
            st.caption("本次为自定义条件调查，证据将通过新仿真建立。原工单观测可在下方查看。")
    with st.expander("查看本次调查数据"):
        st.json(case or {**original_case, **conditions, "incident": question})
    with st.expander("查看完整原始数据"):
        st.json(original_case)
    if st.button("开始调查", type="primary", use_container_width=True, disabled=input_error is not None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = WORK_ROOT / "evidence" / "ui_runs" / f"{stamp}_{case['id']}_{group}.json"
        with st.status("正在调用模型与通信工具…", expanded=True) as status:
            st.write("读取工单，执行计算与仿真，整理证据和配置建议。")
            report = asyncio.run(run_investigation(case, group, output_path=path))
            st.session_state["report"] = report
            status.update(label="调查完成" if report["status"] == "completed" else "调查遇到错误",
                          state="complete" if report["status"] == "completed" else "error", expanded=False)

    report_matches_input = (
        "report" in st.session_state
        and st.session_state["report"]["case"] == case
        and st.session_state["report"]["group"] == group
    )
    if "report" in st.session_state and not report_matches_input:
        st.caption("输入已更新，点击“开始调查”生成对应结果。")
    if report_matches_input:
        report = st.session_state["report"]
        st.subheader(f"{report['case_id']} 调查结果")
        if report["status"] == "failed":
            st.error(f"{report['error_type']} · {report['error']}")
        else:
            answer = report["output"]
            decision_labels = {"recommend": "推荐配置", "no_feasible_option": "无可行配置", "insufficient_evidence": "证据不足"}
            first, second, third, fourth = st.columns(4)
            first.metric("调查判断", decision_labels[answer["decision"]])
            second.metric("推荐配置", answer["recommended_mcs"] or "待确定")
            third.metric("实际仿真帧数", f"{report['frames_used']:,}")
            fourth.metric("运行时间", f"{report['elapsed_seconds']:.1f} s")
            st.markdown("#### 结论与依据")
            st.write(answer["summary"])
            simulations = [item for item in report["evidence"].values() if item.get("config")]
            if simulations:
                rows = [{"证据": item["evidence_id"], "配置": item["config"]["mcs"],
                         "信道": item["config"]["channel"], "Es/N0 (dB)": item["config"]["esn0_db"],
                         "帧数": item["frames"], "BLER": item["bler"],
                         "BLER 95%上界": item["bler_ci95"][1], "有效吞吐 Mbps": item["goodput_mbps"]}
                        for item in simulations]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.markdown("#### 假设、支持证据与反证")
            for index, hypothesis in enumerate(answer["hypotheses"], 1):
                with st.expander(f"{index}. {hypothesis['explanation']}", expanded=index == 1):
                    st.write({"supported": "获得支持", "refuted": "受到反证", "undetermined": "尚待判断"}[hypothesis["status"]])
                    st.write("支持证据", ", ".join(hypothesis["supporting_evidence"]) or "待补充")
                    st.write("反面或限制性证据", ", ".join(hypothesis["opposing_evidence"]) or "当前记录为空")
            st.markdown("#### 工具调用与证据")
            for identifier, evidence in report["evidence"].items():
                with st.expander(f"{identifier} · {evidence.get('config', {}).get('mcs', evidence.get('status', '证据'))}"):
                    st.json(evidence)
            st.markdown("#### 不确定性与后续行动")
            st.write(answer["uncertainty"])
            for action in answer["next_actions"]:
                st.write("• " + action)
            st.download_button("下载本次调查记录", json.dumps(report, ensure_ascii=False, indent=2),
                               file_name=f"{report['case_id']}_{report['group']}_trace.json", mime="application/json")

with evaluation_tab:
    summary_path = PROJECT_ROOT / "results" / "summary.csv"
    if summary_path.exists():
        summary = pd.read_csv(summary_path)
        st.subheader("同一模型的三组对照")
        st.dataframe(summary[["group", "planned_runs", "successes", "task_success_rate", "mean_seconds", "mean_requests"]],
                     hide_index=True, use_container_width=True)
        image = PROJECT_ROOT / "results" / "comparison.png"
        if image.exists():
            st.image(str(image), use_container_width=True)
        st.caption("每个配置对八个案例重复三次。任务成功按独立参考仿真和预先定义的规则判定。")
    else:
        st.info("正式评测汇总完成后将在此显示。")
