"""Reproducible, symbol-rate coded QAM link simulation.

The encoder is a terminated K=3 convolutional code with octal generators (7,5).
Rate 3/4 punctures the serial encoder output with [1,1,1,0,0,1]. The receiver
uses max-log soft demapping and a full-frame Viterbi decoder with known zero
initial/final states. Rayleigh fading is constant within each frame and is
known perfectly at the receiver. Synchronization is ideal.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.stats import norm


@dataclass(frozen=True)
class MCS:
    order: int
    rate: float
    puncture: tuple[int, ...]


MCS_TABLE = {
    "qpsk_r12": MCS(4, 0.5, (1, 1)),
    "qpsk_r34": MCS(4, 0.75, (1, 1, 1, 0, 0, 1)),
    "qam16_r12": MCS(16, 0.5, (1, 1)),
    "qam16_r34": MCS(16, 0.75, (1, 1, 1, 0, 0, 1)),
    "qam64_r12": MCS(64, 0.5, (1, 1)),
    "qam64_r34": MCS(64, 0.75, (1, 1, 1, 0, 0, 1)),
}
MCSName = Literal[
    "qpsk_r12", "qpsk_r34", "qam16_r12", "qam16_r34", "qam64_r12", "qam64_r34"
]


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mcs: MCSName
    channel: Literal["awgn", "rayleigh_block"] = "awgn"
    esn0_db: float = Field(ge=-30, le=60)
    frames: int = Field(default=512, ge=1, le=8192)
    seed: int = Field(default=20261113, ge=0, le=2**32 - 1)
    payload_bits: int = Field(default=192, ge=8, le=2048)
    header_bits: int = Field(default=32, ge=0, le=256)
    preamble_symbols: int = Field(default=16, ge=0, le=1024)
    symbol_rate_msps: float = Field(default=1.0, gt=0, le=1000)


def wilson_interval(errors: int, trials: int) -> tuple[float, float]:
    """Two-sided 95% Wilson score interval for independent Bernoulli trials."""
    if trials <= 0 or not 0 <= errors <= trials:
        raise ValueError("Require 0 <= errors <= trials and trials > 0.")
    z = float(norm.ppf(0.975))
    probability = errors / trials
    denominator = 1 + z * z / trials
    center = (probability + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(
        probability * (1 - probability) / trials + z * z / (4 * trials**2)
    ) / denominator
    return max(0.0, center - half), min(1.0, center + half)


@lru_cache(maxsize=3)
def constellation(order: int) -> tuple[np.ndarray, np.ndarray]:
    """Unit-average-energy square QAM with binary-reflected Gray labels."""
    if order not in (4, 16, 64):
        raise ValueError("Supported modulation orders are 4, 16, 64.")
    bits_per_symbol = int(math.log2(order))
    labels = np.arange(order)
    bits = ((labels[:, None] >> np.arange(bits_per_symbol - 1, -1, -1)) & 1)
    axis_bits = bits_per_symbol // 2
    axis_mask = (1 << axis_bits) - 1
    gray_i = labels >> axis_bits
    gray_q = labels & axis_mask
    index_i, index_q = gray_i.copy(), gray_q.copy()
    for shift in range(1, axis_bits):
        index_i ^= gray_i >> shift
        index_q ^= gray_q >> shift
    side = int(math.sqrt(order))
    points = (2 * index_i - side + 1) + 1j * (2 * index_q - side + 1)
    points = points / math.sqrt(2 * (order - 1) / 3)
    return points, bits.astype(np.uint8)


def modulate(bits: np.ndarray, order: int) -> tuple[np.ndarray, int]:
    """Map rows of bits to QAM symbols, padding the final symbol with zeros."""
    points, _ = constellation(order)
    bits_per_symbol = int(math.log2(order))
    padding = (-bits.shape[1]) % bits_per_symbol
    padded = np.pad(bits, ((0, 0), (0, padding)))
    grouped = padded.reshape(bits.shape[0], -1, bits_per_symbol)
    labels = grouped @ (1 << np.arange(bits_per_symbol - 1, -1, -1))
    return points[labels], padding


def demap_llr(received: np.ndarray, gains: np.ndarray, noise_power: float, order: int) -> np.ndarray:
    """Positive LLR favors bit zero. Likelihoods use the known channel gain."""
    points, labels = constellation(order)
    distances = np.abs(received[:, :, None] - gains[:, :, None] * points) ** 2
    llrs = []
    for bit in range(labels.shape[1]):
        zero_distance = distances[:, :, labels[:, bit] == 0].min(axis=2)
        one_distance = distances[:, :, labels[:, bit] == 1].min(axis=2)
        llrs.append((one_distance - zero_distance) / noise_power)
    return np.stack(llrs, axis=2).reshape(received.shape[0], -1)


def convolutional_encode(bits: np.ndarray) -> np.ndarray:
    """Encode rows of information bits, appending two zero termination bits."""
    terminated = np.pad(bits, ((0, 0), (0, 2)))
    state = np.zeros(bits.shape[0], dtype=np.uint8)
    encoded = np.empty((bits.shape[0], terminated.shape[1], 2), dtype=np.uint8)
    for index in range(terminated.shape[1]):
        register = (state << 1) | terminated[:, index]
        encoded[:, index, 0] = (register ^ (register >> 1) ^ (register >> 2)) & 1
        encoded[:, index, 1] = (register ^ (register >> 2)) & 1
        state = register & 3
    return encoded.reshape(bits.shape[0], -1)


def puncture_mask(length: int, pattern: tuple[int, ...]) -> np.ndarray:
    return np.resize(np.asarray(pattern, dtype=bool), length)


def viterbi_decode(llrs: np.ndarray) -> np.ndarray:
    """Soft maximum-likelihood trellis decoding; punctured positions have LLR 0."""
    observations = llrs.reshape(llrs.shape[0], -1, 2)
    frame_count, steps, _ = observations.shape
    states = np.arange(4)
    predecessors = np.stack((states >> 1, (states >> 1) | 2), axis=1)
    registers = (predecessors << 1) | (states[:, None] & 1)
    output_bits = np.stack(
        ((registers ^ (registers >> 1) ^ (registers >> 2)) & 1,
         (registers ^ (registers >> 2)) & 1), axis=2
    )
    signs = 2 * output_bits - 1
    metrics = np.full((frame_count, 4), np.inf)
    metrics[:, 0] = 0
    decisions = np.empty((steps, frame_count, 4), dtype=np.uint8)
    for index in range(steps):
        branch_cost = np.einsum("fb,spb->fsp", observations[:, index], signs)
        alternatives = metrics[:, predecessors] + branch_cost
        decisions[index] = alternatives.argmin(axis=2)
        metrics = alternatives.min(axis=2)
    decoded = np.empty((frame_count, steps), dtype=np.uint8)
    current_states = np.zeros(frame_count, dtype=np.int64)
    rows = np.arange(frame_count)
    for index in range(steps - 1, -1, -1):
        decoded[:, index] = current_states & 1
        chosen = decisions[index, rows, current_states]
        current_states = predecessors[current_states, chosen]
    return decoded[:, :-2]


def frame_layout(config: SimulationConfig) -> dict[str, int | float]:
    mcs = MCS_TABLE[config.mcs]
    information_bits = config.payload_bits + config.header_bits
    coded_bits = int(puncture_mask(2 * (information_bits + 2), mcs.puncture).sum())
    data_symbols = math.ceil(coded_bits / math.log2(mcs.order))
    total_symbols = data_symbols + config.preamble_symbols
    return {
        "information_bits": information_bits,
        "transmitted_coded_bits": coded_bits,
        "data_symbols": data_symbols,
        "total_symbols": total_symbols,
        "payload_ceiling_mbps": config.payload_bits * config.symbol_rate_msps / total_symbols,
    }


def simulate_link(config: SimulationConfig) -> dict:
    rng = np.random.default_rng(config.seed)
    mcs = MCS_TABLE[config.mcs]
    layout = frame_layout(config)
    noise_power = 10 ** (-config.esn0_db / 10)
    payload_errors = 0
    block_errors = 0
    # Bound demapper memory for 64-QAM and large frame counts.
    for start in range(0, config.frames, 128):
        count = min(128, config.frames - start)
        bits = rng.integers(0, 2, (count, layout["information_bits"]), dtype=np.uint8)
        encoded = convolutional_encode(bits)
        mask = puncture_mask(encoded.shape[1], mcs.puncture)
        symbols, _ = modulate(encoded[:, mask], mcs.order)
        gains = np.ones((count, 1), dtype=np.complex128)
        if config.channel == "rayleigh_block":
            gains = (rng.normal(size=(count, 1)) + 1j * rng.normal(size=(count, 1))) / math.sqrt(2)
        noise = math.sqrt(noise_power / 2) * (
            rng.normal(size=symbols.shape) + 1j * rng.normal(size=symbols.shape)
        )
        received = gains * symbols + noise
        transmitted_llrs = demap_llr(received, gains, noise_power, mcs.order)
        full_llrs = np.zeros(encoded.shape)
        full_llrs[:, mask] = transmitted_llrs[:, :int(mask.sum())]
        decoded = viterbi_decode(full_llrs)
        wrong = decoded != bits
        payload_errors += int(wrong[:, :config.payload_bits].sum())
        block_errors += int(np.any(wrong, axis=1).sum())
    lower, upper = wilson_interval(block_errors, config.frames)
    ceiling = layout["payload_ceiling_mbps"]
    return {
        "config": config.model_dump(),
        "frame_layout": layout,
        "payload_bit_errors": payload_errors,
        "payload_bits_tested": config.frames * config.payload_bits,
        "ber": payload_errors / (config.frames * config.payload_bits),
        "block_errors": block_errors,
        "frames": config.frames,
        "bler": block_errors / config.frames,
        "bler_ci95": [lower, upper],
        "goodput_mbps": ceiling * (1 - block_errors / config.frames),
        "goodput_ci95_mbps": [ceiling * (1 - upper), ceiling * (1 - lower)],
        "assumptions": [
            "unit average symbol energy; complex noise variance N0",
            "ideal synchronization and receiver channel knowledge",
            "independent frames; Rayleigh gain constant within a frame",
            "frame failure detected by comparison with transmitted information bits",
            "fixed preamble time overhead; single transmission per frame",
        ],
    }
