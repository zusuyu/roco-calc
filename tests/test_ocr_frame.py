"""下游结构的关键边界，不依赖 OCR 模型或用户截图。"""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_frame import (parse_hp, pet_result, resolve_frame_region, select_number,
                       skill_identity_candidates, skill_result, validate_layout)


class TestStructuredFrame(unittest.TestCase):
    def test_relaxed_energy_accepts_single_high_confidence_ten(self):
        reading = [{"text": "10", "confidence": 0.8158}]
        self.assertIsNone(select_number(reading))
        self.assertEqual(select_number(reading, min_single=0.75), 10)

    def test_database_energy_supports_low_confidence_digit(self):
        reading = {
            "text": "Z",
            "confidence": 0.62,
            "retries": [{"text": "2", "confidence": 0.55}],
        }
        result = skill_result(
            1,
            [{"name": "花炮", "category": "状态", "status": "exact"}],
            None,
            None,
            reading,
        )
        self.assertEqual(result["energy_cost"], 2)
        self.assertEqual(result["energy_cost_status"], "database_supported")
        self.assertEqual(result["energy_cost_estimate"], 2)

    def test_database_energy_estimate_is_not_claimed_as_read(self):
        result = skill_result(
            1,
            [{"name": "花炮", "category": "状态", "status": "exact"}],
            None,
            None,
            {"text": "Z", "confidence": 0.62, "retries": []},
        )
        self.assertIsNone(result["energy_cost"])
        self.assertEqual(result["energy_cost_status"], "database_estimate")
        self.assertEqual(result["energy_cost_estimate"], 2)

    def test_skill_set_candidates_identify_fire_bug_family(self):
        candidates = skill_identity_candidates(
            ("引燃", "藤绞", "充分燃烧", "火焰护盾")
        )
        names = {item["name"] for item in candidates}
        self.assertEqual(names, {"柴渣虫", "燃薪虫"})

    def test_zero_energy_is_valid_and_ambiguous_numbers_are_rejected(self):
        self.assertEqual(select_number([
            {"text": "D", "confidence": 0.99},
            {"text": "0", "confidence": 0.7},
            {"text": "0", "confidence": 0.6},
        ]), 0)
        self.assertIsNone(select_number([
            {"text": "3", "confidence": 0.95},
            {"text": "8", "confidence": 0.94},
        ]))
        self.assertIsNone(select_number([
            {"text": "星7", "confidence": 0.99},
        ]))

    def test_nickname_does_not_become_database_identity(self):
        pet = pet_result("钥匙扣", 9)
        self.assertIsNone(pet["name"])
        self.assertEqual(pet["display_name"], "钥匙扣")
        self.assertEqual(pet["energy"], 9)
        pet = pet_result("玩家昵称", 0, {"name": "迪莫", "number": "001", "form": ""})
        self.assertEqual(pet["name"], "迪莫")
        self.assertEqual(pet["energy"], 0)

    def test_displayed_power_is_never_filled_from_base_power(self):
        reading = [{"name": "倾泻", "category": "攻击", "base_power": 70,
                    "status": "exact"}]
        result = skill_result(1, reading, 58, 3)
        self.assertEqual((result["power"], result["base_power"]), (58, 70))
        missing = skill_result(1, reading, None, 3)
        self.assertIsNone(missing["power"])
        self.assertEqual(missing["power_status"], "unresolved")

    def test_non_attack_power_is_null_and_missing_slot_is_preserved(self):
        defense = skill_result(2, [{"name": "能量守恒", "category": "防御",
                                   "status": "exact"}], 8, 2)
        self.assertIsNone(defense["power"])
        self.assertEqual(defense["power_status"], "not_applicable")
        missing = skill_result(3, [], 123, None)
        self.assertEqual(missing["slot"], 3)
        self.assertIsNone(missing["name"])
        self.assertIsNone(missing["power"])

    def test_layout_must_match_resolution_and_have_four_unique_slots(self):
        path = Path(__file__).resolve().parents[1] / "configs" / "ocr_reference4.json"
        layout = json.loads(path.read_text())
        validate_layout(layout, (2094, 1179))
        with self.assertRaises(ValueError):
            validate_layout(layout, (1608, 944))
        duplicate = copy.deepcopy(layout)
        duplicate["skills"][1]["slot"] = 1
        with self.assertRaises(ValueError):
            validate_layout(duplicate, (2094, 1179))

    def test_desktop_viewport_and_content_image_share_one_layout(self):
        path = Path(__file__).resolve().parents[1] / "configs" / "ocr_1600x900_on_1920x1080.json"
        layout = json.loads(path.read_text())
        self.assertEqual(resolve_frame_region(layout, (1920, 1080)),
                         [160, 66, 1760, 966])
        self.assertEqual(resolve_frame_region(layout, (1600, 900)), [0, 0, 1600, 900])
        validate_layout(layout, (1920, 1080))
        validate_layout(layout, (1600, 900))

    def test_wegame_1602_by_932_window_is_cropped_to_content(self):
        path = Path(__file__).resolve().parents[1] / "configs" / "ocr_1602x932_window.json"
        layout = json.loads(path.read_text())
        self.assertEqual(
            resolve_frame_region(layout, (1602, 932)),
            [1, 32, 1601, 932],
        )
        validate_layout(layout, (1602, 932))
        validate_layout(layout, (1600, 900))

    def test_title_bar_or_out_of_screen_viewport_is_rejected(self):
        layout = {"image_size": [1600, 900], "screen_size": [1920, 1080]}
        for box in ([160, 36, 1760, 966], [-1, 66, 1599, 966],
                    [400, 66, 2000, 966]):
            with self.subTest(viewport=box):
                with self.assertRaises(ValueError):
                    resolve_frame_region({**layout, "viewport": box}, (1920, 1080))

    def test_hp_values_must_have_separator_and_valid_bounds(self):
        self.assertEqual(parse_hp("403 / 403"), (403, 403))
        self.assertEqual(parse_hp("0/403"), (0, 403))
        for text in (None, "403403", "404/403", "0/0", "403/403血量"):
            with self.subTest(text=text):
                self.assertEqual(parse_hp(text), (None, None))


if __name__ == "__main__":
    unittest.main()
