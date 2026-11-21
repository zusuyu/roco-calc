#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地网页服务（前端第 3 步）。

用法:
    python server.py
    浏览器打开 http://127.0.0.1:8000

只监听本机，不安装任何第三方包。提供：
    GET  /api/meta      精灵、性格、攻击技能和每只精灵的可学习技能
    GET  /api/ocr/status OCR 环境、模型、布局和头像状态
    POST /api/ocr/frame 静态操作帧 OCR（原始图片字节）
    GET  /assets/...    本机图片（精灵头像、技能图标）
    POST /api/analyze   单只精灵：返回对方范围和伤害范围
    POST /api/team      6 对 6：返回每只对方精灵的范围和双方技能表
    POST /api/battle    完整对局日志：返回推断结果和提示
    GET  /              最小页面 web/index.html
"""

import argparse
from datetime import datetime, timezone
import json
import secrets
import sys
import threading
import traceback
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import ocr_service as ocr
import roco_analysis as ra
import roco_battle as rb
import roco_calc as rc
import roco_skill_rules as rsr
import roco_team as rt

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
ASSET_DIR = WEB_DIR / "assets"
ASSET_MANIFEST = BASE_DIR / "data" / "assets.json"
LIVE_TOKEN_PATH = BASE_DIR / ".cache" / "live" / "token"
LIVE_LOCK = threading.Lock()
LIVE_PAYLOAD = None
LIVE_RECEIVED = None
LIVE_TOKEN = None

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/live": ("live.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

MAX_REQUEST_BYTES = 10_000_000


def ensure_live_token():
    """生成实时监控令牌并写入本机文件；监控进程读取它才能推送状态。"""
    global LIVE_TOKEN
    if LIVE_TOKEN:
        return LIVE_TOKEN
    token = secrets.token_urlsafe(24)
    LIVE_TOKEN = token
    LIVE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    return token


def read_live_state():
    """返回监控推送的最新状态；没有推送时 active=false。"""
    with LIVE_LOCK:
        payload = LIVE_PAYLOAD
        received = LIVE_RECEIVED
    if not payload:
        return {"active": False, "age_s": None}
    result = dict(payload)
    result["age_s"] = (round((datetime.now(timezone.utc) - received)
                             .total_seconds(), 1) if received else None)
    return result


def _skill_priority(desc):
    """保留旧名字，实际规则集中在 roco_skill_rules。"""
    return rsr.skill_priority(desc)


@lru_cache(maxsize=None)
def load_assets():
    """读取本地图片名单；没有下载图片时返回空名单，页面显示占位。"""
    try:
        payload = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=None)
def build_meta():
    """整理前端需要的精灵、性格、攻击技能和全部行动技能。"""
    learnsets = rc.load_learnsets()
    action_learnsets = rc.load_action_learnsets()
    assets = load_assets()
    pet_items = assets.get("pets")
    if not isinstance(pet_items, list):
        pet_items = []
    pet_avatars = {}
    for item in pet_items:
        if isinstance(item, dict):
            pet_avatars[
                (item.get("number"), item.get("name"), item.get("form") or "")
            ] = item.get("avatar")
    skill_items = assets.get("skills")
    if not isinstance(skill_items, list):
        skill_items = []
    skill_icons = {}
    for item in skill_items:
        if isinstance(item, dict) and item.get("name"):
            skill_icons[item["name"]] = item.get("icon")
    pets = [
        {
            "name": pet["name"],
            "form": pet["form"],
            "number": pet["number"],
            "types": pet["types"],
            "avatar": pet_avatars.get(
                (pet["number"], pet["name"], pet["form"])
            ),
            "skills": list(learnsets.get((pet["name"], pet["form"]), ())),
            "action_skills": list(
                action_learnsets.get((pet["name"], pet["form"]), ())
            ),
        }
        for pet in rc.load_pets()
    ]
    natures = [
        {
            "name": nature["name"],
            "up_name": nature["up_name"],
            "down_name": nature["down_name"],
        }
        for nature in rc.load_natures().values()
    ]
    skills = []
    for entries in rc.load_skills().values():
        for skill in entries:
            if skill["category"] != "攻击":
                continue
            if skill["damage_class"] not in ("物攻", "魔攻"):
                continue
            if skill.get("power") is None:
                continue
            priority, priority_note = _skill_priority(skill.get("desc"))
            skills.append(
                {
                    "name": skill["name"],
                    "element": skill["element"],
                    "damage_class": skill["damage_class"],
                    "power": skill["power"],
                    "energy": skill.get("energy"),
                    "priority": priority,
                    "priority_note": priority_note,
                    "icon": skill_icons.get(skill["name"]),
                }
            )
    skills.sort(key=lambda item: item["name"])

    action_skills = []
    for entries in rc.load_skills().values():
        for skill in entries:
            if skill["category"] not in rsr.ACTION_CATEGORIES:
                continue
            priority, priority_note = _skill_priority(skill.get("desc"))
            action_skills.append(
                {
                    "name": skill["name"],
                    "category": skill["category"],
                    "element": skill["element"],
                    "damage_class": skill["damage_class"],
                    "power": skill.get("power"),
                    "energy": skill.get("energy"),
                    "desc": skill.get("desc", ""),
                    "priority": priority,
                    "priority_note": priority_note,
                    "icon": skill_icons.get(skill["name"]),
                    "damage_reduction_percent": rsr.damage_reduction_percent(
                        skill.get("desc")
                    ),
                    "counter_tags": sorted(
                        rsr.counter_tags(skill.get("desc"))
                    ),
                }
            )
    action_skills.sort(key=lambda item: item["name"])
    return {
        "pets": pets,
        "natures": natures,
        "skills": skills,
        "action_skills": action_skills,
    }


def analyze_request(payload):
    """处理 /api/analyze 的请求体；出错时抛 roco_calc.RocoCalcError。"""
    if not isinstance(payload, dict):
        raise ra.AnalysisError("请求内容必须是一个 JSON 对象。")
    return ra.analyze_battle(payload)


def analyze_team_request(payload):
    """处理 /api/team 的请求体；出错时抛 roco_calc.RocoCalcError。"""
    if not isinstance(payload, dict):
        raise ra.AnalysisError("请求内容必须是一个 JSON 对象。")
    battle_log = payload.get("battle_log")
    if battle_log is not None:
        if not isinstance(battle_log, dict):
            raise rb.BattleLogError("battle_log 必须是字典。")
        battle_payload = dict(battle_log)
        battle_payload["active_our_slot"] = payload.get("active_our_slot", 0)
        battle_payload["active_opponent_slot"] = payload.get(
            "active_opponent_slot", 0
        )
        return rb.analyze_battle_active(battle_payload)
    return rt.analyze_team(payload)


def analyze_battle_request(payload):
    """处理 /api/battle 的请求体：整场对局日志。"""
    if not isinstance(payload, dict):
        raise rb.BattleLogError("请求内容必须是一个 JSON 对象。")
    return rb.analyze_battle_log(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "RocoCalc/0.3"
    protocol_version = "HTTP/1.1"

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_asset(self, path):
        """只允许读取 web/assets 下面的图片，防止跑到别的目录。"""
        relative = unquote(path[len("/assets/"):])
        try:
            asset_root = ASSET_DIR.resolve()
            file_path = (ASSET_DIR / relative).resolve()
            file_path.relative_to(asset_root)
        except (OSError, ValueError):
            self._send_json(404, {"error": "找不到这个图片。"})
            return
        if not file_path.is_file():
            self._send_json(404, {"error": "找不到这个图片。"})
            return
        content_type = (
            "image/png"
            if file_path.suffix.lower() == ".png"
            else "application/octet-stream"
        )
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "读取图片失败。"})
            return
        self._send_bytes(200, body, content_type)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/meta":
            self._send_json(200, build_meta())
            return
        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/ocr/status":
            query = parse_qs(urlsplit(self.path).query)
            force = query.get("force", [""])[0] in ("1", "true", "yes")
            self._send_json(200, ocr.status(force=force))
            return
        if path == "/api/live/state":
            self._send_json(200, read_live_state())
            return
        if path.startswith("/assets/"):
            self._send_asset(path)
            return

        static = STATIC_FILES.get(path)
        if static is None:
            self._send_json(404, {"error": "找不到这个地址。"})
            return

        filename, content_type = static
        file_path = WEB_DIR / filename
        if not file_path.exists():
            self._send_json(404, {"error": f"缺少网页文件 {filename}。"})
            return
        self._send_bytes(200, file_path.read_bytes(), content_type)

    def _handle_live_update(self):
        """接收实时监控推送的状态；只接受带本机令牌的请求。"""
        global LIVE_PAYLOAD, LIVE_RECEIVED

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "状态长度不合法。"})
            return
        if length <= 0:
            self._send_json(400, {"error": "状态内容为空。"})
            return
        if length > 2_000_000:
            self._send_json(413, {"error": "状态内容太大。"})
            return
        token = self.headers.get("X-Roco-Live-Token") or ""
        if not LIVE_TOKEN or not secrets.compare_digest(token, LIVE_TOKEN):
            self._send_json(403, {"error": "实时监控令牌不正确。"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "状态不是合法的 JSON。"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "状态必须是对象。"})
            return
        with LIVE_LOCK:
            LIVE_PAYLOAD = payload
            LIVE_RECEIVED = datetime.now(timezone.utc)
        self._send_json(200, {"ok": True})

    def _handle_ocr_frame(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)

        def value(name, default=None):
            items = query.get(name)
            return items[0] if items else default

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "图片长度不合法。"})
            return
        if length <= 0:
            self._send_json(400, {"error": "没有收到图片。"})
            return
        if length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "图片太大，请先裁成游戏内容区域。"})
            return
        body = self.rfile.read(length)
        layout = value("layout")
        if not layout:
            layouts = ocr.list_layouts()
            if not layouts:
                self._send_json(503, {"error": "没有找到 OCR 布局配置。"})
                return
            layout = layouts[0]["id"]
        try:
            image_path = ocr.save_upload(body)
            result = ocr.run_frame(
                image_path,
                layout,
                our_pet=value("our_pet") or None,
                opponent_pet=value("opponent_pet") or None,
                our_form=value("our_form") or None,
                opponent_form=value("opponent_form") or None,
                debug=value("debug") in ("1", "true", "yes"),
                no_avatar=value("no_avatar") in ("1", "true", "yes"),
            )
        except ocr.OcrUnavailable as exc:
            self._send_json(503, {"error": str(exc)})
            return
        except ocr.OcrError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception:
            traceback.print_exc()
            self._send_json(
                500,
                {"error": "OCR 服务器内部错误，请查看运行 server.py 的窗口。"},
            )
            return
        self._send_json(200, result)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/ocr/frame":
            self._handle_ocr_frame()
            return
        if path == "/api/live/update":
            self._handle_live_update()
            return
        if path not in ("/api/analyze", "/api/team", "/api/battle"):
            self._send_json(404, {"error": "找不到这个地址。"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "请求长度不合法。"})
            return
        if length <= 0:
            self._send_json(400, {"error": "请求内容为空。"})
            return
        if length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "请求内容太大。"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "请求不是合法的 JSON。"})
            return

        try:
            if path == "/api/team":
                result = analyze_team_request(payload)
            elif path == "/api/battle":
                result = analyze_battle_request(payload)
            else:
                result = analyze_request(payload)
        except rc.RocoCalcError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception:
            traceback.print_exc()
            self._send_json(
                500,
                {"error": "服务器内部错误，请看运行 server.py 的窗口。"},
            )
            return

        self._send_json(200, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description="洛克王国世界伤害范围：本地网页服务")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址，默认 127.0.0.1（只有本机能访问）")
    parser.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    args = parser.parse_args(argv)

    ensure_live_token()
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"错误：无法在 {args.host}:{args.port} 启动服务（{exc}）。", file=sys.stderr)
        print("可以换一个端口，例如：python server.py --port 8001", file=sys.stderr)
        return 1

    print(f"已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
