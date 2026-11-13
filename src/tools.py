"""Deterministic communication calculations exposed to the agent."""

import math
from typing import Annotated

from pydantic import Field, validate_call


@validate_call
def calculate_shannon_capacity(
    bandwidth_mhz: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    snr_db: Annotated[float, Field(allow_inf_nan=False)],
) -> dict[str, float | str]:
    """Calculate the ideal AWGN Shannon capacity upper bound.

    Args:
        bandwidth_mhz: Channel bandwidth in MHz, strictly positive.
        snr_db: Received signal-to-noise power ratio over that bandwidth in dB.
            This parameter uses SNR, with any Eb/N0 conversion done beforehand.

    Returns:
        The inputs, capacity in Mbps, and applicable channel assumption.
        Practical modulation, coding, finite frames, and overhead require
        separate performance evaluation.
    """
    log_snr = (snr_db / 10.0) * math.log(10.0)
    # Stable log(1 + exp(x)) also supports large positive and negative SNR.
    spectral_efficiency = (
        max(log_snr, 0.0) + math.log1p(math.exp(-abs(log_snr)))
    ) / math.log(2.0)
    capacity_mbps = bandwidth_mhz * spectral_efficiency
    if not math.isfinite(capacity_mbps):
        raise ValueError("Capacity exceeds the supported floating-point range.")
    return {
        "bandwidth_mhz": bandwidth_mhz,
        "snr_db": snr_db,
        "capacity_mbps": capacity_mbps,
        "assumption": "Ideal band-limited AWGN channel with average power constraint",
    }
