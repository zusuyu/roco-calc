#!/usr/bin/env python3
"""识别战斗画面头像左下角的属性图标。

图标位置相对头像框固定（1602×932 和 2094×1179 布局实测一致）：
- 主属性图标在下方，圆心约在头像框 (left + 0.13w, top + 0.76h)；
- 副属性图标在上方，圆心约在头像框 (left - 0.005w, top + 0.50h)；
- 单属性精灵只有下方一个图标。

识别方法：在头像框左下的小区域里，对 wiki 的 18 个属性图标做圆形遮挡的
逐像素平方差比对（同时尝试左右镜像），取分数最低、并且和第二名拉开差距的
属性。不依赖神经网络，也不读取游戏内存。
"""

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TYPE_DIR = ROOT / "web" / "assets" / "types"

# 编号与 wiki 文件名固定；2 号是普通系，7 号在游戏里没有对应属性。
TYPE_FILES = {
    2: ("普通系", "Type_2.png"),
    3: ("草系", "Type_3.png"),
    4: ("火系", "Type_4.png"),
    5: ("水系", "Type_5.png"),
    6: ("光系", "Type_6.png"),
    8: ("地系", "Type_8.png"),
    9: ("冰系", "Type_9.png"),
    10: ("龙系", "Type_10.png"),
    11: ("电系", "Type_11.png"),
    12: ("毒系", "Type_12.png"),
    13: ("虫系", "Type_13.png"),
    14: ("武系", "Type_14.png"),
    15: ("翼系", "Type_15.png"),
    16: ("萌系", "Type_16.png"),
    17: ("幽系", "Type_17.png"),
    18: ("恶系", "Type_18.png"),
    19: ("机械系", "Type_19.png"),
    20: ("幻系", "Type_20.png"),
}

# 下方是主属性，上方是副属性。
LOWER_OFFSET = (0.13, 0.76)
UPPER_OFFSET = (-0.005, 0.50)

# 搜索范围按头像框高度缩放；分数越低越像。
SEARCH_PAD_RATIO = 0.12
RADIUS_RANGE = (0.10, 0.22)
MIN_RADIUS = 6
MAX_RADIUS = 20
# 实测：正确图标分数约 100～1300，空位在 8000 以上，所以 3000 是安全分界。
MAX_SCORE = 3000.0
MIN_GAP = 300.0


def slot_centers(avatar_box):
    """返回主/副属性图标的预期圆心（画面坐标）。"""
    left, top, right, bottom = avatar_box
    width, height = right - left, bottom - top
    return {
        "lower": (round(left + LOWER_OFFSET[0] * width),
                  round(top + LOWER_OFFSET[1] * height)),
        "upper": (round(left + UPPER_OFFSET[0] * width),
                  round(top + UPPER_OFFSET[1] * height)),
    }


def search_region(avatar_box, frame_size):
    """返回覆盖两个图标、并留出移动余量的裁剪区域。"""
    left, top, right, bottom = avatar_box
    width, height = right - left, bottom - top
    x0 = max(0, left - max(20, round(0.30 * width)))
    x1 = min(frame_size[0], left + max(40, round(0.60 * width)))
    y0 = max(0, top + round(0.20 * height))
    y1 = min(frame_size[1], bottom + round(0.22 * height))
    return [x0, y0, x1, y1]


def radii(height):
    low = max(MIN_RADIUS, round(RADIUS_RANGE[0] * height))
    high = min(MAX_RADIUS, round(RADIUS_RANGE[1] * height))
    return range(low, high + 1)


def decide(scores):
    """从 {属性编号: 分数} 里选出最低分并判断是否足够可信。"""
    if not scores:
        return None, None, None, False
    ranking = sorted(scores.items(), key=lambda item: item[1])
    number, best = ranking[0]
    gap = ranking[1][1] - best if len(ranking) > 1 else float("inf")
    detected = best < MAX_SCORE and gap >= MIN_GAP
    return number, best, gap, detected


@lru_cache(maxsize=1)
def load_templates():
    """读取本地属性图标；缺少图片时返回空字典，属性识别自动关闭。"""
    import cv2
    import numpy as np

    templates = {}
    for number, (_, filename) in TYPE_FILES.items():
        path = TYPE_DIR / filename
        if not path.is_file():
            continue
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
            continue
        alpha = raw[:, :, 3] > 32
        ys, xs = np.where(alpha)
        if len(xs) == 0:
            continue
        templates[number] = raw[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return templates


def available():
    return len(load_templates()) >= 2


def _empty(status="unavailable", reason=None):
    result = {"status": status, "primary": None, "secondary": None, "pair": [],
              "slots": {"lower": None, "upper": None}}
    if reason:
        result["reason"] = reason
    return result


def _detect_slot(roi, origin, center, templates, height):
    import cv2
    import numpy as np

    x0, y0 = origin
    cx, cy = center[0] - x0, center[1] - y0
    pad = max(4, round(height * SEARCH_PAD_RATIO))
    scored = {}
    locations = {}
    for number, template in templates.items():
        top = (float("inf"), None)
        for flipped in (template, cv2.flip(template, 1)):
            for radius in radii(height):
                diameter = radius * 2
                px0 = max(0, cx - radius - pad)
                py0 = max(0, cy - radius - pad)
                px1 = min(roi.shape[1], cx + radius + pad)
                py1 = min(roi.shape[0], cy + radius + pad)
                if px1 - px0 < diameter + 2 * pad or py1 - py0 < diameter + 2 * pad:
                    continue
                resized = cv2.resize(
                    flipped, (diameter, diameter), interpolation=cv2.INTER_AREA
                )[:, :, :3]
                mask = np.zeros((diameter, diameter), np.float32)
                cv2.circle(mask, (radius, radius), int(radius * 0.86), 1, -1)
                result = cv2.matchTemplate(
                    roi[py0:py1, px0:px1], resized, cv2.TM_SQDIFF, mask=mask
                )
                score = float(result.min()) / (float(mask.sum()) * 3)
                if score < top[0]:
                    _, _, location, _ = cv2.minMaxLoc(result)
                    top = (score, (px0 + location[0] + radius,
                                   py0 + location[1] + radius, radius))
        scored[number] = top[0]
        locations[number] = top[1]

    number, best, gap, detected = decide(scored)
    name = TYPE_FILES[number][0] if number is not None else None
    location = locations.get(number) if number is not None else None
    return {
        "type": name if detected else None,
        "candidate": name,
        "score": round(best, 1) if best is not None else None,
        "gap": round(gap, 1) if gap not in (None, float("inf")) else None,
        "detected": detected,
        "center": [x0 + location[0], y0 + location[1]] if location else None,
        "radius": location[2] if location else None,
    }


def detect_types(image, avatar_box, templates=None):
    """识别头像框对应的主/副属性。

    image 是已经裁成游戏内容的 PIL 图片；avatar_box 是同一坐标系下的
    [left, top, right, bottom]。templates 只给测试注入用，正常从本机图片加载。
    """
    import numpy as np

    box = [int(value) for value in avatar_box]
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        return _empty("unresolved", "头像框无效")
    templates = load_templates() if templates is None else templates
    if len(templates) < 2:
        return _empty("unavailable", "本机还没有属性图标")

    region = search_region(box, image.size)
    if region[2] <= region[0] or region[3] <= region[1]:
        return _empty("unresolved", "属性图标区域超出画面")
    # PIL 是 RGB，cv2.imread 得到的是 BGR；统一成 BGR 再比对。
    pixels = np.asarray(image.crop(tuple(region)).convert("RGB"))[:, :, ::-1].copy()
    centers = slot_centers(box)
    height = box[3] - box[1]
    origin = (region[0], region[1])
    lower = _detect_slot(pixels, origin, centers["lower"], templates, height)
    upper = _detect_slot(pixels, origin, centers["upper"], templates, height)
    primary = lower["type"]
    secondary = upper["type"] if primary else None
    if primary and secondary:
        status = "recognized"
    elif primary:
        status = "primary_only"
    else:
        status = "unresolved"
    pair = [value for value in (primary, secondary) if value]
    return {
        "status": status,
        "primary": primary,
        "secondary": secondary,
        "pair": pair,
        "slots": {"lower": lower, "upper": upper},
    }
