#!/usr/bin/env python3
"""固定区域头像检索：视觉 Top 10 与显示名分开评估，不以昵称改写视觉排序。"""

import argparse
from collections import OrderedDict
import csv
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
CACHE_VERSION = 3
SCALES = (0.85, 1.0, 1.15)


def _pet_key(pet):
    return (pet.get("number"), pet.get("name"), pet.get("form"))


def _candidate_key(candidate):
    return tuple(sorted(_pet_key(pet) for pet in candidate.get("pets", ())))


def assess_candidates(candidates, display_name=None, min_score=0.90, min_margin=0.08,
                      type_info=None):
    """名字只核对视觉候选，无法替代头像证据或消除共用头像的歧义。

    同一只精灵的普通/异色头像会分成两个候选，所以分差要跟“下一只不同的
    精灵”比，而不是跟同一只精灵的另一张头像比。属性识别可信时，候选池已经
    被属性缩小，可以用更低的分数门槛确认。
    """
    label = (display_name or "").strip()
    checked = []
    matching_ranks = []
    for rank, candidate in enumerate(candidates, 1):
        row = dict(candidate)
        matches = [p for p in candidate["pets"] if label and p["name"] == label]
        row.update(rank=rank, name_match=bool(matches), name_matches=matches)
        checked.append(row)
        if matches:
            matching_ranks.append(rank)
    top_key = _candidate_key(checked[0]) if checked else None
    runner = next((row for row in checked[1:]
                   if _candidate_key(row) != top_key), None)
    margin = (checked[0]["score"] - runner["score"]
              if checked and runner is not None else None)
    strong = bool(checked and margin is not None and
                  checked[0]["score"] >= min_score and margin >= min_margin)
    supported = bool(checked and 1 in matching_ranks and margin is not None and
                     margin >= min_margin and checked[0]["score"] >= 0.85 and
                     checked[0].get("feature_inliers", 0) >= 5)
    type_applied = bool(type_info and type_info.get("applied"))
    type_pets = int(type_info.get("pets") or 0) if type_applied else 0
    type_single = bool(type_applied and type_pets == 1 and checked and
                       checked[0]["score"] >= 0.45)
    type_lenient = bool(type_applied and 1 < type_pets <= 8 and checked and
                        checked[0]["score"] >= 0.45 and
                        margin is not None and margin >= 0.08)
    # 同一精灵的进化形态（例如水灵/圣水守护）长得很像，特征点分数会一起
    # 封顶，最终分差很小；这时用精细色块分和特征点数量的差距补充判断。
    template_gap = (checked[0].get("template_score", 0)
                    - runner.get("template_score", 0)
                    if runner is not None else None)
    feature_gap = (checked[0].get("feature_inliers", 0)
                   - runner.get("feature_inliers", 0)
                   if runner is not None else None)
    # 线条完全相同、只有颜色不同的形态（如岚鸟的四季样子）在特征点和最终
    # 分上都会打平；用对齐后的头像颜色相关性补充判断。
    top_color = checked[0].get("aligned_color_score") if checked else None
    runner_color = runner.get("aligned_color_score") if runner is not None else None
    color_gap = (top_color - runner_color
                 if top_color is not None and runner_color is not None else None)
    detail_template = bool(
        checked and margin is not None and margin < min_margin and
        checked[0]["score"] >= 0.85 and
        template_gap is not None and template_gap >= 0.15 and
        feature_gap is not None and feature_gap >= 5)
    detail_color = bool(
        checked and margin is not None and margin < min_margin and
        checked[0]["score"] >= 0.85 and
        color_gap is not None and color_gap >= 0.12)
    detail = detail_template or detail_color
    confirmed = strong or supported or detail or type_single or type_lenient
    identity = (checked[0]["pets"][0]
                if confirmed and len(checked[0]["pets"]) == 1 else None)
    if confirmed and len(checked[0]["pets"]) > 1:
        status = "shared_avatar"
    elif identity:
        status = ("visual_supported" if strong else
                  "visual_detail_supported" if detail else
                  "visual_type_supported" if (type_single or type_lenient) else
                  "visual_name_supported")
    elif matching_ranks:
        status = "name_supported_candidate"
    else:
        status = "needs_review"
    name_check = ("not_read" if not label else "matches_best" if 1 in matching_ranks
                  else "matches_other_candidate" if matching_ranks else "not_in_top10")
    return {
        "status": status, "identity": identity,
        "requires_review": identity is None,
        "display_name": display_name,
        "name_check": name_check,
        "name_match_ranks": matching_ranks,
        "name_conflict": bool(strong and matching_ranks and 1 not in matching_ranks),
        "score_margin": round(margin, 5) if margin is not None else None,
        "detail_evidence": {
            "used": detail,
            "used_template": detail_template,
            "used_color": detail_color,
            "template_gap": round(template_gap, 5) if template_gap is not None else None,
            "feature_gap": feature_gap,
            "color_gap": round(color_gap, 5) if color_gap is not None else None,
        },
        "type_evidence": {
            "applied": type_applied,
            "pets": type_pets,
            "used": bool(type_single or type_lenient),
        },
        "thresholds": {"min_score": min_score, "min_margin": min_margin,
                       "name_supported_min_score": 0.85,
                       "name_supported_min_feature_inliers": 5,
                       "detail_min_template_gap": 0.15,
                       "detail_min_feature_gap": 5,
                       "detail_min_color_gap": 0.12,
                       "type_supported_min_score": 0.45,
                       "type_supported_min_margin": 0.08,
                       "type_supported_max_pets": 8},
        "candidates": checked,
    }


def _features(pixels):
    import cv2
    import numpy as np

    rgb = pixels.astype(np.float32) / 255
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gradients = [cv2.Sobel(gray, cv2.CV_32F, dx, dy, ksize=3) / 4
                 for dx, dy in ((1, 0), (0, 1))]
    return np.concatenate((rgb, np.stack(gradients, axis=-1)), axis=-1)


def _bank(values, masks):
    import numpy as np

    values = np.asarray(values, dtype=np.float32)
    masks = np.asarray(masks, dtype=np.float32)
    count = masks.sum(axis=1).clip(1)
    mean = np.einsum("npc,np->nc", values, masks) / count[:, None]
    centered = (values - mean[:, None, :]) * masks[:, :, None]
    energy = np.stack(((centered[:, :, :3] ** 2).sum(axis=(1, 2)),
                       (centered[:, :, 3:] ** 2).sum(axis=(1, 2))), axis=1)
    return centered, masks, count, energy


def _scores(bank, query):
    import numpy as np

    centered, masks, count, energy = bank
    query = query.reshape(-1, 5)
    sums = np.einsum("np,pc->nc", masks, query)
    squares = np.einsum("np,pc->nc", masks, query ** 2)
    variance = np.maximum(squares - sums ** 2 / count[:, None], 0)
    query_energy = np.stack((variance[:, :3].sum(axis=1),
                             variance[:, 3:].sum(axis=1)), axis=1)
    dot = np.einsum("npc,pc->nc", centered, query)
    dot = np.stack((dot[:, :3].sum(axis=1), dot[:, 3:].sum(axis=1)), axis=1)
    correlations = dot / np.sqrt(np.maximum(energy * query_energy, 1e-10))
    correlations = np.clip(correlations, -1, 1)
    # 分数是相关系数组合，不是正确概率。
    total = 0.55 * correlations[:, 0] + 0.45 * correlations[:, 1]
    return total, correlations


@lru_cache(maxsize=64)
def _cached_rgba(path, modified_ns, byte_size):
    import numpy as np
    from PIL import Image

    with Image.open(path) as source:
        return np.array(source.convert("RGBA"))


def _rgba(path):
    stat = path.stat()
    return _cached_rgba(path, stat.st_mtime_ns, stat.st_size)


def _template(path, size, scale, flip=False, offset=(0, 0)):
    import cv2
    import numpy as np
    rgba = _rgba(path)
    y, x = np.where(rgba[:, :, 3] > 32)
    if len(x) == 0:
        raise ValueError(f"头像完全透明：{path}")
    rgba = rgba[y.min():y.max() + 1, x.min():x.max() + 1]
    if flip:
        rgba = cv2.flip(rgba, 1)
    ratio = size * 0.9 * scale / max(rgba.shape[:2])
    resized = cv2.resize(rgba, (max(1, round(rgba.shape[1] * ratio)),
                              max(1, round(rgba.shape[0] * ratio))),
                         interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size, 4), dtype=np.uint8)
    height, width = resized.shape[:2]
    left = (size - width) // 2 + offset[0]
    top = (size - height) // 2 + offset[1]
    x1, y1, x2, y2 = max(0, left), max(0, top), min(size, left + width), min(size, top + height)
    if x1 < x2 and y1 < y2:
        canvas[y1:y2, x1:x2] = resized[y1 - top:y2 - top, x1 - left:x2 - left]
    mask = (canvas[:, :, 3] > 220).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8))
    # 两侧属性图标通常覆盖头像左下方；不将这些位置用于匹配。
    mask[round(size * 0.4):, :round(size * 0.38)] = 0
    return _features(canvas[:, :, :3]).reshape(-1, 5), mask.reshape(-1)


def _geometry(query_points, query_descriptors, points, descriptors):
    import cv2
    import math
    import numpy as np

    empty = {"feature_inliers": 0, "feature_matches": 0,
             "feature_coverage": 0.0, "feature_score": 0.0, "_matrix": None}
    if query_descriptors is None or len(descriptors) < 2:
        return empty
    pairs = cv2.BFMatcher().knnMatch(query_descriptors, descriptors, k=2)
    good = sorted((m for m, n in pairs if m.distance < 0.8 * n.distance),
                  key=lambda match: match.distance)
    unique, source_used, query_used = [], set(), set()
    for match in good:
        source = tuple(np.round(points[match.trainIdx] / 3).astype(int))
        target = tuple(np.round(query_points[match.queryIdx] / 3).astype(int))
        if source not in source_used and target not in query_used:
            unique.append(match)
            source_used.add(source)
            query_used.add(target)
    if len(unique) < 3:
        return {**empty, "feature_matches": len(unique)}
    source = np.float32([points[m.trainIdx] for m in unique])
    target = np.float32([query_points[m.queryIdx] for m in unique])
    matrix, inliers = cv2.estimateAffinePartial2D(
        source, target, method=cv2.RANSAC, ransacReprojThreshold=4)
    if matrix is None:
        return empty
    scale = math.hypot(matrix[0, 0], matrix[0, 1])
    angle = abs(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
    selected = inliers.ravel().astype(bool)
    count = int(selected.sum())
    area = cv2.contourArea(cv2.convexHull(source[selected])) if count >= 3 else 0
    score = 0.0
    if 0.3 < scale < 3.5 and angle < 35 and count >= 4 and area >= 100:
        score = min(0.99, 0.7 + 0.025 * min(count, 10) + 0.05 * count / len(unique))
    return {"feature_inliers": count if score else 0, "feature_matches": len(unique),
            "feature_coverage": round(area, 2), "feature_score": round(score, 5),
            "_matrix": matrix}


def _aligned_color(query_rgb, path, matrix):
    """先用特征点矩阵把头图对齐，再在头像像素上比颜色。

    线条完全相同的季节/颜色变体在特征点分数上会一起满分；只有对齐后的
    颜色相关性能区分它们。
    """
    import cv2
    import numpy as np

    if matrix is None or query_rgb is None:
        return None
    rgba = _rgba(path)
    template = cv2.resize(rgba, (256, 256), interpolation=cv2.INTER_AREA)
    height, width = query_rgb.shape[:2]
    warped = cv2.warpAffine(template[:, :, :3].astype(np.float32), matrix,
                            (width, height))
    alpha = cv2.warpAffine(template[:, :, 3].astype(np.float32), matrix,
                           (width, height))
    mask = alpha > 200
    if int(mask.sum()) < 100:
        return None
    template_values = warped[mask]
    query_values = query_rgb[mask].astype(np.float32)
    template_centered = template_values - template_values.mean(axis=0)
    query_centered = query_values - query_values.mean(axis=0)
    denominator = np.sqrt((template_centered ** 2).sum(axis=0) *
                          (query_centered ** 2).sum(axis=0))
    correlations = (template_centered * query_centered).sum(axis=0) / \
        np.maximum(denominator, 1e-6)
    return round(float(np.clip(correlations, -1, 1).mean()), 5)


def _pet_types():
    """pets.csv 里的属性，作为名单中没有 types 时的后备。"""
    types = {}
    path = ROOT / "data" / "pets.csv"
    if not path.is_file():
        return types
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            values = [value for value in (row.get("types") or "").split("|") if value]
            if values:
                types[(row["number"], row["name"], row["form"])] = values
    return types


def _load_groups(manifest_path):
    pet_ids = {}
    with (ROOT / "data" / "learnsets.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            pet_ids[(row["number"], row["name"], row["form"])] = row["pet_id"]
    fallback_types = _pet_types()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups = {}
    assets = (ROOT / "web" / "assets" / "pets").resolve()
    for pet in manifest["pets"]:
        key = (pet["number"], pet["name"], pet["form"])
        pet_types = pet.get("types")
        if not isinstance(pet_types, list) or not pet_types:
            pet_types = fallback_types.get(key, [])
        for avatar_index, avatar in enumerate(
            (pet.get("avatar"), pet.get("shiny_avatar"))
        ):
            if not avatar:
                continue
            path = (ROOT / "web" / avatar.lstrip("/")).resolve()
            if not path.is_relative_to(assets):
                raise ValueError("头像名单包含图片目录以外的路径。")
            group = groups.setdefault(avatar, {
                "avatar": avatar, "path": path, "pets": [], "shiny": False,
                "types": set(),
            })
            group["shiny"] = group["shiny"] or avatar_index == 1
            if not any(
                (item["number"], item["name"], item["form"]) == key
                for item in group["pets"]
            ):
                group["pets"].append({
                    "pet_id": pet_ids.get(key), "number": pet["number"],
                    "name": pet["name"], "form": pet["form"],
                    "types": list(pet_types),
                })
                group["types"].add(tuple(pet_types))
    return list(groups.values())


class AvatarMatcher:
    """启动时建立小图索引；每次先筛候选，再对少量候选精排。"""

    def __init__(self, manifest_path=None, cache_path=None):
        import cv2
        import numpy as np

        self.sift = cv2.SIFT_create(nfeatures=160, contrastThreshold=0.012, edgeThreshold=15)
        self.groups = _load_groups(Path(manifest_path or ROOT / "data" / "assets.json"))
        self.missing = [group["avatar"] for group in self.groups if not group["path"].is_file()]
        self.groups = [group for group in self.groups if group["path"].is_file()]
        if len(self.groups) < 2:
            raise ValueError("本地头像不足，请先运行 download_assets.py --only pets。")
        self.cache_path = Path(cache_path or ROOT / ".cache" / "avatar_index.npz")
        signature = [(g["avatar"], g["path"].stat().st_size,
                      g["path"].stat().st_mtime_ns) for g in self.groups]
        fingerprint = hashlib.sha256(json.dumps([CACHE_VERSION, signature]).encode()).hexdigest()
        self.variants = [(group, scale, flip) for group in range(len(self.groups))
                         for scale in SCALES for flip in (False, True)]
        loaded = False
        if self.cache_path.is_file():
            try:
                with np.load(self.cache_path, allow_pickle=False) as cache:
                    if str(cache["fingerprint"]) == fingerprint:
                        self.coarse_bank = tuple(cache[k] for k in
                                                  ("centered", "masks", "count", "energy"))
                        self.points = cache["points"]
                        self.descriptors = cache["descriptors"]
                        self.group_ids = cache["group_ids"]
                        loaded = True
            except (OSError, ValueError, KeyError):
                pass
        if not loaded:
            features, masks = [], []
            for group, scale, flip in self.variants:
                feature, mask = _template(self.groups[group]["path"], 16, scale, flip)
                features.append(feature)
                masks.append(mask)
            self.coarse_bank = _bank(features, masks)
            points, descriptors, group_ids = [], [], []
            for group, entry in enumerate(self.groups):
                rgba = cv2.resize(_rgba(entry["path"]), (256, 256))
                gray = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
                keypoints, desc = self.sift.detectAndCompute(gray, rgba[:, :, 3])
                if desc is not None:
                    points.extend(point.pt for point in keypoints)
                    descriptors.extend(desc)
                    group_ids.extend([group] * len(desc))
            self.points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
            self.descriptors = np.asarray(descriptors, dtype=np.float32).reshape(-1, 128)
            self.group_ids = np.asarray(group_ids, dtype=np.int32)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            with temp.open("wb") as handle:
                np.savez_compressed(handle, fingerprint=fingerprint,
                                    points=self.points, descriptors=self.descriptors,
                                    group_ids=self.group_ids,
                                    **dict(zip(("centered", "masks", "count", "energy"),
                                               self.coarse_bank)))
            temp.replace(self.cache_path)
        self.flann = None
        if len(self.descriptors) >= 2:
            self.flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
            self.flann.add([self.descriptors])
            self.flann.train()
        self.group_indices = [np.flatnonzero(self.group_ids == group)
                              for group in range(len(self.groups))]
        self._rank_cache = OrderedDict()

    def _type_filter(self, types):
        """按识别出的属性组合缩小头像候选；找不到就回退到全部头像。"""
        info = {"requested": list(types) if types else None, "applied": False,
                "groups": 0, "pets": 0, "order_flipped": False}
        if not types:
            return info, None

        def matching(order):
            want = tuple(order)
            return {group for group, entry in enumerate(self.groups)
                    if want in entry["types"]}

        pool = matching(types)
        if not pool and len(types) == 2 and types[0] != types[1]:
            pool = matching(list(reversed(types)))
            info["order_flipped"] = bool(pool)
        if not pool:
            return info, None
        info.update(applied=True, groups=len(pool),
                    pets=len({_pet_key(pet) for group in pool
                              for pet in self.groups[group]["pets"]}))
        return info, pool

    def _rank(self, image, top_k=10, types=None):
        import cv2
        import numpy as np

        if not 1 <= top_k <= 20:
            raise ValueError("top_k 必须在 1～20 之间。")
        type_info, allowed = self._type_filter(types)
        pixels = np.array(image.convert("RGB"))
        key = hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes() +
                             repr(tuple(types or ())).encode()).digest()
        if key in self._rank_cache:
            self._rank_cache.move_to_end(key)
            ranked, cached_info = self._rank_cache[key]
            return ranked[:top_k], dict(cached_info)
        query16 = _features(cv2.resize(pixels, (16, 16), interpolation=cv2.INTER_AREA))
        coarse, _ = _scores(self.coarse_bank, query16)
        all_best = {}
        for variant in np.argsort(coarse)[::-1]:
            group, scale, flip = self.variants[variant]
            all_best.setdefault(group, (scale, flip, float(coarse[variant])))
        ordered_groups = sorted(all_best, key=lambda group: all_best[group][2],
                                reverse=True)
        if allowed is not None:
            shortlisted = {group for group in ordered_groups[:40] if group in allowed}
        else:
            shortlisted = set(ordered_groups[:20])
        query_features = []
        query_colors = []
        for flip in (False, True):
            gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
            factor = 256 / max(gray.shape)
            gray = cv2.resize(gray, None, fx=factor, fy=factor)
            color = cv2.resize(pixels, (gray.shape[1], gray.shape[0]),
                               interpolation=cv2.INTER_AREA)
            if flip:
                gray = cv2.flip(gray, 1)
                color = cv2.flip(color, 1)
            keypoints, desc = self.sift.detectAndCompute(gray, None)
            points = np.float32([point.pt for point in keypoints]).reshape(-1, 2)
            query_features.append((points, desc))
            query_colors.append(color)
            votes = {}
            if desc is not None and self.flann is not None:
                for neighbors in self.flann.knnMatch(desc, k=min(8, len(self.descriptors))):
                    seen = set()
                    for match in neighbors:
                        group = int(self.group_ids[match.trainIdx])
                        if group not in seen:
                            votes[group] = votes.get(group, 0) + 1 / (1 + match.distance / 100) ** 2
                            seen.add(group)
                voted = sorted(votes, key=votes.get, reverse=True)
                if allowed is not None:
                    voted = [group for group in voted if group in allowed]
                shortlisted.update(voted[:20])
        geometries = {}
        for group in shortlisted:
            indices = self.group_indices[group]
            trials = [_geometry(points, desc, self.points[indices], self.descriptors[indices])
                      for points, desc in query_features]
            best_index = max(range(len(trials)),
                             key=lambda index: trials[index]["feature_score"])
            geometries[group] = dict(trials[best_index])
            geometries[group]["_query_index"] = best_index
        ordered = sorted(shortlisted, key=lambda group: max(
            all_best[group][2], geometries[group]["feature_score"]), reverse=True)[:20]
        best = {group: all_best[group][:2] for group in ordered}
        features, masks, variants = [], [], []
        for group, (scale, flip) in best.items():
            for fine_scale in (scale - 0.08, scale, scale + 0.08):
                for dx in (-2, 0, 2):
                    for dy in (-2, 0, 2):
                        feature, mask = _template(self.groups[group]["path"], 32,
                                                  fine_scale, flip, (dx, dy))
                        features.append(feature)
                        masks.append(mask)
                        variants.append(group)
        query32 = _features(cv2.resize(pixels, (32, 32), interpolation=cv2.INTER_AREA))
        scores, parts = _scores(_bank(features, masks), query32)
        ranked = []
        for group in best:
            indices = np.flatnonzero(np.asarray(variants) == group)
            index = indices[np.argmax(scores[indices])]
            coarse_score = float(all_best[group][2])
            base = max(float(scores[index]), geometries[group]["feature_score"])
            # 炫彩/变色头像的颜色块分数比精细比对更稳；属性已经缩小候选池时，
            # 把粗粒度色块分也计入最终分。
            final = max(base, coarse_score) if type_info["applied"] else base
            geometry = geometries[group]
            aligned_color = _aligned_color(
                query_colors[geometry.get("_query_index", 0)],
                self.groups[group]["path"], geometry.get("_matrix"))
            ranked.append({
                "avatar": self.groups[group]["avatar"],
                "pets": self.groups[group]["pets"],
                "score": round(final, 5),
                "template_score": round(float(scores[index]), 5),
                "coarse_score": round(coarse_score, 5),
                "color_score": round(float(parts[index, 0]), 5),
                "edge_score": round(float(parts[index, 1]), 5),
                "aligned_color_score": aligned_color,
                **{key: value for key, value in geometry.items()
                   if not key.startswith("_")},
            })
        ranked.sort(key=lambda row: (row["score"],
                                     row.get("aligned_color_score") or 0),
                    reverse=True)
        self._rank_cache[key] = (ranked, dict(type_info))
        if len(self._rank_cache) > 8:
            self._rank_cache.popitem(last=False)
        return ranked[:top_k], dict(type_info)

    def rank(self, image, top_k=10, types=None):
        return self._rank(image, top_k, types)[0]

    def match(self, image, display_name=None, min_score=0.90, min_margin=0.08,
              types=None):
        start = time.perf_counter()
        candidates, type_info = self._rank(image, 10, types)
        result = assess_candidates(candidates, display_name, min_score, min_margin,
                                   type_info)
        result.update(elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
                      method="opencv_templates_and_sift",
                      indexed_avatars=len(self.groups), missing_avatars=self.missing,
                      type_filter=type_info)
        return result


def main():
    from download_ocr import load_image

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--crop", type=int, nargs=4, required=True,
                        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--display-name", help="OCR 读取的画面显示名，仅用于核对")
    args = parser.parse_args()
    try:
        image = load_image(args.image, args.crop)
        matcher = AvatarMatcher()
        result = matcher.match(image, args.display_name)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
