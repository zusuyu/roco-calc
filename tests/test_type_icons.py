"""属性图标识别：位置、判定逻辑用纯 Python 测；像素比对用 OCR 环境测。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import type_icons

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError:  # 主计算器只用标准库；没有 OCR 环境时跳过像素测试
    cv2 = np = Image = None


class TestGeometry(unittest.TestCase):
    def test_primary_is_below_and_secondary_is_above(self):
        centers = type_icons.slot_centers([34, 16, 111, 88])
        self.assertEqual(centers["lower"], (44, 71))
        self.assertEqual(centers["upper"], (34, 52))
        self.assertGreater(centers["lower"][1], centers["upper"][1])
        self.assertLess(centers["lower"][0], centers["upper"][0] + 20)

    def test_search_region_stays_inside_the_frame(self):
        self.assertEqual(type_icons.search_region([34, 16, 111, 88], (1600, 900)),
                         [11, 30, 80, 104])
        self.assertEqual(type_icons.search_region([1337, 21, 1396, 83], (1600, 900)),
                         [1317, 33, 1377, 97])
        self.assertEqual(type_icons.search_region([0, 0, 59, 62], (1600, 900))[0], 0)

    def test_radius_range_follows_avatar_height(self):
        self.assertIn(10, type_icons.radii(72))
        self.assertIn(10, type_icons.radii(62))
        self.assertLessEqual(min(type_icons.radii(30)), 6)

    def test_decision_needs_low_score_and_clear_gap(self):
        number, best, gap, detected = type_icons.decide({4: 500.0, 3: 1800.0})
        self.assertEqual((number, best, gap, detected), (4, 500.0, 1300.0, True))
        self.assertFalse(type_icons.decide({4: 500.0, 3: 650.0})[3])
        self.assertFalse(type_icons.decide({4: 5000.0})[3])
        self.assertFalse(type_icons.decide({})[3])


@unittest.skipIf(cv2 is None, "需要 OCR 环境里的 numpy/opencv")
class TestPixelMatching(unittest.TestCase):
    def icon(self, colour, glyph):
        size = 56
        image = np.zeros((size, size, 4), np.uint8)
        cv2.circle(image, (28, 28), 27, (*colour, 255), -1)
        cv2.circle(image, (28, 28), 12, (*glyph, 255), -1)
        return image

    def place(self, frame, template, center, radius):
        diameter = radius * 2
        resized = cv2.resize(template, (diameter, diameter),
                             interpolation=cv2.INTER_AREA)
        alpha = resized[:, :, 3:4].astype(np.float32) / 255
        x0, y0 = center[0] - radius, center[1] - radius
        area = frame[y0:y0 + diameter, x0:x0 + diameter].astype(np.float32)
        frame[y0:y0 + diameter, x0:x0 + diameter] = (
            resized[:, :, :3] * alpha + area * (1 - alpha)
        ).astype(np.uint8)

    def test_dual_type_icons_are_read_in_primary_secondary_order(self):
        templates = {2: self.icon((30, 30, 200), (250, 250, 250)),
                     3: self.icon((40, 180, 60), (250, 250, 250))}
        frame = np.full((120, 200, 3), (50, 40, 30), np.uint8)
        box = [20, 10, 100, 82]
        centers = type_icons.slot_centers(box)
        self.place(frame, templates[2], centers["lower"], 10)
        self.place(frame, templates[3], centers["upper"], 10)
        result = type_icons.detect_types(
            Image.fromarray(frame[:, :, ::-1]), box, templates=templates
        )
        self.assertEqual(result["status"], "recognized")
        self.assertEqual(result["primary"], "普通系")
        self.assertEqual(result["secondary"], "草系")
        self.assertEqual(result["pair"], ["普通系", "草系"])
        self.assertTrue(result["slots"]["lower"]["detected"])
        self.assertTrue(result["slots"]["upper"]["detected"])

    def test_single_type_leaves_secondary_empty(self):
        templates = {2: self.icon((30, 30, 200), (250, 250, 250)),
                     3: self.icon((40, 180, 60), (250, 250, 250))}
        frame = np.full((120, 200, 3), (50, 40, 30), np.uint8)
        box = [20, 10, 100, 82]
        centers = type_icons.slot_centers(box)
        self.place(frame, templates[2], centers["lower"], 10)
        result = type_icons.detect_types(
            Image.fromarray(frame[:, :, ::-1]), box, templates=templates
        )
        self.assertEqual(result["status"], "primary_only")
        self.assertEqual(result["primary"], "普通系")
        self.assertIsNone(result["secondary"])
        self.assertEqual(result["pair"], ["普通系"])
        self.assertFalse(result["slots"]["upper"]["detected"])

    def test_plain_background_is_not_guessed_as_a_type(self):
        templates = {2: self.icon((30, 30, 200), (250, 250, 250)),
                     3: self.icon((40, 180, 60), (250, 250, 250))}
        frame = np.full((120, 200, 3), (50, 40, 30), np.uint8)
        result = type_icons.detect_types(
            Image.fromarray(frame[:, :, ::-1]), [20, 10, 100, 82], templates=templates
        )
        self.assertEqual(result["status"], "unresolved")
        self.assertIsNone(result["primary"])

    def test_missing_icon_library_disables_detection(self):
        result = type_icons.detect_types(
            Image.new("RGB", (200, 120)), [20, 10, 100, 82], templates={}
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["primary"])


if __name__ == "__main__":
    unittest.main()
