"""Regression checks for editable conditions and historical observations."""

import unittest

from src.cases import get_case
from src.ui_inputs import prepare_ui_case


class EditableConditionTests(unittest.TestCase):
    def test_original_case_is_preserved_without_parameter_changes(self):
        for number in range(1, 9):
            case = get_case(f"C{number:02d}")
            self.assertEqual(prepare_ui_case(case, case["incident"], {}), case)

    def test_custom_conditions_do_not_reuse_old_pilot_observations(self):
        original = get_case("C08")
        case = prepare_ui_case(original, original["incident"], {"metric_value_db": 8.0})
        self.assertEqual(case["id"], "C08_custom")
        self.assertEqual(case["metric_value_db"], 8.0)
        self.assertFalse(any("block_errors" in item for item in case["observations"]))
        self.assertEqual(original, get_case("C08"))

    def test_custom_ebn0_uses_selected_current_mcs(self):
        original = get_case("C06")
        case = prepare_ui_case(original, "比较候选配置", {"current_mcs": "qpsk_r12", "metric_value_db": 60.0})
        self.assertEqual(case["metric_value_db"], 60.0)
        with self.assertRaisesRegex(ValueError, "Es/N0"):
            prepare_ui_case(original, "比较候选配置", {"metric_value_db": 60.0})

    def test_business_constraint_change_keeps_valid_measurements(self):
        original = get_case("C08")
        case = prepare_ui_case(original, original["incident"], {"bler_limit": 0.15})
        self.assertEqual(case["observations"], original["observations"])
        self.assertTrue(case["customization"]["observations_reused"])

    def test_restoring_conditions_restores_original_case(self):
        original = get_case("C04")
        custom = prepare_ui_case(original, original["incident"], {"channel": "awgn"})
        self.assertEqual(custom["channel"], "awgn")
        restored = prepare_ui_case(original, original["incident"], {"channel": original["channel"]})
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
