#!/usr/bin/env python3
"""按固定布局输出操作帧 JSON；头像候选及昵称辅助信息与原名分开保存。

python ocr_frame.py 参考4.jpg --layout configs/ocr_reference4.json
"""

import argparse
import json
from functools import lru_cache
from pathlib import Path
import re
import time

from download_ocr import load_image, prepare_config
from ocr_skills import load_skill_pool, recognize
import roco_calc as rc
from type_icons import detect_types


def resolve_frame_region(layout, image_size):
    """同时接受纯游戏图与包含指定游戏窗口的桌面截图；不缩放 OCR 像素。"""
    expected = layout.get("image_size")
    if (not isinstance(expected, list) or len(expected) != 2 or
            not all(type(value) is int and value > 0 for value in expected)):
        raise ValueError("布局 image_size 必须为两个正整数。")
    if expected == list(image_size):
        return [0, 0, *expected]
    if layout.get("screen_size") != list(image_size):
        raise ValueError("图片尺寸与布局配置不一致，请使用对应窗口尺寸的布局。")
    box = layout.get("viewport")
    width, height = image_size
    if (not isinstance(box, list) or len(box) != 4 or
            not all(type(value) is int for value in box) or not
            (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        raise ValueError("布局 viewport 必须是屏幕内的游戏内容矩形，不包含标题栏。")
    if [box[2] - box[0], box[3] - box[1]] != expected:
        raise ValueError("viewport 的大小必须与游戏内容 image_size 一致。")
    return box


def validate_layout(layout, image_size):
    resolve_frame_region(layout, image_size)
    skills = layout.get("skills", [])
    if len(skills) != 4 or {s.get("slot") for s in skills} != {1, 2, 3, 4}:
        raise ValueError("布局必须分别配置 1～4 号技能，不能缺少或重复。")
    width, height = layout["image_size"]
    regions = [layout[side][key] for side in ("our", "opponent")
               for key in ("display_name", "energy")]
    regions += [layout[side]["avatar"] for side in ("our", "opponent")
                if "avatar" in layout[side]]
    regions += [layout[side]["avatar_match"] for side in ("our", "opponent")
                if "avatar_match" in layout[side]]
    regions += [layout[side][key] for side in ("our", "opponent")
                for key in ("hp", "hp_percent") if key in layout[side]]
    regions += [skill[key] for skill in skills
                for key in ("name", "power", "energy_cost")]
    regions += [skill["cooldown"] for skill in skills if "cooldown" in skill]
    for box in regions:
        if (len(box) != 4 or not all(type(v) is int for v in box) or not
                (0 <= box[0] < box[2] <= width and
                 0 <= box[1] < box[3] <= height)):
            raise ValueError(f"布局包含无效裁剪坐标：{box}")


def read_field(image, region, engine, numeric=False, relaxed=False, zero_like=False,
               detect_fallback=False, scale=3):
    """只在配置的字段内读取；数字必须完整匹配，不从杂乱字符串提取。

    detect_fallback 用于显示名：短名字旁边常跟着性别/职业小图标，整块识别
    会把图标读成字符；低置信度时改用文本检测框重读，通常能只框住名字。
    scale 用来放大小裁剪区；生命数字在 5 倍时更稳定。
    """
    from PIL import Image

    crop = image.crop(region)
    crop = crop.resize((crop.width * scale, crop.height * scale),
                       Image.Resampling.LANCZOS)
    result = engine(crop, use_det=False, use_cls=False)
    text = result.txts[0].strip() if result.txts else None
    confidence = float(result.scores[0]) if result.scores else None
    if detect_fallback and (confidence is None or confidence < 0.5):
        wide = (max(0, region[0] - 4), max(0, region[1] - 4),
                min(image.width, region[2] + 4),
                min(image.height, region[3] + 4))
        crop = image.crop(wide)
        crop = crop.resize((crop.width * 5, crop.height * 5),
                           Image.Resampling.LANCZOS)
        detected = engine(crop, use_det=True, use_cls=False, text_score=0.3)
        if detected.txts:
            text = detected.txts[0].strip()
            confidence = float(detected.scores[0])
    reading = {"text": text, "confidence": confidence}
    value = text if confidence is not None and confidence >= 0.5 else None
    if numeric:
        candidate = normalize_zero_like(value) if zero_like else value
        starred = re.fullmatch(r"[★☆*]\s*([0-9]{1,2})", candidate or "")
        starred_ok = bool(starred and confidence is not None and confidence >= 0.5)
        if starred_ok:
            # 能源徽标常是“星星+数字”，星星是固定 UI，数字部分可以直接采用。
            value = int(starred.group(1))
        else:
            value = (int(candidate)
                     if candidate and re.fullmatch(r"[0-9]{1,5}", candidate) else None)
        if not starred_ok and (value is None or confidence < 0.85):
            value, retries = retry_number(
                image.crop(region), engine, reading, relaxed=relaxed,
                zero_like=zero_like,
            )
            reading["retries"] = retries
    reading["status"] = "recognized" if value is not None else "unresolved"
    reading["value"] = value
    return value, reading


def clean_label(text):
    """去掉名字末尾的性别/职业图标残影和标点。"""
    return re.sub(r"[：:，,。.、—–\-?？♀♂★☆xX\s]+$", "", (text or "").strip())


# 游戏字体里的“0”是一个圆角方框，文字识别经常读成这些字符。
ZERO_GLYPHS = set("DdOoQ口回〇○◯")


def normalize_zero_like(text):
    """把能源徽标里的方形字符还原成 0；只对明确启用该规则的字段调用。"""
    if not text:
        return text
    text = text.strip()
    if all(char.isdigit() or char in ZERO_GLYPHS for char in text):
        return "".join("0" if char in ZERO_GLYPHS else char for char in text)
    return text


def has_red_glyph(image, region, threshold=0.10):
    """冷却数字是红色粗字；没有足够红色就不去 OCR，避免把花纹/闪电读成数字。"""
    import numpy as np

    crop = np.asarray(image.crop(tuple(region))).astype(np.int16)
    if crop.ndim != 3 or crop.shape[2] < 3:
        return False
    red = crop[:, :, 0]
    green = crop[:, :, 1]
    blue = crop[:, :, 2]
    mask = ((red >= 150) & (green <= 95) & (blue <= 95) &
            (red - np.maximum(green, blue) >= 60))
    return float(mask.mean()) >= threshold


def select_number(readings, min_single=0.9, zero_like=False):
    groups = {}
    for reading in readings:
        text, score = reading["text"], reading["confidence"]
        if not text:
            continue
        if zero_like:
            text = normalize_zero_like(text)
        if score >= 0.5 and re.fullmatch(r"[0-9]{1,5}", text):
            groups.setdefault(int(text), []).append(score)
    ranked = sorted(groups, key=lambda number: max(groups[number]), reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    score = max(groups[best])
    if score < 0.6 or (len(groups[best]) < 2 and score < min_single):
        return None
    if len(ranked) > 1 and score - max(groups[ranked[1]]) < 0.15:
        return None
    return best


def retry_number(crop, engine, initial_reading=None, relaxed=False, zero_like=False):
    """低置信度数字做有限次旋转、反色重读。

    zero_like 只用于能耗字段：游戏把 0 画成方框，识别结果常是 D/口/回。
    其他数字字段保持严格，不把 D/O 自动当作 0。relaxed 只用于“当前能量”
    这种值域很小、单个 10 也很可能正确的字段；技能能耗仍使用默认严格阈值，
    再由技能数据库做低置信度校核。
    """
    import cv2
    import numpy as np

    pixels = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR)
    pixels = cv2.resize(pixels, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    min_single = 0.75 if relaxed else 0.9
    readings = [dict(initial_reading)] if initial_reading else []
    # 数字徽标也可能倾斜；检测框会先做透视校正，避免直接读整块徽标。
    detected = engine(pixels, use_det=True, use_cls=False, text_score=0.3)
    for text, score in zip(detected.txts or (), detected.scores or ()):
        readings.append({"text": text.strip(), "confidence": float(score)})
    selected = select_number(readings, min_single=min_single, zero_like=zero_like)
    if selected is not None:
        return selected, readings
    # 重读一旦凑够“两次一致”或单个高置信读数就立即停止；确实很难读的
    # 数字仍然会跑完所有角度和反色变体，不能为了速度牺牲读数。
    height, width = pixels.shape[:2]
    for angle in (-15, 0, 15):
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
        rotated = cv2.warpAffine(pixels, matrix, (width, height),
                                 borderMode=cv2.BORDER_REPLICATE)
        gray = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY)
        variants = [rotated] + [255 - cv2.threshold(channel, threshold, 255,
                    cv2.THRESH_BINARY)[1] for channel, threshold in
                    ((gray, 100), (gray, 160), (rotated[:, :, 2], 160))]
        for variant in variants:
            border = 255 if variant.ndim == 2 else (255, 255, 255)
            variant = cv2.copyMakeBorder(variant, 15, 15, 15, 15,
                        cv2.BORDER_CONSTANT, value=border)
            result = engine(variant, use_det=False, use_cls=False)
            if result.txts:
                readings.append({"text": result.txts[0],
                                 "confidence": float(result.scores[0])})
                selected = select_number(readings, min_single=min_single,
                                         zero_like=zero_like)
                if selected is not None:
                    return selected, readings
    return select_number(readings, min_single=min_single,
                         zero_like=zero_like), readings


def pet_result(display_name, energy, identity=None, identity_source="provided"):
    """昵称不能用作数据库身份；未确认身份时原名、编号、形态为 null。"""
    return {
        "name": identity["name"] if identity else None,
        "number": identity["number"] if identity else None,
        "form": identity["form"] if identity else None,
        "pet_id": identity.get("pet_id") if identity else None,
        "identity_source": identity_source if identity else "unresolved",
        "display_name": display_name,
        "energy": energy,
    }


def parse_hp(text):
    match = re.fullmatch(r"\s*([0-9]{1,5})\s*/\s*([0-9]{1,5})\s*", text or "")
    if match:
        current, maximum = map(int, match.groups())
        if 0 <= current <= maximum and maximum > 0:
            return current, maximum
    return None, None


@lru_cache(maxsize=None)
def _skill_energy(name):
    if not name:
        return None
    try:
        return rc.find_skill(name).get("energy")
    except rc.RocoCalcError:
        return None


def skill_result(slot, readings, displayed_power, energy_cost,
                 energy_cost_reading=None, cooldown=None):
    """每个固定位置单独组装，缺失识别不能让后面的技能前移。

    能耗先采用画面读数；读不出时用技能数据库能耗做低置信度校核。若 OCR
    重读中确实出现过数据库数字，标记 database_supported；否则只给
    database_estimate，不冒充画面直接读数。
    """
    row = readings[0] if len(readings) == 1 else {}
    category = row.get("category")
    name = row.get("name")
    db_energy = _skill_energy(name)
    energy_status = "unresolved"
    if energy_cost is not None:
        energy_status = "recognized"
    elif db_energy is not None:
        candidates = []
        if energy_cost_reading:
            candidates.append(energy_cost_reading)
            candidates.extend(energy_cost_reading.get("retries") or [])
        matched = any(
            str(item.get("text", "")).strip() == str(db_energy)
            and float(item.get("confidence") or 0) >= 0.4
            for item in candidates
        )
        if matched:
            energy_cost = db_energy
            energy_status = "database_supported"
        else:
            energy_status = "database_estimate"
    power = displayed_power if category == "攻击" else None
    return {
        "slot": slot, "name": name, "category": category,
        "power": power, "base_power": row.get("base_power"),
        "energy_cost": energy_cost,
        "energy_cost_status": energy_status,
        "energy_cost_estimate": db_energy,
        "cooldown": cooldown,
        "name_status": row.get("status", "unresolved"),
        "power_status": ("not_applicable" if category in ("防御", "状态")
                         else "recognized" if power is not None else "unresolved"),
    }


def load_pool_for(identity):
    return (load_skill_pool(identity["name"], identity["form"])
            if identity else load_skill_pool())


def parse_identity(image, side, layout, engine, avatar_matcher=None, provided=None):
    """只解析属性图标和头像；显示名可以改，默认不读取、也不参与身份判断。"""
    label = None
    label_reading = {"status": "disabled", "text": None,
                     "confidence": None, "value": None}
    detected_types = None
    avatar = None
    identity = provided
    source = "provided" if provided else None
    if avatar_matcher is not None and "avatar" in layout[side]:
        detected_types = detect_types(image, layout[side]["avatar"])
        match_box = layout[side].get("avatar_match") or layout[side]["avatar"]
        avatar = avatar_matcher.match(
            image.crop(match_box),
            None,
            min_score=float(layout.get("avatar_min_score", 0.90)),
            min_margin=float(layout.get("avatar_min_margin", 0.08)),
            types=(detected_types or {}).get("pair") or None,
        )
        if identity is None and avatar["identity"] is not None:
            identity = avatar["identity"]
            source = "avatar"
    return {
        "label": label, "label_reading": label_reading,
        "types": detected_types, "avatar": avatar,
        "identity": identity, "source": source,
    }


def parse_status(image, side, layout, engine):
    """只解析生命和当前能量；监控在数值区域变化时调用。"""
    energy, energy_reading = read_field(
        image, layout[side]["energy"], engine, True, relaxed=True
    )
    result = {
        "energy": energy, "energy_reading": energy_reading,
        "hp": None, "max_hp": None, "hp_percent": None,
        "hp_reading": None, "percent_reading": None,
    }
    if "hp" in layout[side]:
        hp_text, hp_reading = read_field(
            image, layout[side]["hp"], engine, scale=5
        )
        hp, max_hp = parse_hp(hp_text)
        hp_reading["status"] = "recognized" if hp is not None else "unresolved"
        result.update(hp=hp, max_hp=max_hp, hp_reading=hp_reading)
    if "hp_percent" in layout[side]:
        text, percent_reading = read_field(
            image, layout[side]["hp_percent"], engine, scale=5
        )
        match = re.fullmatch(r"([0-9]{1,3})\s*%", text or "")
        percent = int(match.group(1)) if match else None
        if percent is not None and not 0 <= percent <= 100:
            percent = None
        percent_reading["status"] = ("recognized" if percent is not None
                                     else "unresolved")
        result.update(hp_percent=percent, percent_reading=percent_reading)
    return result


def parse_skill(image, region, engine, pool):
    """只解析一个技能槽；监控在该槽区域变化时调用。"""
    crop = image.crop(region["name"])
    crop = crop.resize((crop.width * 3, crop.height * 3))
    detected = recognize(crop, pool, engine, single_line=True)
    rows = detected["skill_readings"]
    category = rows[0].get("category") if len(rows) == 1 else None
    if category in ("防御", "状态"):
        power, power_reading = None, {"status": "not_applicable"}
    else:
        power, power_reading = read_field(image, region["power"], engine, True)
    cost, cost_reading = read_field(
        image, region["energy_cost"], engine, True, zero_like=True
    )
    cooldown = None
    cooldown_reading = None
    cooldown_status = "absent"
    if "cooldown" in region and has_red_glyph(image, region["cooldown"]):
        cooldown, cooldown_reading = read_field(
            image, region["cooldown"], engine, True
        )
        cooldown_status = ("recognized" if cooldown is not None
                           else "unresolved")
    result = skill_result(
        region["slot"], rows, power, cost, cost_reading, cooldown
    )
    result["cooldown_status"] = cooldown_status
    diagnostics = {
        "slot": region["slot"], **detected,
        "power": power_reading, "energy_cost": cost_reading,
        "cooldown": cooldown_reading,
    }
    return result, diagnostics


def analyze_frame(image, layout, engine, our_identity=None, opponent_identity=None,
                  debug=False, avatar_matcher=None):
    validate_layout(layout, image.size)
    source_size = image.size
    viewport = resolve_frame_region(layout, source_size)
    image = image.crop(viewport)
    output = {"schema_version": 1, "frame_type": "operation", "skills": []}
    diagnostics = {"pets": {}, "skills": []}
    for side, provided in (("our", our_identity), ("opponent", opponent_identity)):
        ident = parse_identity(image, side, layout, engine, avatar_matcher, provided)
        if side == "our":
            our_identity = ident["identity"]
        status = parse_status(image, side, layout, engine)
        output[side] = pet_result(
            ident["label"], status["energy"], ident["identity"],
            ident["source"] or "unresolved",
        )
        if ident["types"] is not None:
            output[side]["types"] = ident["types"]
        if ident["avatar"] is not None:
            output[side]["avatar_match"] = ident["avatar"]
        diagnostics["pets"][side] = {
            "display_name": ident["label_reading"],
            "energy": status["energy_reading"],
        }
        if "hp" in layout[side]:
            output[side].update(hp=status["hp"], max_hp=status["max_hp"])
            diagnostics["pets"][side]["hp"] = status["hp_reading"]
        if "hp_percent" in layout[side]:
            output[side]["hp_percent"] = status["hp_percent"]
            diagnostics["pets"][side]["hp_percent"] = status["percent_reading"]
    pool = load_pool_for(our_identity)
    for region in sorted(layout["skills"], key=lambda item: item["slot"]):
        result, skill_diagnostics = parse_skill(image, region, engine, pool)
        output["skills"].append(result)
        diagnostics["skills"].append(skill_diagnostics)
    if debug:
        diagnostics.update(source_size=list(source_size), viewport=viewport,
                           frame_size=list(image.size))
        output["debug"] = diagnostics
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--our-pet", help="已经确认的我方精灵原名或编号")
    parser.add_argument("--our-form")
    parser.add_argument("--opponent-pet", help="已经确认的敌方精灵原名或编号")
    parser.add_argument("--opponent-form")
    parser.add_argument("--debug", action="store_true", help="附加 OCR 原文和置信度")
    parser.add_argument("--no-avatar", action="store_true", help="跳过头像检索，仅运行 OCR")
    args = parser.parse_args()
    for side in ("our", "opponent"):
        if getattr(args, side + "_form") and not getattr(args, side + "_pet"):
            parser.error(f"--{side}-form 需要同时指定 --{side}-pet")
    try:
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        image = load_image(args.image)
        validate_layout(layout, image.size)
        our = rc.find_pet(args.our_pet, args.our_form) if args.our_pet else None
        opponent = rc.find_pet(args.opponent_pet, args.opponent_form) \
            if args.opponent_pet else None
    except (OSError, ValueError, KeyError, TypeError, rc.RocoCalcError) as exc:
        parser.error(str(exc))
    from rapidocr import RapidOCR
    engine = RapidOCR(config_path=str(prepare_config()))
    matcher = None
    if not args.no_avatar and any("avatar" in layout[side] for side in ("our", "opponent")):
        from avatar_match import AvatarMatcher
        matcher = AvatarMatcher()
    start = time.perf_counter()
    output = analyze_frame(image, layout, engine, our, opponent, args.debug, matcher)
    if args.debug:
        output["debug"]["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 1)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
