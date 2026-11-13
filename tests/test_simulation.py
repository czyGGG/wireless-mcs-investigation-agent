"""Independent checks for communications primitives and simulation boundaries."""

import itertools
import math
import unittest

import numpy as np
from pydantic import ValidationError
from scipy.special import erfc

from src.simulation import (
    MCS_TABLE, SimulationConfig, constellation, convolutional_encode, demap_llr,
    frame_layout, modulate, puncture_mask, simulate_link, viterbi_decode, wilson_interval,
)
from src.tools import calculate_shannon_capacity


class CommunicationTests(unittest.TestCase):
    def test_constellation_energy_and_gray_neighbors(self):
        for order in (4, 16, 64):
            points, labels = constellation(order)
            self.assertAlmostEqual(float(np.mean(np.abs(points) ** 2)), 1)
            distances = np.abs(points[:, None] - points[None, :])
            nearest = np.min(distances[distances > 0])
            for left, right in np.argwhere(np.isclose(distances, nearest)):
                self.assertEqual(int(np.sum(labels[left] != labels[right])), 1)

    def test_encoder_known_vector(self):
        # Manual shift-register calculation for input 101 and two tail zeros.
        encoded = convolutional_encode(np.array([[1, 0, 1]], dtype=np.uint8))
        np.testing.assert_array_equal(encoded, [[1, 1, 1, 0, 0, 0, 1, 0, 1, 1]])

    def test_noiseless_all_six_mcs(self):
        rng = np.random.default_rng(42)
        bits = rng.integers(0, 2, (9, 37), dtype=np.uint8)
        encoded = convolutional_encode(bits)
        for mcs in MCS_TABLE.values():
            mask = puncture_mask(encoded.shape[1], mcs.puncture)
            symbols, _ = modulate(encoded[:, mask], mcs.order)
            llrs = demap_llr(symbols, np.ones((9, 1)), 0.01, mcs.order)
            restored = np.zeros(encoded.shape)
            restored[:, mask] = llrs[:, :int(mask.sum())]
            np.testing.assert_array_equal(viterbi_decode(restored), bits)

    def test_viterbi_matches_exhaustive_likelihood(self):
        candidates = np.array(list(itertools.product((0, 1), repeat=5)), dtype=np.uint8)
        codewords = convolutional_encode(candidates).astype(float)
        llrs = np.random.default_rng(17).normal(size=(6, codewords.shape[1]))
        costs = llrs @ (2 * codewords - 1).T
        expected = candidates[costs.argmin(axis=1)]
        np.testing.assert_array_equal(viterbi_decode(llrs), expected)

    def test_uncoded_qpsk_matches_theory(self):
        rng = np.random.default_rng(91)
        bits = rng.integers(0, 2, (1000, 400), dtype=np.uint8)
        symbols, _ = modulate(bits, 4)
        esn0 = 10 ** (4 / 10)
        noise = (rng.normal(size=symbols.shape) + 1j * rng.normal(size=symbols.shape)) / math.sqrt(2 * esn0)
        decoded = demap_llr(symbols + noise, np.ones((1000, 1)), 1 / esn0, 4) < 0
        observed = np.mean(decoded != bits)
        theory = 0.5 * erfc(math.sqrt(esn0 / 2))
        self.assertLess(abs(observed - theory), 0.0015)

    def test_zero_errors_still_has_positive_uncertainty(self):
        low, high = wilson_interval(0, 8)
        self.assertAlmostEqual(low, 0)
        self.assertGreater(high, 0.3)
        self.assertLess(wilson_interval(0, 512)[1], 0.01)

    def test_repeatability_and_goodput_identity(self):
        config = SimulationConfig(mcs="qam16_r34", esn0_db=10, frames=64)
        first = simulate_link(config)
        self.assertEqual(first, simulate_link(config))
        expected = frame_layout(config)["payload_ceiling_mbps"] * (1 - first["bler"])
        self.assertAlmostEqual(first["goodput_mbps"], expected)

    def test_high_snr_recovery(self):
        for mcs in MCS_TABLE:
            result = simulate_link(SimulationConfig(mcs=mcs, esn0_db=40, frames=32))
            self.assertEqual(result["block_errors"], 0)

    def test_input_validation(self):
        for invalid in ({"frames": 0}, {"esn0_db": float("nan")}, {"mcs": "unknown"}):
            params = {"mcs": "qpsk_r12", "esn0_db": 10, **invalid}
            with self.assertRaises(ValidationError):
                SimulationConfig(**params)
        with self.assertRaises(ValidationError):
            calculate_shannon_capacity(0, 10)
        self.assertAlmostEqual(calculate_shannon_capacity(10, 10)["capacity_mbps"], 34.59431618637297)


if __name__ == "__main__":
    unittest.main()
