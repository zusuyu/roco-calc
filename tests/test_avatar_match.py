"""名字只能辅助头像候选，不能用冒名昵称替代视觉身份。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avatar_match import _load_groups, assess_candidates


def candidate(name, score, inliers=0, shared=None, template_score=0.0):
    return {"avatar": name + ".png", "score": score, "feature_inliers": inliers,
            "template_score": template_score,
            "pets": [{"pet_id": "pet_" + value, "name": value,
                      "number": "001", "form": ""}
                     for value in ([name] + (shared or []))]}


class TestAvatarNameEvidence(unittest.TestCase):
    def test_impostor_nickname_does_not_promote_lower_visual_candidate(self):
        rows = [candidate("迪莫", 0.98, 20), candidate("彩蝶鲨", 0.7, 0)]
        result = assess_candidates(rows, "彩蝶鲨")
        self.assertEqual(result["identity"]["name"], "迪莫")
        self.assertEqual(result["name_match_ranks"], [2])
        self.assertTrue(result["name_conflict"])
        self.assertEqual(result["candidates"][0]["pets"][0]["name"], "迪莫")
        self.assertNotIn("name_match", rows[0])

    def test_matching_name_alone_is_insufficient(self):
        result = assess_candidates([candidate("花衣蝶", 0.55),
                                    candidate("武者鸡", 0.4)], "花衣蝶")
        self.assertIsNone(result["identity"])
        self.assertEqual(result["name_check"], "matches_best")
        self.assertTrue(result["requires_review"])

    def test_name_can_support_first_candidate_with_local_feature_evidence(self):
        rows = [candidate("花衣蝶", 0.867, 5), candidate("武者鸡", 0.43)]
        self.assertIsNone(assess_candidates(rows, "玩家昵称")["identity"])
        supported = assess_candidates(rows, "花衣蝶")
        self.assertEqual(supported["status"], "visual_name_supported")
        self.assertEqual(supported["identity"]["name"], "花衣蝶")

    def test_shared_avatar_remains_ambiguous_even_when_name_matches(self):
        rows = [candidate("形态甲", 0.99, 15, ["形态乙"]), candidate("其他", 0.5)]
        result = assess_candidates(rows, "形态乙")
        self.assertIsNone(result["identity"])
        self.assertEqual(result["status"], "shared_avatar")
        self.assertEqual(len(result["candidates"][0]["pets"]), 2)

    def test_shiny_avatar_group_keeps_same_pet_identity(self):
        manifest = {
            "pets": [
                {
                    "number": "010",
                    "name": "水灵",
                    "form": "",
                    "avatar": "/assets/pets/PetAvatar_3005.png",
                    "shiny_avatar": "/assets/pets/PetAvatar_3437_1.png",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            groups = _load_groups(path)
        self.assertEqual(len(groups), 2)
        self.assertTrue(any(group["shiny"] for group in groups))
        self.assertTrue(
            all(group["pets"][0]["name"] == "水灵" for group in groups)
        )

    def test_small_score_margin_and_no_candidates_are_not_confirmed(self):
        rows = [candidate("花衣蝶", 0.97, 12), candidate("武者鸡", 0.96, 12)]
        self.assertIsNone(assess_candidates(rows, "花衣蝶")["identity"])
        result = assess_candidates([], "花衣蝶")
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["identity"])

    def test_close_scores_can_be_resolved_by_detail_evidence(self):
        # 实测圣水守护：最终分 0.990 / 水灵 0.979，但精细色块分 0.762 / 0.466、
        # 特征点 22 / 10，细节证据足够明确。
        rows = [candidate("圣水守护", 0.99, 22, template_score=0.762),
                candidate("水灵", 0.979, 10, template_score=0.466)]
        result = assess_candidates(rows, "蓝蓝")
        self.assertEqual(result["identity"]["name"], "圣水守护")
        self.assertEqual(result["status"], "visual_detail_supported")
        self.assertFalse(result["requires_review"])
        self.assertTrue(result["detail_evidence"]["used"])

    def test_detail_evidence_does_not_rescue_a_weaker_top_candidate(self):
        rows = [candidate("水灵", 0.99, 10, template_score=0.466),
                candidate("圣水守护", 0.979, 22, template_score=0.762)]
        result = assess_candidates(rows, None)
        self.assertIsNone(result["identity"])
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["detail_evidence"]["used"])

    def test_detail_evidence_needs_both_template_and_feature_gaps(self):
        rows = [candidate("甲", 0.99, 12, template_score=0.8),
                candidate("乙", 0.98, 10, template_score=0.6)]
        self.assertIsNone(assess_candidates(rows, None)["identity"])
        rows = [candidate("甲", 0.99, 22, template_score=0.6),
                candidate("乙", 0.98, 10, template_score=0.5)]
        self.assertIsNone(assess_candidates(rows, None)["identity"])

    def test_clear_margin_still_uses_the_primary_score(self):
        rows = [candidate("甲", 0.99, 8, template_score=0.4),
                candidate("乙", 0.90, 30, template_score=0.9)]
        result = assess_candidates(rows, None)
        self.assertEqual(result["status"], "visual_supported")
        self.assertFalse(result["detail_evidence"]["used"])

    def test_normal_and_shiny_groups_of_one_pet_do_not_count_as_two_pets(self):
        rows = [candidate("利灯鱼", 0.715, 5, template_score=0.5),
                candidate("利灯鱼", 0.446, 5, template_score=0.4),
                candidate("双灯鱼", 0.406, 0, template_score=0.2)]
        result = assess_candidates(rows, None,
                                   type_info={"applied": True, "pets": 2})
        self.assertEqual(result["identity"]["name"], "利灯鱼")
        self.assertEqual(result["status"], "visual_type_supported")
        self.assertAlmostEqual(result["score_margin"], 0.309, places=3)
        self.assertTrue(result["type_evidence"]["used"])

    def test_single_pet_type_pool_can_be_confirmed_without_a_runner_up(self):
        result = assess_candidates(
            [candidate("抹茶布丁", 0.586, 0, template_score=0.5)], None,
            type_info={"applied": True, "pets": 1},
        )
        self.assertEqual(result["identity"]["name"], "抹茶布丁")
        self.assertEqual(result["status"], "visual_type_supported")
        self.assertIsNone(result["score_margin"])

    def test_type_confidence_is_not_used_for_a_large_pet_pool(self):
        rows = [candidate("甲", 0.55, 0), candidate("乙", 0.30, 0)]
        result = assess_candidates(rows, None,
                                   type_info={"applied": True, "pets": 50})
        self.assertIsNone(result["identity"])
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["type_evidence"]["used"])


if __name__ == "__main__":
    unittest.main()
