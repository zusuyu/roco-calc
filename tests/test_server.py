#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页服务（server.py）的单元测试。

不启动固定端口：测试时用 0 号端口让系统临时分配，结束后关闭。
"""

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import roco_analysis as ra  # noqa: E402
import server  # noqa: E402


class QuietHandler(server.Handler):
    def log_message(self, format, *args):
        pass


class TestMeta(unittest.TestCase):
    def test_meta_counts(self):
        meta = server.build_meta()
        self.assertEqual(len(meta["pets"]), 621)
        self.assertEqual(len(meta["natures"]), 30)
        self.assertEqual(len(meta["skills"]), 358)
        for skill in meta["skills"]:
            self.assertIn(skill["damage_class"], ("物攻", "魔攻"))
            self.assertIsInstance(skill["power"], int)

    def test_meta_has_known_pets(self):
        names = {pet["name"] for pet in server.build_meta()["pets"]}
        self.assertIn("水灵", names)
        self.assertIn("龙息帕尔", names)

    def test_meta_skill_priority(self):
        skills = {
            skill["name"]: skill
            for skill in server.build_meta()["skills"]
        }
        self.assertEqual(skills["先发制人"]["priority"], 1)
        self.assertEqual(skills["俯冲"]["priority"], 1)
        self.assertEqual(skills["后发制人"]["priority"], -1)
        self.assertIsNone(skills["气泡"]["priority_note"])
        self.assertEqual(skills["气泡"]["priority"], 0)
        self.assertEqual(skills["拖拉机"]["priority"], 0)
        self.assertIn("1号位", skills["拖拉机"]["priority_note"])
        self.assertEqual(skills["扬尘"]["priority"], 0)
        self.assertIn("沙暴", skills["扬尘"]["priority_note"])

    def test_meta_has_learnable_attack_skills(self):
        pets = {
            (pet["name"], pet["form"]): pet["skills"]
            for pet in server.build_meta()["pets"]
        }
        self.assertIn("气泡", pets[("水灵", "")])
        self.assertIn("龙之利爪", pets[("龙息帕尔", "")])
        self.assertNotIn("光合作用", pets[("水灵", "")])
        self.assertEqual(len(pets), 621)
        self.assertTrue(all(pets.values()))

    def test_meta_has_asset_paths(self):
        meta = server.build_meta()
        water = next(
            pet for pet in meta["pets"]
            if pet["name"] == "水灵" and not pet["form"]
        )
        self.assertTrue(water["avatar"].startswith("/assets/pets/"))
        skills = {skill["name"]: skill for skill in meta["skills"]}
        self.assertTrue(skills["气泡"]["icon"].startswith("/assets/skills/"))

    def test_meta_action_skills(self):
        meta = server.build_meta()
        self.assertEqual(len(meta["action_skills"]), 579)
        actions = {skill["name"]: skill for skill in meta["action_skills"]}
        self.assertEqual(actions["防御"]["category"], "防御")
        self.assertEqual(actions["防御"]["damage_reduction_percent"], 70)
        self.assertIn("攻击", actions["防御"]["counter_tags"])
        self.assertEqual(actions["魔法增效"]["category"], "状态")
        self.assertEqual(actions["有效预防"]["priority"], 0)
        self.assertEqual(actions["马步"]["priority"], -1)
        self.assertEqual(actions["麻痹"]["priority"], 0)
        self.assertEqual(actions["风隐"]["priority"], -1)
        self.assertEqual(actions["伪造账单"]["priority"], 1)

    def test_meta_action_learnsets(self):
        meta = server.build_meta()
        water = next(
            pet for pet in meta["pets"]
            if pet["name"] == "水灵" and not pet["form"]
        )
        self.assertEqual(len(water["action_skills"]), 45)
        self.assertIn("气泡", water["action_skills"])
        self.assertIn("防御", water["action_skills"])
        self.assertIn("魔法增效", water["action_skills"])
        self.assertTrue(set(water["skills"]).issubset(water["action_skills"]))


class TestAnalyzeRequest(unittest.TestCase):
    def test_initial_range(self):
        result = server.analyze_request(
            {
                "our_pet": {
                    "name": "水灵",
                    "nature": "聪明",
                    "talents": {"spa": 10},
                },
                "opponent_pet": {"name": "龙息帕尔"},
                "skill": "气泡",
            }
        )
        self.assertEqual(result["damage"]["min"], 140)
        self.assertEqual(result["damage"]["max"], 210)
        self.assertEqual(result["opponent"]["builds"]["total"], 46320)

    def test_invalid_payload(self):
        with self.assertRaises(ra.AnalysisError):
            server.analyze_request([])


TEAM_STATE = {
    "level": 60,
    "our_team": [
        {
            "pet": "水灵",
            "nature": "聪明",
            "talents": {"spa": 10},
            "skills": ["气泡", "水炮"],
            "current_hp": 306,
        },
    ],
    "opponent_team": [
        {"pet": "龙息帕尔", "skills": ["龙之利爪"]},
    ],
    "active_our_slot": 0,
    "active_opponent_slot": 0,
    "records": [],
}


class TestTeamRequest(unittest.TestCase):
    def test_team_initial_state(self):
        result = server.analyze_team_request(TEAM_STATE)
        self.assertEqual(len(result["our_team"]), 6)
        self.assertEqual(len(result["opponent_team"]), 6)
        self.assertEqual(
            result["opponent_team"][0]["builds"]["remaining"], 46320
        )
        self.assertEqual(
            [skill["skill"] for skill in result["active"]["our_skills"]],
            ["气泡", "水炮"],
        )

    def test_team_invalid_skill_count(self):
        state = json.loads(json.dumps(TEAM_STATE, ensure_ascii=False))
        state["our_team"][0]["skills"] = [
            "气泡", "水炮", "天洪", "水弹枪", "涌泉",
        ]
        with self.assertRaises(ra.AnalysisError) as ctx:
            server.analyze_team_request(state)
        self.assertIn("最多只能有 4 个", str(ctx.exception))


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.read()

    def post(self, path, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()

    def test_live_update_requires_token_and_state_endpoint_returns_it(self):
        old_token = server.LIVE_TOKEN
        old_recording, old_session = server.LIVE_RECORDING, server.LIVE_SESSION
        server.LIVE_TOKEN = "test-live-token"
        server.LIVE_RECORDING, server.LIVE_SESSION = False, 0
        with server.LIVE_LOCK:
            server.LIVE_PAYLOAD = None
            server.LIVE_RECEIVED = None
        try:
            request = urllib.request.Request(
                self.base + "/api/live/update",
                data=json.dumps({"active": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(ctx.exception.code, 403)

            state = {"active": True, "battle": True,
                     "state": {"our": {"name": "水灵"}}}
            request = urllib.request.Request(
                self.base + "/api/live/update",
                data=json.dumps(state).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "X-Roco-Live-Token": "test-live-token"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            status, body = self.get("/api/live/state")
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(status, 200)
            self.assertTrue(payload["active"])
            self.assertEqual(payload["state"]["our"]["name"], "水灵")
            self.assertIsNotNone(payload["age_s"])
        finally:
            server.LIVE_TOKEN = old_token
            server.LIVE_RECORDING, server.LIVE_SESSION = old_recording, old_session
            with server.LIVE_LOCK:
                server.LIVE_PAYLOAD = None
                server.LIVE_RECEIVED = None

    def test_live_control_starts_and_stops_a_session(self):
        old_recording, old_session = server.LIVE_RECORDING, server.LIVE_SESSION
        server.LIVE_RECORDING, server.LIVE_SESSION = False, 0
        try:
            def control(action):
                request = urllib.request.Request(
                    self.base + "/api/live/control",
                    data=json.dumps({"action": action}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))

            started = control("start")
            self.assertTrue(started["recording"])
            self.assertEqual(started["session_id"], 1)
            stopped = control("stop")
            self.assertFalse(stopped["recording"])
            self.assertEqual(stopped["session_id"], 1)

            request = urllib.request.Request(
                self.base + "/api/live/control",
                data=json.dumps({"action": "bogus"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
        finally:
            server.LIVE_RECORDING, server.LIVE_SESSION = old_recording, old_session

    def test_index_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("伤害范围", text)
        self.assertIn("/app.js", text)

    def test_meta_endpoint(self):
        status, body = self.get("/api/meta")
        self.assertEqual(status, 200)
        meta = json.loads(body.decode("utf-8"))
        self.assertEqual(len(meta["pets"]), 621)

    def test_ocr_status_endpoint(self):
        status, body = self.get("/api/ocr/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("ready", data)
        self.assertIn("layouts", data)
        self.assertIn("message", data)

    def test_ocr_frame_empty_body(self):
        request = urllib.request.Request(
            self.base + "/api/ocr/frame?layout=ocr_reference4",
            data=b"",
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_asset_file_served(self):
        meta = server.build_meta()
        water = next(
            pet for pet in meta["pets"]
            if pet["name"] == "水灵" and not pet["form"]
        )
        file_path = server.WEB_DIR / water["avatar"].lstrip("/")
        if not file_path.exists():
            self.skipTest("本机还没有下载图片")
        status, body = self.get(water["avatar"])
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_missing_asset_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/assets/pets/does-not-exist.png")
        self.assertEqual(ctx.exception.code, 404)

    def test_asset_path_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/assets/%2e%2e/app.js")
        self.assertEqual(ctx.exception.code, 404)

    def test_analyze_endpoint(self):
        status, body = self.post(
            "/api/analyze",
            {
                "our_pet": {
                    "name": "水灵",
                    "nature": "聪明",
                    "talents": {"spa": 10},
                },
                "opponent_pet": {"name": "龙息帕尔"},
                "skill": "气泡",
            },
        )
        self.assertEqual(status, 200)
        result = json.loads(body.decode("utf-8"))
        self.assertEqual(result["damage"]["min"], 140)

    def test_analyze_error_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post(
                "/api/analyze",
                {
                    "our_pet": {"name": "不存在的精灵"},
                    "opponent_pet": {"name": "龙息帕尔"},
                    "skill": "气泡",
                },
            )
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("找不到精灵", error["error"])

    def test_team_endpoint(self):
        status, body = self.post("/api/team", TEAM_STATE)
        self.assertEqual(status, 200)
        result = json.loads(body.decode("utf-8"))
        self.assertEqual(len(result["opponent_team"]), 6)
        self.assertEqual(result["opponent_team"][0]["pet"], "龙息帕尔")

    def test_battle_endpoint(self):
        payload = {
            "version": 1,
            "initial": {
                "our_team": [
                    {
                        "pet": "水灵",
                        "nature": "聪明",
                        "talents": {"spa": 10},
                        "skills": ["气泡"],
                    }
                ],
                "opponent_team": [{"pet": "龙息帕尔"}],
                "active_our": 0,
                "active_opp": 0,
            },
            "turns": [],
        }
        status, body = self.post("/api/battle", payload)
        self.assertEqual(status, 200)
        result = json.loads(body.decode("utf-8"))
        self.assertEqual(len(result["opponent_team"]), 6)
        self.assertEqual(
            result["opponent_team"][0]["builds"]["remaining"], 46320
        )

    def test_team_battle_log_endpoint(self):
        battle_log = {
            "version": 1,
            "initial": {
                "our_team": [
                    {
                        "pet": "水灵",
                        "nature": "聪明",
                        "talents": {"spa": 10},
                        "skills": ["气泡"],
                    }
                ],
                "opponent_team": [{"pet": "龙息帕尔"}],
                "active_our": 0,
                "active_opp": 0,
            },
            "turns": [],
        }
        status, body = self.post(
            "/api/team",
            {
                "battle_log": battle_log,
                "active_our_slot": 0,
                "active_opponent_slot": 0,
            },
        )
        self.assertEqual(status, 200)
        result = json.loads(body.decode("utf-8"))
        self.assertEqual(
            result["active"]["our_skills"][0]["skill"], "气泡"
        )
        self.assertEqual(
            result["opponent_team"][0]["builds"]["remaining"], 46320
        )

    def test_battle_error_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/battle", {"version": 99})
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("版本", error["error"])

    def test_team_error_returns_400(self):
        state = json.loads(json.dumps(TEAM_STATE, ensure_ascii=False))
        state["our_team"][0]["skills"] = [
            "气泡", "水炮", "天洪", "水弹枪", "涌泉",
        ]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/team", state)
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("最多只能有 4 个", error["error"])


if __name__ == "__main__":
    unittest.main()
