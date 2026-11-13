"""Checks for units, comparison consistency, and concurrent simulation budgets."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from src.cases import get_case
from src.investigation_tools import (
    InvestigationContext, calculate_link_limits, compare_mcs_results, normalize_esn0, simulate_mcs,
)
from src.simulation import MCS_TABLE


class InvestigationToolTests(unittest.TestCase):
    def test_reference_ebn0_conversion(self):
        self.assertAlmostEqual(normalize_esn0(get_case("C06")), 10.532125137753438)

    def test_missing_definition_returns_explicit_error(self):
        ctx = SimpleNamespace(deps=InvestigationContext(get_case("C07")))
        self.assertEqual(calculate_link_limits(ctx)["status"], "error")

    def test_comparison_rejects_mixed_conditions(self):
        ctx = SimpleNamespace(deps=InvestigationContext(get_case("C02")))
        first = simulate_mcs(ctx, "qpsk_r12", 8, "awgn", 64)
        second = simulate_mcs(ctx, "qpsk_r34", 12, "awgn", 64)
        comparison = compare_mcs_results(ctx, [first["evidence_id"], second["evidence_id"]])
        self.assertEqual(comparison["status"], "error")

    def test_concurrent_frame_budget(self):
        state = InvestigationContext(get_case("C02"), frame_budget=128)
        ctx = SimpleNamespace(deps=state)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda name: simulate_mcs(ctx, name, 8, "awgn", 64), list(MCS_TABLE)[:3]))
        self.assertEqual(state.frames_used, 128)
        self.assertEqual(sum(result["status"] == "ok" for result in results), 2)

    def test_punctured_code_has_no_nonzero_zero_output_cycle(self):
        pattern = MCS_TABLE["qpsk_r34"].puncture
        cycles = []

        def walk(node, path, inputs):
            if node in path:
                start = path.index(node)
                if any(inputs[start:]):
                    cycles.append(path[start:])
                return
            state, phase = node
            for bit in (0, 1):
                register = (state << 1) | bit
                output = ((register ^ (register >> 1) ^ (register >> 2)) & 1,
                          (register ^ (register >> 2)) & 1)
                keep = pattern[2 * phase:2 * phase + 2]
                if sum(value * mask for value, mask in zip(output, keep)) == 0:
                    walk((register & 3, (phase + 1) % 3), path + [node], inputs + [bit])

        for state in range(4):
            for phase in range(3):
                walk((state, phase), [], [])
        self.assertEqual(cycles, [])


if __name__ == "__main__":
    unittest.main()
