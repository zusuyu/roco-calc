#!/usr/bin/env python3
"""实时监控游戏窗口：定时截图、解析双方状态、记录变化。

只在 OCR 虚拟环境里运行（需要 mss / rapidocr / opencv）：
    .venv-ocr314\\Scripts\\python.exe live_monitor.py
    .venv-ocr314\\Scripts\\python.exe live_monitor.py --self-test

工具只截取游戏窗口内容，不读内存、不注入、不修改游戏。截图在内存里处理，
默认只在识别出状态变化时写日志；--save-frames 可以把每张识别用的图留档。
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_LAYOUT = ROOT / "configs" / "ocr_1602x932_window.json"
DEFAULT_OUTPUT = ROOT / ".cache" / "live"
GAME_TITLE_KEYWORD = "洛克"
BROWSER_TITLE_PARTS = ("Microsoft Edge", "Google Chrome", "浏览器", "Firefox")
CONTENT_SIZES = {(1600, 900), (1602, 932), (2094, 1179)}


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
        # 优先已知的游戏内容尺寸，其次优先面积大的窗口。
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


def label_of(side):
    if not side:
        return "未识别"
    name = side.get("name")
    display = side.get("display")
    if name and display and name != display:
        return f"{name}（{display}）"
    return name or display or "未识别"


def state_summary(result):
    """从 ocr_frame.analyze_frame 的结果里取出实时监控需要的字段。"""
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
    """菜单/赛季页面没有战斗 HUD；没有这些字段时不记日志。"""
    for key in ("our", "opponent"):
        side = summary.get(key) or {}
        if any(side.get(field) is not None
               for field in ("name", "display", "hp", "hp_percent", "energy")):
            return True
    return any(skill.get("name") for skill in summary.get("skills") or [])


def diff_states(previous, current):
    """对比两次识别结果，返回人话事件；null 不当作 0。"""
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
        # 用“确认的原名”判断换人；显示名偶尔读丢不应被当成换精灵。
        old_key = old.get("name") or old.get("display")
        new_key = new.get("name") or new.get("display")
        if old_key and new_key and old_key != new_key:
            events.append(f"{prefix}换精灵：{old_label} → {new_label}")
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


def capture_client(area):
    """截取游戏客户区，返回 PIL RGB 图片（不写硬盘）。"""
    import mss
    from PIL import Image

    box = {"left": area.left, "top": area.top,
           "width": area.width, "height": area.height}
    with mss.MSS() as sct:
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.rgb)


def load_ocr():
    import logging

    from download_ocr import prepare_config
    from rapidocr import RapidOCR

    # 实时监控时不要让每帧的“检测结果为空”警告刷满控制台。
    logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    return RapidOCR(config_path=str(prepare_config()))


def load_matcher():
    from avatar_match import AvatarMatcher

    return AvatarMatcher()


def session_folder(output):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(output) / f"session_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run(args):
    import os

    from windows_capture import WindowsAPI
    from ocr_frame import analyze_frame, resolve_frame_region, validate_layout

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
    try:
        resolve_frame_region(layout, (area.width, area.height))
    except ValueError as exc:
        print(f"这个窗口尺寸和当前布局不匹配：{exc}")
        print("请告诉我窗口内容尺寸，我再加对应布局。")
        return 2

    print("正在加载 OCR 模型和头像索引……")
    engine = load_ocr()
    matcher = load_matcher()
    print("模型加载完成，进入监控。按 Ctrl+C 停止。")

    folder = session_folder(args.output) if not args.self_test else None
    log_path = folder / "state.jsonl" if folder else None
    previous = None
    counts = {"frames": 0, "ocr": 0, "battle": 0, "events": 0, "errors": 0}
    started = time.perf_counter()
    last_wait_notice = 0.0
    try:
        while True:
            elapsed = time.perf_counter() - started
            if args.duration and elapsed >= args.duration:
                break
            if not api.user.IsWindow(hwnd):
                print("游戏窗口已关闭，结束监控。")
                break
            if not args.allow_background and (
                    not api.foreground(hwnd) or api.user.IsIconic(hwnd)):
                if time.perf_counter() - last_wait_notice > 5:
                    print("游戏不在前台，暂停识别；切回游戏后自动继续。")
                    last_wait_notice = time.perf_counter()
                time.sleep(0.5)
                continue
            try:
                area = api.area(hwnd)
                image = capture_client(area)
                counts["frames"] += 1
                result = analyze_frame(image, layout, engine,
                                       avatar_matcher=matcher)
                counts["ocr"] += 1
                summary = state_summary(result)
                battle = is_battle_like(summary)
                events = diff_states(previous, summary) if battle else []
                if battle:
                    counts["battle"] += 1
                    counts["events"] += len(events)
                    previous = summary
                    print(f"[{elapsed:6.1f}s] {format_state(summary)}")
                    for event in events:
                        print(f"           · {event}")
                    if log_path is not None:
                        record = {
                            "elapsed_s": round(elapsed, 3),
                            "captured_at": datetime.now(timezone.utc).isoformat(),
                            "state": summary,
                            "events": events,
                        }
                        with log_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                if args.save_frames and folder is not None:
                    image.save(folder / f"frame_{counts['frames']:06d}.png")
                if args.self_test:
                    print(json.dumps(summary, ensure_ascii=False, indent=1))
                    break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                counts["errors"] += 1
                print(f"这一帧识别失败：{exc}")
            if args.self_test:
                break
            delay = max(0.0, args.interval - (time.perf_counter() - started - elapsed))
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n收到停止信号。")
    finally:
        if folder is not None:
            summary_path = folder / "summary.json"
            summary_path.write_text(json.dumps({
                "layout": str(args.layout),
                "window": window["title"].strip(),
                "client_size": [area.width, area.height],
                "elapsed_s": round(time.perf_counter() - started, 3),
                **counts,
            }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"日志目录：{folder}")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT,
                        help="OCR 布局文件")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="每隔多少秒识别一次，默认 2 秒")
    parser.add_argument("--title", default=GAME_TITLE_KEYWORD,
                        help="游戏窗口标题包含的文字，默认“洛克”")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="日志保存目录")
    parser.add_argument("--duration", type=float, default=0,
                        help="监控多少秒后自动停止；0 表示一直运行")
    parser.add_argument("--wait", type=float, default=0,
                        help="找不到游戏窗口时最多等待多少秒；0 表示立即退出")
    parser.add_argument("--self-test", action="store_true",
                        help="只截一帧并识别，打印结果后退出")
    parser.add_argument("--save-frames", action="store_true",
                        help="把每张用于识别的截图也保存下来，便于排查")
    parser.add_argument("--allow-background", action="store_true",
                        help="游戏不在前台时也继续截图（通常读不到画面）")
    args = parser.parse_args(argv)
    if args.interval < 0.5:
        parser.error("--interval 不能小于 0.5 秒")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
