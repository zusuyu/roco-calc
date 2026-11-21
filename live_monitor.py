#!/usr/bin/env python3
"""实时监控游戏窗口：每 0.1 秒截图，按区域差异触发解析。

只在 OCR 虚拟环境里运行（需要 mss / rapidocr / opencv）：
    .venv-ocr314\\Scripts\\python.exe live_monitor.py
    .venv-ocr314\\Scripts\\python.exe live_monitor.py --self-test

工作方式：
- 截图线程固定每 1/fps 秒抓一张游戏客户区（默认 0.1 秒）；
- 每个解析区域先做灰度缩略图差异比较（纯像素比较，不做 OCR）；
- 只有发生变化的区域才进入解析队列；队列会合并，始终解析最新一帧；
- 头像/属性/显示名和技能槽只在变化时重新识别，并缓存其余状态；
- 状态写入 .cache/live/latest.json（网页实时面板读取）和会话 JSONL。

工具只截取游戏窗口，不读内存、不注入、不修改游戏。
"""

import argparse
import copy
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_LAYOUT = ROOT / "configs" / "ocr_1602x932_window.json"
DEFAULT_OUTPUT = ROOT / ".cache" / "live"
DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_TOKEN_FILE = DEFAULT_OUTPUT / "token"
GAME_TITLE_KEYWORD = "洛克"
BROWSER_TITLE_PARTS = ("Microsoft Edge", "Google Chrome", "浏览器", "Firefox")
CONTENT_SIZES = {(1600, 900), (1602, 932), (2094, 1179)}

# 区域类型 -> (缩略图尺寸, 触发阈值)。阈值越低越敏感；只影响是否解析，不影响识别。
SIGNATURE_SETTINGS = {
    "identity": ((32, 24), 8.0),
    "status": ((24, 12), 2.0),
    "skill": ((32, 24), 3.0),
}


def _area_size(area):
    if hasattr(area, "width") and hasattr(area, "height"):
        return int(area.width), int(area.height)
    return int(area["width"]), int(area["height"])


def pick_window(windows, keyword=GAME_TITLE_KEYWORD):
    """从窗口列表里选游戏窗口，避开标题里带“洛克”的浏览器页面。"""
    candidates = []
    for window in windows:
        title = (window.get("title") or "").strip()
        if keyword not in title:
            continue
        if any(part.lower() in title.lower() for part in BROWSER_TITLE_PARTS):
            continue
        width, height = _area_size(window["area"])
        if (width, height) not in CONTENT_SIZES and width < 800:
            continue
        candidates.append((window, width, height))
    if not candidates:
        return None

    def score(item):
        _, width, height = item
        known = 1 if (width, height) in CONTENT_SIZES else 0
        return (known, -width * height)

    return max(candidates, key=score)[0]


def wait_for_window(api, title, wait_seconds, exclude_pid=None, poll=2.0):
    """找不到游戏窗口时等待一会儿，方便先开监控再启动游戏。"""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    announced = False
    while True:
        window = pick_window(api.windows(exclude_pid=exclude_pid), title)
        if window is not None:
            return window
        if time.monotonic() >= deadline:
            return None
        if not announced:
            print(f"没有找到标题包含“{title}”的游戏窗口，等待游戏打开……")
            announced = True
        time.sleep(poll)


# ---------- 状态结构与差异 ----------

def _union(boxes):
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def build_regions(layout):
    """把布局拆成“解析区域组”：身份、生命/能量、四个技能。"""
    regions = {}
    for side in ("our", "opponent"):
        boxes = []
        if "avatar" in layout[side]:
            avatar = layout[side]["avatar"]
            boxes.append([avatar[0] - 20, avatar[1], avatar[2], avatar[3] + 15])
        if "avatar_match" in layout[side]:
            boxes.append(layout[side]["avatar_match"])
        regions[f"identity_{side}"] = (_union(boxes) if boxes
                                       else layout[side]["display_name"])
        status_boxes = [layout[side][key] for key in ("hp", "hp_percent", "energy")
                        if key in layout[side]]
        regions[f"status_{side}"] = _union(status_boxes)
    for skill in layout["skills"]:
        boxes = [skill["name"], skill["power"], skill["energy_cost"]]
        if "cooldown" in skill:
            boxes.append(skill["cooldown"])
        regions[f"skill_{skill['slot']}"] = _union(boxes)
    return regions


def group_kind(group):
    return group.split("_", 1)[0]


def frame_signature(frame, box, size):
    """取区域灰度缩略图；这是纯像素比较，不做 OCR。"""
    import cv2

    x0, y0, x1, y1 = [int(value) for value in box]
    crop = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return cv2.resize(gray, (int(size[0]), int(size[1])),
                      interpolation=cv2.INTER_AREA).tolist()


def signature_delta(previous, current):
    """两个缩略图的平均像素差；空值视为完全变化。"""
    if previous is None or current is None:
        return 255.0
    total = 0
    count = 0
    for row_previous, row_current in zip(previous, current):
        for old, new in zip(row_previous, row_current):
            total += abs(int(old) - int(new))
            count += 1
    return total / count if count else 255.0


def empty_side():
    return {"display": None, "name": None, "number": None, "form": None,
            "types": [], "hp": None, "max_hp": None, "hp_percent": None,
            "energy": None, "avatar_status": None, "identity_source": None}


def empty_state(layout):
    return {
        "our": empty_side(),
        "opponent": empty_side(),
        "skills": [{"slot": skill["slot"], "name": None, "category": None,
                    "power": None, "energy": None, "energy_status": None,
                    "cooldown": None}
                   for skill in sorted(layout["skills"], key=lambda item: item["slot"])],
    }


def label_of(side):
    if not side:
        return "未识别"
    name = side.get("name")
    if name:
        form = side.get("form")
        return f"{name}（{form}）" if form else name
    return side.get("display") or "未识别"


def state_summary(result):
    """把 analyze_frame 的完整结果转成监控用的紧凑状态。"""
    def side(key):
        value = result.get(key) or {}
        avatar = value.get("avatar_match") or {}
        return {
            "display": value.get("display_name"),
            "name": value.get("name"),
            "number": value.get("number"),
            "form": value.get("form"),
            "types": (value.get("types") or {}).get("pair") or [],
            "hp": value.get("hp"),
            "max_hp": value.get("max_hp"),
            "hp_percent": value.get("hp_percent"),
            "energy": value.get("energy"),
            "avatar_status": avatar.get("status"),
            "identity_source": value.get("identity_source"),
        }

    skills = []
    for skill in result.get("skills") or []:
        skills.append({
            "slot": skill.get("slot"),
            "name": skill.get("name"),
            "category": skill.get("category"),
            "power": skill.get("power"),
            "energy": skill.get("energy_cost"),
            "energy_status": skill.get("energy_cost_status"),
            "cooldown": skill.get("cooldown"),
        })
    return {"our": side("our"), "opponent": side("opponent"), "skills": skills}


def is_battle_like(summary):
    """菜单页面也可能读出个别字；要求多个战斗 HUD 信号同时存在。"""
    def present(value):
        return value is not None and value != ""

    our = summary.get("our") or {}
    opponent = summary.get("opponent") or {}
    signals = sum(present(value) for value in (
        our.get("name"), our.get("hp"), our.get("energy"),
        opponent.get("name"), opponent.get("hp_percent"), opponent.get("energy"),
    ))
    skills = any(skill.get("name") for skill in summary.get("skills") or [])
    return signals >= 3 or (skills and signals >= 2)


def diff_states(previous, current):
    """对比两次状态，返回人话事件；null 不当作 0。"""
    events = []
    if previous is None:
        for key, prefix in (("our", "我方"), ("opponent", "对方")):
            side = current.get(key) or {}
            if side.get("name") or side.get("display"):
                events.append(f"{prefix}：{label_of(side)}")
        return events
    for key, prefix in (("our", "我方"), ("opponent", "对方")):
        old = previous.get(key) or {}
        new = current.get(key) or {}
        old_label, new_label = label_of(old), label_of(new)
        # 只有“确认的原名”变化才算换人；显示名可以改、也会读错，不能作依据。
        switched = bool(old.get("name") and new.get("name")
                        and old["name"] != new["name"])
        if switched:
            events.append(f"{prefix}换精灵：{old_label} → {new_label}")
        if not switched:
            if key == "our":
                if old.get("hp") is not None and new.get("hp") is not None \
                        and old["hp"] != new["hp"]:
                    delta = new["hp"] - old["hp"]
                    events.append(f"我方生命 {old['hp']}→{new['hp']}（{delta:+d}）")
            else:
                if old.get("hp_percent") is not None and new.get("hp_percent") is not None \
                        and old["hp_percent"] != new["hp_percent"]:
                    delta = new["hp_percent"] - old["hp_percent"]
                    events.append(f"对方生命 {old['hp_percent']}%→{new['hp_percent']}%（{delta:+d}%）")
            if old.get("energy") is not None and new.get("energy") is not None \
                    and old["energy"] != new["energy"]:
                delta = new["energy"] - old["energy"]
                events.append(f"{prefix}能量 {old['energy']}→{new['energy']}（{delta:+d}）")

    old_skills = {skill.get("slot"): skill for skill in previous.get("skills") or []}
    for skill in current.get("skills") or []:
        old = old_skills.get(skill.get("slot"))
        if not old:
            continue
        if old.get("name") and skill.get("name") and old["name"] != skill["name"]:
            events.append(f"{skill['slot']}号技能：{old['name']} → {skill['name']}")
        if old.get("cooldown") is not None and skill.get("cooldown") is not None \
                and old["cooldown"] != skill["cooldown"]:
            events.append(f"{skill['slot']}号技能冷却 {old['cooldown']}→{skill['cooldown']}")
    return events


def format_state(summary):
    our, opponent = summary["our"], summary["opponent"]
    our_hp = (f"{our['hp']}/{our['max_hp']}"
              if our.get("hp") is not None and our.get("max_hp") is not None
              else "生命未知")
    opp_hp = (f"{opponent['hp_percent']}%"
              if opponent.get("hp_percent") is not None else "生命未知")
    our_energy = f"能量{our['energy']}" if our.get("energy") is not None else "能量未知"
    opp_energy = (f"能量{opponent['energy']}"
                  if opponent.get("energy") is not None else "能量未知")
    skills = []
    for skill in summary.get("skills") or []:
        if skill.get("name"):
            text = skill["name"]
            if skill.get("cooldown") is not None:
                text += f"(冷却{skill['cooldown']})"
            skills.append(text)
    skill_text = "、".join(skills) if skills else "技能未识别"
    return (f"我方 {label_of(our)} {our_hp} {our_energy} ｜ "
            f"对方 {label_of(opponent)} {opp_hp} {opp_energy} ｜ {skill_text}")


# ---------- 状态合并 ----------

def apply_identity(summary, side, ident):
    """识别失败时保留上一次确认的身份；显示名变了才清掉旧身份。"""
    target = summary[side]
    old_display = target.get("display")
    new_display = ident["label"]
    identity = ident["identity"]
    if identity is not None:
        target["name"] = identity["name"]
        target["number"] = identity["number"]
        target["form"] = identity["form"]
        target["identity_source"] = ident["source"]
    elif new_display and new_display != old_display:
        target["name"] = None
        target["number"] = None
        target["form"] = None
        target["identity_source"] = None
    target["display"] = new_display
    types = (ident.get("types") or {}).get("pair") or []
    if types:
        target["types"] = types
    elif new_display and new_display != old_display:
        target["types"] = []
    target["avatar_status"] = (ident.get("avatar") or {}).get("status")


def apply_status(summary, side, status, layout):
    """读不出的数字不覆盖上一次的值，避免动画遮挡把生命/能量清空。"""
    target = summary[side]
    if status.get("energy") is not None:
        target["energy"] = status["energy"]
    if "hp" in layout[side] and status.get("hp") is not None:
        target["hp"] = status["hp"]
        target["max_hp"] = status["max_hp"]
    if "hp_percent" in layout[side] and status.get("hp_percent") is not None:
        target["hp_percent"] = status["hp_percent"]


def apply_skill(summary, slot, result):
    """技能面板被动画挡住时保留上次结果；冷却消失时按 0 更新。"""
    for skill in summary["skills"]:
        if skill["slot"] == slot:
            if result.get("name") is not None:
                skill["name"] = result["name"]
                skill["category"] = result["category"]
                skill["power"] = result["power"]
            if result.get("energy_cost") is not None:
                skill["energy"] = result["energy_cost"]
                skill["energy_status"] = result["energy_cost_status"]
            cooldown_state = result.get("cooldown_status")
            if cooldown_state == "absent":
                skill["cooldown"] = None
            elif cooldown_state == "recognized":
                skill["cooldown"] = result.get("cooldown")
            elif result.get("cooldown") is not None:
                skill["cooldown"] = result["cooldown"]
            return


# ---------- 截图线程 ----------

class MonitorState:
    def __init__(self):
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.frame = None
        self.dirty = set()
        self.running = True
        self.failure = None
        self.stats = {"snapshots": 0, "capture_ms": 0.0, "diff_ms": 0.0,
                      "capture_errors": 0}
        self.paused_announced = 0.0

    def stop(self, failure=None):
        with self.lock:
            self.running = False
            if failure and not self.failure:
                self.failure = str(failure)
            self.condition.notify_all()


def capture_client(area):
    import mss
    from PIL import Image

    box = {"left": area.left, "top": area.top,
           "width": area.width, "height": area.height}
    with mss.MSS() as sct:
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.rgb)


def capture_loop(api, hwnd, layout, regions, state, fps, refresh_s, stop_event,
                 allow_background=False):
    import numpy as np

    from ocr_frame import resolve_frame_region

    previous = {}
    next_tick = time.perf_counter()
    next_refresh = time.perf_counter() + refresh_s if refresh_s > 0 else None
    while not stop_event.is_set() and state.running:
        now = time.perf_counter()
        if not api.user.IsWindow(hwnd):
            state.stop("游戏窗口已关闭")
            return
        if not allow_background and (
                not api.foreground(hwnd) or api.user.IsIconic(hwnd)):
            if now - state.paused_announced > 5:
                print("游戏不在前台，暂停截图；切回游戏后自动继续。")
                state.paused_announced = now
            stop_event.wait(0.3)
            next_tick = time.perf_counter()
            continue
        try:
            area = api.area(hwnd)
            try:
                resolve_frame_region(layout, (area.width, area.height))
            except ValueError as exc:
                state.stop(f"游戏窗口尺寸 {area.width}×{area.height} 与布局不匹配：{exc}")
                return
            started = time.perf_counter()
            image = capture_client(area)
            pixels = np.asarray(image)
            captured = time.perf_counter()
            changes = set()
            for group, box in regions.items():
                size, threshold = SIGNATURE_SETTINGS[group_kind(group)]
                signature = frame_signature(pixels, box, size)
                if signature_delta(previous.get(group), signature) >= threshold:
                    changes.add(group)
                previous[group] = signature
            finished = time.perf_counter()
            if next_refresh is not None and now >= next_refresh:
                changes.update(regions)
                next_refresh = now + refresh_s
            with state.lock:
                state.frame = image
                state.dirty.update(changes)
                state.stats["snapshots"] += 1
                state.stats["capture_ms"] += (captured - started) * 1000
                state.stats["diff_ms"] += (finished - captured) * 1000
                if changes:
                    state.condition.notify_all()
        except Exception as exc:
            with state.lock:
                state.stats["capture_errors"] += 1
            print(f"截图失败：{exc}")
            stop_event.wait(1.0)
            next_tick = time.perf_counter()
            continue
        next_tick += 1.0 / fps
        delay = next_tick - time.perf_counter()
        if delay < 0:
            next_tick = time.perf_counter()
            delay = 0
        stop_event.wait(delay)


# ---------- 解析循环 ----------

def push_state(server_url, token_file, payload, timeout=1.0):
    """把状态推给本地网页服务，并取回“开始/结束记录”的控制状态。"""
    import urllib.error
    import urllib.request

    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return False, "没有找到实时面板令牌", {}
    request = urllib.request.Request(
        server_url.rstrip("/") + "/api/live/update",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Roco-Live-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            try:
                control = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                control = {}
            return 200 <= response.status < 300, None, control
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc), {}


def parse_loop(state, layout, engine, matcher, args, started):
    from ocr_frame import (parse_identity, parse_skill, parse_status)

    all_groups = set(build_regions(layout))
    summary = empty_state(layout)
    previous_state = None
    event_history = deque(maxlen=50)
    timings = {"groups": 0, "parse_ms": 0.0, "parses": 0, "events": 0}
    last_print = 0.0
    last_log = 0.0
    last_publish = 0.0
    last_push_warning = 0.0
    last_batch_ms = 0.0
    session = None
    last_session = None
    session_id = 0
    recording = False
    session_started = 0.0
    session_events = 0
    log_path = None
    frames_dir = None
    event_counter = 0

    def snapshot():
        with state.lock:
            stats = dict(state.stats)
        return {
            "active": True,
            "recording": recording,
            "session_id": session_id,
            "session": str(session) if session else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.perf_counter() - started, 3),
            "battle": is_battle_like(summary),
            "state": summary,
            "events": list(event_history),
            "timings": {
                "last_parse_ms": round(last_batch_ms, 1),
                "groups": timings["groups"],
                "parse_ms": round(timings["parse_ms"], 1),
                "parses": timings["parses"],
                "events": timings["events"],
            },
            "stats": stats,
        }

    def begin_session(new_id):
        nonlocal summary, previous_state, session, session_id, recording
        nonlocal session_started, session_events, log_path, frames_dir, event_counter
        session_id = new_id
        recording = True
        session = session_folder(args.output)
        log_path = session / "state.jsonl"
        frames_dir = session / "frames"
        session_started = time.perf_counter()
        session_events = 0
        event_counter = 0
        summary = empty_state(layout)
        previous_state = None
        event_history.clear()
        with state.condition:
            state.dirty.update(all_groups)
            state.condition.notify_all()
        print(f"开始记录第 {session_id} 场：{session}")

    def end_session(reason):
        nonlocal recording, session, last_session, log_path, frames_dir
        if session is not None:
            last_session = session
            payload = {
                "session_id": session_id,
                "reason": reason,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(time.perf_counter() - session_started, 3),
                "events": session_events,
                "state": summary,
            }
            (session / "summary.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8")
            print(f"结束记录第 {session_id} 场：{session_events} 条变化；目录：{session}")
        recording = False
        session = None
        log_path = None
        frames_dir = None

    def publish():
        nonlocal last_publish, last_push_warning
        ok, error, control = push_state(args.server, args.token_file, snapshot())
        if not ok:
            if time.perf_counter() - last_push_warning > 10:
                print(f"实时面板没收到状态：{error}（战斗记录仍写会话日志）")
                last_push_warning = time.perf_counter()
        else:
            if control.get("recording") and control.get("session_id") != session_id:
                begin_session(int(control.get("session_id") or 0))
            elif not control.get("recording") and recording:
                end_session("用户在网页结束")
        last_publish = time.perf_counter()

    while state.running:
        with state.condition:
            if not state.dirty:
                state.condition.wait(0.2)
            if not state.running:
                break
            groups = sorted(
                state.dirty,
                key=lambda group: (0 if group.startswith("identity") else
                                   1 if group.startswith("status") else 2, group),
            )
            state.dirty.clear()
            frame = state.frame if groups else None
        elapsed = time.perf_counter() - started
        if groups and frame is not None:
            batch_start = time.perf_counter()
            pool = None
            for group in groups:
                group_start = time.perf_counter()
                try:
                    if group.startswith("identity_"):
                        side = group.split("_", 1)[1]
                        before_name = (summary["our"].get("name")
                                       if side == "our" else None)
                        ident = parse_identity(frame, side, layout, engine, matcher)
                        apply_identity(summary, side, ident)
                        if side == "our" and summary["our"].get("name") != before_name:
                            # 我方换精灵后旧技能不能继续挂着；等新面板出现再填。
                            for item in summary["skills"]:
                                item.update(name=None, category=None, power=None,
                                            energy=None, energy_status=None,
                                            cooldown=None)
                    elif group.startswith("status_"):
                        side = group.split("_", 1)[1]
                        status = parse_status(frame, side, layout, engine)
                        apply_status(summary, side, status, layout)
                    elif group.startswith("skill_"):
                        slot = int(group.split("_", 1)[1])
                        if pool is None:
                            from ocr_frame import load_pool_for
                            our = summary["our"]
                            pool = (load_pool_for({"name": our.get("name"),
                                                   "form": our.get("form")})
                                    if our.get("name") else load_pool_for(None))
                        region = next(item for item in layout["skills"]
                                      if item["slot"] == slot)
                        result, _ = parse_skill(frame, region, engine, pool)
                        apply_skill(summary, slot, result)
                    timings["groups"] += 1
                except Exception as exc:
                    print(f"解析 {group} 失败：{exc}")
                timings["parse_ms"] += (time.perf_counter() - group_start) * 1000
            timings["parses"] += 1
            last_batch_ms = (time.perf_counter() - batch_start) * 1000

            if recording:
                events = diff_states(previous_state, summary)
                previous_state = copy.deepcopy(summary)
                timings["events"] += len(events)
                session_events += len(events)
                for event in events:
                    event_history.append({
                        "elapsed_s": round(elapsed, 1),
                        "text": event,
                    })
                if events or elapsed - last_print >= 3:
                    print(f"[{elapsed:6.1f}s] {format_state(summary)}")
                    for event in events:
                        print(f"           · {event}")
                    last_print = elapsed
                if events or elapsed - last_log >= 5:
                    record = {
                        "elapsed_s": round(elapsed, 3),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "groups": groups,
                        "events": events,
                        "state": summary,
                    }
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    last_log = elapsed
                    if events and args.save_events:
                        frames_dir.mkdir(parents=True, exist_ok=True)
                        event_counter += 1
                        frame.save(frames_dir / f"event_{event_counter:04d}_{elapsed:07.1f}s.png")
        # 有变化时立即推送；没有变化时每秒推一次心跳，页面据此判断监控还活着。
        if groups or time.perf_counter() - last_publish >= 1.0:
            publish()
    if recording:
        end_session("监控退出")
    return summary, timings, list(event_history), last_session


def session_folder(output):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(output) / f"session_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_ocr():
    import logging

    from download_ocr import prepare_config
    from rapidocr import RapidOCR

    engine = RapidOCR(config_path=str(prepare_config()))
    logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    return engine


def load_matcher():
    from avatar_match import AvatarMatcher

    return AvatarMatcher()


def run(args):
    import os

    # 让截图线程在 OCR 解析期间也能及时拿到 GIL，尽量接近 0.1 秒一张。
    sys.setswitchinterval(0.001)
    from windows_capture import WindowsAPI
    from ocr_frame import analyze_frame, validate_layout

    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    validate_layout(layout, tuple(layout["image_size"]))
    api = WindowsAPI()
    window = wait_for_window(api, args.title, args.wait, exclude_pid=os.getpid())
    if window is None:
        titles = "；".join(w["title"] for w in api.windows())
        print(f"等待超时：没有找到标题包含“{args.title}”的游戏窗口。当前窗口：{titles}")
        return 2
    hwnd = window["hwnd"]
    area = api.area(hwnd)
    print(f"已锁定游戏窗口：{window['title'].strip()}  "
          f"内容区域 {area.width}×{area.height}（屏幕位置 {area.left},{area.top}）")
    print("正在加载 OCR 模型和头像索引……")
    engine = load_ocr()
    matcher = load_matcher()
    if args.self_test:
        image = capture_client(api.area(hwnd))
        result = analyze_frame(image, layout, engine, avatar_matcher=matcher)
        summary = state_summary(result)
        print(json.dumps({
            "window": window["title"].strip(),
            "client_size": [area.width, area.height],
            "battle": is_battle_like(summary),
            "summary": summary,
        }, ensure_ascii=False, indent=1))
        return 0

    regions = build_regions(layout)
    state = MonitorState()
    stop_event = threading.Event()
    started = time.perf_counter()
    capture = threading.Thread(
        target=capture_loop,
        args=(api, hwnd, layout, regions, state, args.fps, args.refresh,
              stop_event, args.allow_background),
        daemon=True,
    )
    capture.start()
    duration_timer = None
    if args.duration > 0:
        duration_timer = threading.Timer(args.duration, state.stop)
        duration_timer.daemon = True
        duration_timer.start()
    print(f"实时监控已启动：每 {1 / args.fps:.2f} 秒检查一次画面，"
          f"只有变化的区域才解析。请在网页点击“开始记录本场”。")
    summary = empty_state(layout)
    timings = {}
    event_history = []
    last_session = None
    try:
        summary, timings, event_history, last_session = parse_loop(
            state, layout, engine, matcher, args, started)
    except KeyboardInterrupt:
        print("\n收到停止信号。")
    finally:
        state.stop()
        stop_event.set()
        if duration_timer is not None:
            duration_timer.cancel()
        with state.condition:
            state.condition.notify_all()
        capture.join(timeout=2)
        elapsed = time.perf_counter() - started
        with state.lock:
            stats = dict(state.stats)
        attempt = max(1, stats["snapshots"])
        summary_payload = {
            "elapsed_s": round(elapsed, 3),
            "snapshots": stats["snapshots"],
            "observed_fps": round(stats["snapshots"] / max(elapsed, 0.001), 2),
            "avg_capture_ms": round(stats["capture_ms"] / attempt, 2),
            "avg_diff_ms": round(stats["diff_ms"] / attempt, 2),
            "capture_errors": stats["capture_errors"],
            "parses": timings.get("parses", 0),
            "groups_parsed": timings.get("groups", 0),
            "avg_parse_ms_per_group": round(
                timings.get("parse_ms", 0.0) / max(1, timings.get("groups", 0)), 2),
            "events": timings.get("events", 0),
            "failure": state.failure,
        }
        (args.output / "monitor_summary.json").write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        push_state(args.server, args.token_file, {
            "active": False, "recording": False, "session_id": 0,
            "session": str(last_session) if last_session else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(elapsed, 3), "battle": is_battle_like(summary),
            "state": summary, "events": event_history, "timings": timings,
            "stats": stats, "summary": summary_payload,
        })
        print(f"监控结束：快照 {summary_payload['snapshots']} 张"
              f"（{summary_payload['observed_fps']} FPS），"
              f"平均抓图 {summary_payload['avg_capture_ms']} 毫秒，"
              f"平均差异比较 {summary_payload['avg_diff_ms']} 毫秒，"
              f"解析 {summary_payload['groups_parsed']} 个区域，"
              f"平均每区域 {summary_payload['avg_parse_ms_per_group']} 毫秒。")
        if last_session:
            print(f"最后一场记录目录：{last_session}")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT,
                        help="OCR 布局文件")
    parser.add_argument("--fps", type=float, default=10.0,
                        help="每秒检查多少次画面，默认 10（0.1 秒）")
    parser.add_argument("--refresh", type=float, default=10.0,
                        help="每隔多少秒强制重新解析所有区域，0 表示关闭")
    parser.add_argument("--title", default=GAME_TITLE_KEYWORD,
                        help="游戏窗口标题包含的文字，默认“洛克”")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="日志保存目录")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help="实时面板服务地址，默认 http://127.0.0.1:8000")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE,
                        help="服务器生成的实时面板令牌文件")
    parser.add_argument("--duration", type=float, default=0,
                        help="监控多少秒后自动停止；0 表示一直运行")
    parser.add_argument("--wait", type=float, default=0,
                        help="找不到游戏窗口时最多等待多少秒；0 表示立即退出")
    parser.add_argument("--self-test", action="store_true",
                        help="只截一帧并完整识别，打印结果后退出")
    parser.add_argument("--save-events", action="store_true",
                        help="发生状态变化时把当前帧保存到会话目录")
    parser.add_argument("--allow-background", action="store_true",
                        help="游戏不在前台时也继续截图（通常读不到画面）")
    args = parser.parse_args(argv)
    if not 1 <= args.fps <= 30:
        parser.error("--fps 必须在 1～30 之间")
    if args.fps < 5:
        print("提醒：低于 5 FPS 可能会漏掉短暂出现的数字。")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
