"""Observable tools, with per-investigation simulation accounting."""

import math
import threading
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext

from .simulation import MCS_TABLE, MCSName, SimulationConfig, frame_layout, simulate_link, wilson_interval
from .tools import calculate_shannon_capacity


@dataclass
class InvestigationContext:
    case: dict
    seed: int = 20261113
    frame_budget: int = 16384
    frames_used: int = 0
    simulation_counts: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, prefix: str, result: dict) -> dict:
        with self.lock:
            identifier = f"{prefix}.{len(self.evidence) + 1}"
            result = {"evidence_id": identifier, **result}
            self.evidence[identifier] = result
        return result


def normalize_esn0(case: dict) -> float:
    value = case["metric_value_db"]
    kind = case["metric_kind"]
    if value is None or kind is None:
        raise ValueError("Signal metric value and definition are required.")
    if kind == "esn0_db":
        return float(value)
    if kind == "ebn0_information_db":
        reference = MCS_TABLE[case["current_mcs"]]
        return value + 10 * math.log10(math.log2(reference.order) * reference.rate)
    if kind == "snr_db":
        return value + 10 * math.log10(case["bandwidth_mhz"] / case["symbol_rate_msps"])
    raise ValueError("Unsupported signal metric definition.")


def config_for_case(case: dict, mcs: str, frames: int, seed: int) -> SimulationConfig:
    return SimulationConfig(
        mcs=mcs, channel=case["channel"], esn0_db=normalize_esn0(case),
        frames=frames, seed=seed, payload_bits=case["payload_bits"],
        header_bits=case["header_bits"], preamble_symbols=case["preamble_symbols"],
        symbol_rate_msps=case["symbol_rate_msps"],
    )


def get_link_case(ctx: RunContext[InvestigationContext]) -> dict:
    """Read the current case's public configuration, measurements, and requirements."""
    return ctx.deps.record("obs", {"status": "ok", "case": ctx.deps.case})


def calculate_link_limits(ctx: RunContext[InvestigationContext]) -> dict:
    """Normalize the supplied metric, calculate ideal capacity and six frame-rate ceilings.

    Also computes the Wilson interval for any provided pilot frame error count.
    Missing metric definitions are returned as explicit errors.
    """
    case = ctx.deps.case
    try:
        esn0_db = normalize_esn0(case)
    except ValueError as error:
        return ctx.deps.record("calc", {"status": "error", "message": str(error)})
    wideband_snr_db = esn0_db + 10 * math.log10(case["symbol_rate_msps"] / case["bandwidth_mhz"])
    capacity = calculate_shannon_capacity(case["bandwidth_mhz"], wideband_snr_db)
    ceilings = {
        mcs: frame_layout(SimulationConfig(
            mcs=mcs, esn0_db=esn0_db, payload_bits=case["payload_bits"],
            header_bits=case["header_bits"], preamble_symbols=case["preamble_symbols"],
            symbol_rate_msps=case["symbol_rate_msps"],
        ))["payload_ceiling_mbps"]
        for mcs in case["candidate_mcs"]
    }
    pilot = [
        {"observation_id": observation["id"],
         "bler_ci95": wilson_interval(observation["block_errors"], observation["frames"])}
        for observation in case["observations"] if "block_errors" in observation
    ]
    return ctx.deps.record("calc", {
        "status": "ok", "esn0_db": esn0_db, "wideband_snr_db": wideband_snr_db,
        "awgn_capacity_mbps": capacity["capacity_mbps"],
        "capacity_assumption": capacity["assumption"],
        "payload_ceilings_mbps": ceilings, "pilot_intervals": pilot,
        "power_comparison": "Keep this Es/N0 fixed across MCS at equal symbol rate and average power.",
    })


def simulate_mcs(
    ctx: RunContext[InvestigationContext],
    mcs: MCSName,
    esn0_db: Annotated[float, Field(ge=-30, le=60, allow_inf_nan=False)],
    channel: Literal["awgn", "rayleigh_block"],
    frames: Annotated[int, Field(ge=64, le=4096)] = 512,
) -> dict:
    """Run the actual coded link simulation and return errors, intervals, and goodput.

    Args:
        mcs: Candidate modulation and nominal code rate.
        esn0_db: Symbol energy / noise spectral density in dB under the tested hypothesis.
        channel: AWGN or independent, per-frame Rayleigh fading with perfect receiver CSI.
        frames: Number of fresh frames, from 64 through 4096.
    """
    state = ctx.deps
    with state.lock:
        if state.frames_used + frames > state.frame_budget:
            return {"status": "error", "message": "Simulation frame budget exhausted.",
                    "remaining_frames": state.frame_budget - state.frames_used}
        count = state.simulation_counts.get(mcs, 0)
        seed = (state.seed + count * 104729 + list(MCS_TABLE).index(mcs) * 997) % (2**32)
        state.simulation_counts[mcs] = count + 1
        state.frames_used += frames
    case = state.case
    result = simulate_link(SimulationConfig(
        mcs=mcs, esn0_db=esn0_db, channel=channel, frames=frames, seed=seed,
        payload_bits=case["payload_bits"], header_bits=case["header_bits"],
        preamble_symbols=case["preamble_symbols"], symbol_rate_msps=case["symbol_rate_msps"],
    ))
    return state.record("sim", {"status": "ok", **result})


def compare_mcs_results(
    ctx: RunContext[InvestigationContext], simulation_ids: list[str]
) -> dict:
    """Compare existing simulation evidence under matching channel and frame conditions.

    Use one independent result per candidate. Feasibility uses the upper 95%
    BLER interval and the lower goodput interval. Different sample sizes are
    allowed. The result reports unsampled candidates and statistically close rankings.
    """
    state = ctx.deps
    with state.lock:
        results = [state.evidence.get(identifier) for identifier in simulation_ids]
    if not results or any(result is None or "config" not in result for result in results):
        return state.record("cmp", {"status": "error", "message": "Supply existing simulation evidence IDs."})
    signatures = {
        tuple((key, value) for key, value in result["config"].items() if key not in ("mcs", "seed", "frames"))
        for result in results
    }
    names = [result["config"]["mcs"] for result in results]
    if len(signatures) != 1 or len(set(names)) != len(names):
        return state.record("cmp", {"status": "error", "message": "Use matching conditions and one result per MCS."})
    limit = state.case["bler_limit"]
    minimum = state.case["min_goodput_mbps"]
    ranked = sorted(results, key=lambda result: result["goodput_mbps"], reverse=True)
    feasible = [result for result in ranked if result["bler_ci95"][1] <= limit and result["goodput_ci95_mbps"][0] >= minimum]
    rejected = [result for result in ranked if result["bler_ci95"][0] > limit or result["goodput_ci95_mbps"][1] < minimum]
    unsampled = sorted(set(state.case["candidate_mcs"]) - set(names))
    decision = "insufficient_evidence"
    recommendation = None
    close = []
    if feasible:
        decision = "recommend"
        recommendation = feasible[0]["config"]["mcs"]
        close = [result["config"]["mcs"] for result in feasible[1:]
                 if result["goodput_ci95_mbps"][1] >= feasible[0]["goodput_ci95_mbps"][0]]
    elif len(rejected) == len(results) and not unsampled:
        decision = "no_feasible_option"
    return state.record("cmp", {
        "status": "ok", "decision": decision, "recommended_mcs": recommendation,
        "feasible_mcs": [result["config"]["mcs"] for result in feasible],
        "unsampled_mcs": unsampled, "statistically_close_mcs": close,
        "simulation_ids": simulation_ids,
        "ranking": [{"mcs": result["config"]["mcs"], "goodput_mbps": result["goodput_mbps"],
                     "bler": result["bler"], "bler_ci95": result["bler_ci95"]} for result in ranked],
        "scope": "Ranking applies to the supplied simulation conditions and sampled candidates.",
    })
