"""实时监控的窗口选择、状态整理和变化对比（不依赖游戏或 OCR 环境）。"""

import contextlib
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_monitor import (apply_identity, apply_skill, apply_status,
                          build_regions, diff_states, empty_state,
                          format_state, is_battle_like, pick_window,
                          push_state, signature_delta, state_summary,
                          wait_for_window)


class Box:
    def __init__(self, width, height):
        self.left, self.top, self.width, self.height = 0, 0, width, height


def window(title, width, height):
    return {"title": title, "hwnd": 1, "area": Box(width, height)}


def side(name, display=None, hp=None, max_hp=None, energy=None, percent=None):
    return {"name": name, "display": display, "hp": hp, "max_hp": max_hp,
            "hp_percent": percent, "energy": energy}


def state(our=None, opponent=None, skills=None):
    return {"our": our or {}, "opponent": opponent or {},
            "skills": skills or []}


def skill(slot, name, cooldown=None):
    return {"slot": slot, "name": name, "cooldown": cooldown}


class TestWindowSelection(unittest.TestCase):
    def test_skips_browser_tabs_that_also_contain_the_keyword(self):
        windows = [
            window("洛克王国世界 · 6 对 6 伤害范围 - Microsoft Edge", 1920, 1032),
            window("洛克王国：世界  ", 1600, 900),
        ]
        self.assertEqual(pick_window(windows)["title"].strip(), "洛克王国：世界")

    def test_prefers_a_known_game_content_size(self):
        windows = [window("洛克王国：世界  ", 1280, 720),
                   window("洛克王国：世界  ", 1600, 900)]
        picked = pick_window(windows)
        self.assertEqual((picked["area"].width, picked["area"].height), (1600, 900))

    def test_returns_none_without_a_game_window(self):
        self.assertIsNone(pick_window([window("记事本", 800, 600)]))

    def test_waits_until_the_game_window_appears(self):
        class Api:
            def __init__(self):
                self.polls = 0

            def windows(self, exclude_pid=None):
                self.polls += 1
                if self.polls < 2:
                    return []
                return [window("洛克王国：世界  ", 1600, 900)]

        api = Api()
        with contextlib.redirect_stdout(io.StringIO()):
            picked = wait_for_window(api, "洛克", 1.0, poll=0.01)
        self.assertEqual(picked["title"].strip(), "洛克王国：世界")

    def test_wait_times_out_without_a_game_window(self):
        class Api:
            def windows(self, exclude_pid=None):
                return []

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(wait_for_window(Api(), "洛克", 0.05, poll=0.01))


class TestStateSummary(unittest.TestCase):
    def test_summary_keeps_identity_hp_energy_and_skills(self):
        result = {
            "our": {"display_name": "蓝蓝", "name": "水灵", "number": "010",
                    "types": {"pair": ["水系"]}, "hp": 434, "max_hp": 434,
                    "energy": 10, "identity_source": "avatar",
                    "avatar_match": {"status": "visual_supported"}},
            "opponent": {"display_name": "小独角兽", "name": "小独角兽",
                         "hp_percent": 100, "energy": 10},
            "skills": [{"slot": 1, "name": "水泡盾", "category": "防御",
                        "power": None, "energy_cost": 2,
                        "energy_cost_status": "recognized", "cooldown": None}],
        }
        summary = state_summary(result)
        self.assertEqual(summary["our"]["name"], "水灵")
        self.assertEqual(summary["our"]["types"], ["水系"])
        self.assertEqual(summary["our"]["avatar_status"], "visual_supported")
        self.assertEqual(summary["opponent"]["hp_percent"], 100)
        self.assertEqual(summary["skills"][0]["energy"], 2)

    def test_menu_screens_are_not_battle_like(self):
        self.assertFalse(is_battle_like(state()))
        # 菜单页偶尔会读出技能名，不能只凭一个技能名就当成战斗。
        self.assertFalse(is_battle_like(state(skills=[skill(1, "水泡盾")])))
        self.assertTrue(is_battle_like(state(
            our=side("水灵", "蓝蓝", energy=10),
            skills=[skill(1, "水泡盾")],
        )))
        self.assertTrue(is_battle_like(state(
            our=side("水灵", "蓝蓝", hp=434, max_hp=434, energy=10),
            opponent=side("小独角兽", percent=100, energy=10),
        )))


class TestStateDiff(unittest.TestCase):
    def test_first_state_lists_both_sides(self):
        events = diff_states(None, state(our=side("水灵", "蓝蓝"),
                                         opponent=side("小独角兽")))
        self.assertEqual(events, ["我方：水灵（蓝蓝）", "对方：小独角兽"])

    def test_switch_hp_and_energy_are_reported_with_delta(self):
        old = state(our=side("水灵", hp=434, max_hp=434, energy=10),
                    opponent=side("小独角兽", percent=100, energy=10))
        new = state(our=side("抹茶布丁", hp=390, max_hp=434, energy=7),
                    opponent=side("红丝绒", percent=82, energy=10))
        events = diff_states(old, new)
        self.assertIn("我方换精灵：水灵 → 抹茶布丁", events)
        self.assertIn("对方换精灵：小独角兽 → 红丝绒", events)
        self.assertIn("我方生命 434→390（-44）", events)
        self.assertIn("对方生命 100%→82%（-18%）", events)
        self.assertIn("我方能量 10→7（-3）", events)
        self.assertNotIn("对方能量 10→10（+0）", events)

    def test_display_name_flicker_is_not_a_pet_switch(self):
        old = state(our=side("水灵", "蓝蓝", hp=434, max_hp=434, energy=10))
        new = state(our=side("水灵", None, hp=434, max_hp=434, energy=10))
        self.assertEqual(diff_states(old, new), [])

    def test_missing_readings_are_not_treated_as_zero(self):
        old = state(our=side("水灵", hp=434, max_hp=434, energy=None))
        new = state(our=side("水灵", hp=None, max_hp=434, energy=10))
        events = diff_states(old, new)
        self.assertEqual(events, [])

    def test_skill_name_and_cooldown_changes_are_reported(self):
        old = state(skills=[skill(1, "水泡盾", None), skill(2, "水炮", 0)])
        new = state(skills=[skill(1, "火焰护盾", 1), skill(2, "水炮", 2)])
        events = diff_states(old, new)
        self.assertIn("1号技能：水泡盾 → 火焰护盾", events)
        self.assertIn("2号技能冷却 0→2", events)


class TestFormat(unittest.TestCase):
    def test_line_contains_both_sides_and_skills(self):
        summary = state(
            our=side("水灵", "蓝蓝", hp=434, max_hp=434, energy=10),
            opponent=side("小独角兽", percent=100, energy=10),
            skills=[skill(1, "水泡盾", 1)],
        )
        text = format_state(summary)
        self.assertIn("我方 水灵（蓝蓝） 434/434 能量10", text)
        self.assertIn("对方 小独角兽 100% 能量10", text)
        self.assertIn("水泡盾(冷却1)", text)


LAYOUT = {
    "our": {"avatar": [34, 16, 111, 88], "display_name": [92, 35, 185, 59],
            "energy": [224, 83, 248, 105], "hp": [135, 60, 205, 80]},
    "opponent": {"avatar": [1337, 21, 1396, 83],
                 "display_name": [1398, 35, 1458, 60],
                 "energy": [1419, 84, 1443, 107],
                 "hp_percent": [1449, 60, 1500, 79]},
    "skills": [{"slot": slot, "name": [200 + slot, 300, 240 + slot, 320],
                "power": [210 + slot, 300, 250 + slot, 320],
                "energy_cost": [220 + slot, 300, 260 + slot, 320],
                "cooldown": [230 + slot, 300, 270 + slot, 320]}
               for slot in (1, 2, 3, 4)],
}


class TestPush(unittest.TestCase):
    def test_push_without_token_file_reports_missing_token(self):
        ok, error = push_state(
            "http://127.0.0.1:9",
            Path("definitely-missing-live-token"),
            {"active": True},
        )
        self.assertFalse(ok)
        self.assertIn("令牌", error)


class TestRegionsAndMerging(unittest.TestCase):
    def test_regions_cover_identity_status_and_each_skill(self):
        regions = build_regions(LAYOUT)
        self.assertEqual(set(regions), {
            "identity_our", "identity_opponent", "status_our",
            "status_opponent", "skill_1", "skill_2", "skill_3", "skill_4",
        })
        # 我方身份区域要包含头像左侧的属性图标和显示名右边界。
        self.assertEqual(regions["identity_our"], [14, 16, 185, 103])
        # 技能区域要包含名字、威力、能耗和冷却四个框。
        self.assertEqual(regions["skill_1"], [201, 300, 271, 320])

    def test_signature_delta_detects_changes(self):
        base = [[10, 20], [30, 40]]
        self.assertEqual(signature_delta(base, base), 0.0)
        changed = [[10, 20], [30, 60]]
        self.assertGreater(signature_delta(base, changed), 0.0)
        self.assertEqual(signature_delta(None, base), 255.0)

    def test_apply_identity_status_and_skill_merge_into_state(self):
        summary = empty_state(LAYOUT)
        apply_identity(summary, "our", {
            "label": "蓝蓝", "identity": {"name": "水灵", "number": "010",
                                          "form": None},
            "types": {"pair": ["水系"]}, "avatar": {"status": "visual_supported"},
            "source": "avatar",
        })
        self.assertEqual(summary["our"]["name"], "水灵")
        self.assertEqual(summary["our"]["display"], "蓝蓝")
        self.assertEqual(summary["our"]["types"], ["水系"])
        apply_status(summary, "our", {
            "energy": 8, "hp": 390, "max_hp": 434, "hp_percent": None,
        }, LAYOUT)
        self.assertEqual(summary["our"]["hp"], 390)
        self.assertEqual(summary["our"]["energy"], 8)
        apply_skill(summary, 2, {
            "name": "水炮", "category": "攻击", "power": 93,
            "energy_cost": 3, "energy_cost_status": "recognized",
            "cooldown": 1, "cooldown_status": "recognized",
        })
        self.assertEqual(summary["skills"][1]["name"], "水炮")
        self.assertEqual(summary["skills"][1]["power"], 93)
        self.assertEqual(summary["skills"][1]["cooldown"], 1)
        # 面板被动画挡住：名字/能量读不出时保留上次值，冷却消失时才清零。
        apply_skill(summary, 2, {
            "name": None, "category": None, "power": None,
            "energy_cost": None, "energy_cost_status": "unresolved",
            "cooldown": None, "cooldown_status": "unresolved",
        })
        self.assertEqual(summary["skills"][1]["name"], "水炮")
        self.assertEqual(summary["skills"][1]["energy"], 3)
        self.assertEqual(summary["skills"][1]["cooldown"], 1)
        apply_skill(summary, 2, {
            "name": "水炮", "category": "攻击", "power": 93,
            "energy_cost": 3, "energy_cost_status": "recognized",
            "cooldown": None, "cooldown_status": "absent",
        })
        self.assertIsNone(summary["skills"][1]["cooldown"])
        # 生命/能量读不出时保留上一次数值。
        apply_status(summary, "our", {"energy": None, "hp": None,
                                      "max_hp": None, "hp_percent": None}, LAYOUT)
        self.assertEqual(summary["our"]["hp"], 390)
        self.assertEqual(summary["our"]["energy"], 8)


if __name__ == "__main__":
    unittest.main()
