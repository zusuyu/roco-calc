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


def assess_candidates(candidates, display_name=None, min_score=0.90, min_margin=0.08):
    """名字只核对视觉候选，无法替代头像证据或消除共用头像的歧义。"""
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
    margin = checked[0]["score"] - checked[1]["score"] if len(checked) > 1 else None
    strong = bool(checked and margin is not None and
                  checked[0]["score"] >= min_score and margin >= min_margin)
    supported = bool(checked and 1 in matching_ranks and margin is not None and
                     margin >= min_margin and checked[0]["score"] >= 0.85 and
                     checked[0].get("feature_inliers", 0) >= 5)
    identity = checked[0]["pets"][0] if (strong or supported) and len(checked[0]["pets"]) == 1 else None
    if (strong or supported) and len(checked[0]["pets"]) > 1:
        status = "shared_avatar"
    elif identity:
        status = "visual_supported" if strong else "visual_name_supported"
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
        "thresholds": {"min_score": min_score, "min_margin": min_margin,
                       "name_supported_min_score": 0.85,
                       "name_supported_min_feature_inliers": 5},
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
             "feature_coverage": 0.0, "feature_score": 0.0}
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
            "feature_coverage": round(area, 2), "feature_score": round(score, 5)}


def _load_groups(manifest_path):
    pet_ids = {}
    with (ROOT / "data" / "learnsets.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            pet_ids[(row["number"], row["name"], row["form"])] = row["pet_id"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups = {}
    assets = (ROOT / "web" / "assets" / "pets").resolve()
    for pet in manifest["pets"]:
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
            })
            group["shiny"] = group["shiny"] or avatar_index == 1
            key = (pet["number"], pet["name"], pet["form"])
            if not any(
                (item["number"], item["name"], item["form"]) == key
                for item in group["pets"]
            ):
                group["pets"].append({
                    "pet_id": pet_ids.get(key), "number": pet["number"],
                    "name": pet["name"], "form": pet["form"],
                })
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

    def rank(self, image, top_k=10):
        import cv2
        import numpy as np

        if not 1 <= top_k <= 20:
            raise ValueError("top_k 必须在 1～20 之间。")
        pixels = np.array(image.convert("RGB"))
        key = hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes()).digest()
        if key in self._rank_cache:
            self._rank_cache.move_to_end(key)
            return self._rank_cache[key][:top_k]
        query16 = _features(cv2.resize(pixels, (16, 16), interpolation=cv2.INTER_AREA))
        coarse, _ = _scores(self.coarse_bank, query16)
        all_best = {}
        for variant in np.argsort(coarse)[::-1]:
            group, scale, flip = self.variants[variant]
            all_best.setdefault(group, (scale, flip, float(coarse[variant])))
        shortlisted = set(list(all_best)[:20])
        query_features = []
        for flip in (False, True):
            gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
            gray = cv2.resize(gray, None, fx=256 / max(gray.shape), fy=256 / max(gray.shape))
            if flip:
                gray = cv2.flip(gray, 1)
            keypoints, desc = self.sift.detectAndCompute(gray, None)
            points = np.float32([point.pt for point in keypoints]).reshape(-1, 2)
            query_features.append((points, desc))
            votes = {}
            if desc is not None and self.flann is not None:
                for neighbors in self.flann.knnMatch(desc, k=min(8, len(self.descriptors))):
                    seen = set()
                    for match in neighbors:
                        group = int(self.group_ids[match.trainIdx])
                        if group not in seen:
                            votes[group] = votes.get(group, 0) + 1 / (1 + match.distance / 100) ** 2
                            seen.add(group)
                shortlisted.update(sorted(votes, key=votes.get, reverse=True)[:20])
        geometries = {}
        for group in shortlisted:
            indices = self.group_indices[group]
            trials = [_geometry(points, desc, self.points[indices], self.descriptors[indices])
                      for points, desc in query_features]
            geometries[group] = max(trials, key=lambda trial: trial["feature_score"])
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
            ranked.append({
                "avatar": self.groups[group]["avatar"],
                "pets": self.groups[group]["pets"],
                "score": round(max(float(scores[index]), geometries[group]["feature_score"]), 5),
                "template_score": round(float(scores[index]), 5),
                "color_score": round(float(parts[index, 0]), 5),
                "edge_score": round(float(parts[index, 1]), 5),
                **geometries[group],
            })
        ranked.sort(key=lambda row: row["score"], reverse=True)
        self._rank_cache[key] = ranked
        if len(self._rank_cache) > 8:
            self._rank_cache.popitem(last=False)
        return ranked[:top_k]

    def match(self, image, display_name=None, min_score=0.90, min_margin=0.08):
        start = time.perf_counter()
        result = assess_candidates(self.rank(image), display_name, min_score, min_margin)
        result.update(elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
                      method="opencv_templates_and_sift",
                      indexed_avatars=len(self.groups), missing_avatars=self.missing)
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
