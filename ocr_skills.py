#!/usr/bin/env python3
"""识别裁剪区域内的技能名，保留 OCR 原文并用精灵技能池校对。

python ocr_skills.py 参考4.jpg --crop 230 390 540 980
python ocr_skills.py 参考2.jpg --crop 210 335 415 750 --scale 3 --pet 花衣蝶
"""

import argparse
import csv
from functools import lru_cache
import json
from pathlib import Path
import re
import time

import roco_calc as rc
from download_ocr import ROOT, load_image, prepare_config


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


@lru_cache(maxsize=64)
def _load_skill_pool_cached(pet_name, form):
    pet = rc.find_pet(pet_name, form) if pet_name else None
    with (ROOT / "data" / "learnsets.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    if pet:
        rows = [row for row in rows if
                (row["number"], row["name"], row["form"]) ==
                (pet["number"], pet["name"], pet["form"])]
    pool = {}
    for row in rows:
        if row["category"] in ("攻击", "防御", "状态"):
            pool[row["skill_name"]] = {
                "name": row["skill_name"], "category": row["category"],
                "base_power": int(row["power"]) if row["power"] else None,
            }
    if not pool:
        raise ValueError("没有找到可用的技能池；请核对精灵和形态。")
    return pool


def load_skill_pool(pet_name=None, form=None):
    """技能池只读且按精灵缓存；实时监控不要每帧重读 learnsets.csv。"""
    return _load_skill_pool_cached(pet_name, form)


def match_skill(text, confidence, pool):
    """仅纠正低置信度、同字数、单字差异且候选唯一的结果。"""
    text = re.sub(r"\s+", "", text)
    if text in pool:
        if confidence < 0.5:
            return {"status": "unresolved", "name": None,
                    "suggestions": [text]}
        return {"status": "exact", "name": text, "suggestions": []}
    distances = sorted((edit_distance(text, name), name) for name in pool)
    best = distances[0][0]
    nearest = [name for distance, name in distances if distance == best]
    suggestions = nearest[:5] if best <= 2 else []
    if (0.5 <= confidence < 0.9 and len(text) >= 2 and best == 1 and
            len(nearest) == 1 and len(nearest[0]) == len(text)):
        return {"status": "corrected", "name": nearest[0],
                "suggestions": suggestions}
    return {"status": "unresolved", "name": None, "suggestions": suggestions}


def rotate_text(pixels, angle, background=255):
    """旋转时扩展画布，避免倾斜文字或多位数字在边缘被截断。"""
    import cv2
    import math

    height, width = pixels.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = math.ceil(width * cosine + height * sine)
    new_height = math.ceil(height * cosine + width * sine)
    matrix[0, 2] += (new_width - width) / 2
    matrix[1, 2] += (new_height - height) / 2
    border = background if pixels.ndim == 2 else (background,) * pixels.shape[2]
    return cv2.warpAffine(pixels, matrix, (new_width, new_height),
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def resolve_readings(readings, pool):
    accepted, suggestions = [], set()
    for reading in readings:
        matched = match_skill(reading["text"], reading["confidence"], pool)
        suggestions.update(matched["suggestions"])
        if matched["name"]:
            accepted.append((reading, matched))
    exact = [item for item in accepted if item[1]["status"] == "exact"]
    choices = exact or accepted
    names = {item[1]["name"] for item in choices}
    first = readings[0] if readings else {"text": None, "confidence": None}
    row = {"raw_text": first["text"], "raw_confidence": first["confidence"],
           "status": "unresolved", "name": None, "readings": readings,
           "suggestions": sorted(suggestions)}
    if len(names) == 1:
        reading, matched = max(choices, key=lambda item: item[0]["confidence"])
        row.update(pool[matched["name"]])
        row.update(status=matched["status"], recognition_text=reading["text"],
                   recognition_confidence=reading["confidence"])
    elif len(names) > 1:
        row["suggestions"] = sorted(names)
    return row


def recognize(image, pool, engine, single_line=False):
    import cv2
    import numpy as np
    from rapidocr.utils.process_img import get_rotate_crop_image

    pixels = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    result = engine(pixels, use_det=True, use_cls=False, text_score=0.3)
    skills, other = [], []
    for text, score, box in zip(result.txts or (), result.scores or (),
                                result.boxes if result.boxes is not None else ()):
        raw = {"text": text, "confidence": float(score)}
        if not re.search(r"[\u4e00-\u9fff]{2}", text):
            other.append(raw)
            continue
        readings = [raw]
        # 检测框提供倾斜角度；对低置信度名称额外校正、补边和二值化。
        if score < 0.9:
            points = np.asarray(box, dtype=np.float32)
            center = points.mean(axis=0)
            points = center + (points - center) * 1.08
            points[:, 0] = np.clip(points[:, 0], 0, pixels.shape[1] - 1)
            points[:, 1] = np.clip(points[:, 1], 0, pixels.shape[0] - 1)
            crop = get_rotate_crop_image(pixels, points)
            crop = cv2.resize(crop, None, fx=2, fy=2,
                              interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            variants = [crop] + [cv2.threshold(gray, threshold, 255,
                        cv2.THRESH_BINARY)[1] for threshold in (100, 140, 180)]
            for variant in variants:
                border = 255 if variant.ndim == 2 else (255, 255, 255)
                variant = cv2.copyMakeBorder(variant, 8, 8, 8, 8,
                            cv2.BORDER_CONSTANT, value=border)
                retry = engine(variant, use_det=False, use_cls=False)
                if retry.txts:
                    readings.append({"text": retry.txts[0],
                                     "confidence": float(retry.scores[0])})
        skills.append(resolve_readings(readings, pool))
    if single_line and not any(row["name"] for row in skills):
        readings = [reading for row in skills for reading in row["readings"]]
        for angle in (0, -15, -30, 15):
            crop = rotate_text(pixels, angle)
            crop = cv2.copyMakeBorder(crop, 12, 12, 12, 12,
                                     cv2.BORDER_CONSTANT, value=(255, 255, 255))
            retry = engine(crop, use_det=False, use_cls=False)
            if retry.txts:
                readings.append({"text": retry.txts[0],
                                 "confidence": float(retry.scores[0])})
        if readings:
            skills = [resolve_readings(readings, pool)]
    return {"skill_readings": skills, "other_readings": other}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--crop", type=int, nargs=4, required=True,
                        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--pet", help="已确认的精灵原名或编号；省略时使用全局技能池")
    parser.add_argument("--form", help="精灵形态")
    args = parser.parse_args()
    if args.form and not args.pet:
        parser.error("--form 需要同时指定 --pet")
    try:
        pool = load_skill_pool(args.pet, args.form)
        image = load_image(args.image, args.crop, args.scale)
    except (ValueError, OSError, rc.RocoCalcError) as exc:
        parser.error(str(exc))
    from rapidocr import RapidOCR
    engine = RapidOCR(config_path=str(prepare_config()))
    start = time.perf_counter()
    output = recognize(image, pool, engine)
    output.update(image=str(args.image), crop=args.crop, pet=args.pet,
                  pool_size=len(pool), elapsed_ms=round(
                      (time.perf_counter() - start) * 1000, 1))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
