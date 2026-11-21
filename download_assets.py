#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载精灵头像、攻击技能图标和属性图标。

图片来自洛克王国 wiki（BWiki），网站有流量管控，所以这里：
    - 先批量查询图片地址（每批 50 个）；
    - 图片一张一张下载，不并发；
    - 每张之间等待一会儿；
    - 已存在的图片跳过，中断后可以继续；
    - 先试下少量图片，确认没有被拦截，再下全部。

用法:
    python download_assets.py --dry-run       只统计，不联网
    python download_assets.py --sample 5      试下 5 张精灵头像 + 5 张技能图标
    python download_assets.py                 下载全部
    python download_assets.py --only types    只下载 18 个属性图标
    python download_assets.py --metadata-only 只查图片地址，不下载图片

图片保存到:
    web/assets/pets/       精灵头像
    web/assets/skills/     技能图标
    web/assets/types/      属性图标
图片名单保存到:
    data/assets.json
"""

import argparse
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import crawl_pets
from type_icons import TYPE_FILES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
ASSET_DIR = WEB_DIR / "assets"
PET_DIR = ASSET_DIR / "pets"
SKILL_DIR = ASSET_DIR / "skills"
TYPE_DIR = ASSET_DIR / "types"
META_CACHE = CACHE_DIR / "assets_meta.json"
MANIFEST = DATA_DIR / "assets.json"

BASE = crawl_pets.BASE
API_URL = BASE + "/api.php"
HEADERS = dict(crawl_pets.HEADERS)
HEADERS["Accept"] = "application/json"
IMAGE_HEADERS = dict(HEADERS)
IMAGE_HEADERS["Accept"] = "image/avif,image/webp,image/png,image/*,*/*;q=0.8"

CATALOG_CACHE = CACHE_DIR / "Module_Pets_data_Catalog.lua"
SKILLS_CACHE = CACHE_DIR / "Module_Pets_data_Skills.lua"

RETRY_WAITS = (10, 30, 60)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AssetError(Exception):
    """图片下载过程中可以给用户看的错误。"""


def load_lua_module(path):
    """读取本地 Lua 数据模块。只读缓存，不会联网。"""
    if not path.exists():
        raise AssetError(
            f"缺少本地缓存 {path}。请先运行原来的爬虫生成缓存，再执行本脚本。"
        )
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    while lines and lines[0].lstrip().startswith("--"):
        lines.pop(0)
    return crawl_pets.LuaParser("\n".join(lines)).parse()


def build_pet_entries():
    """把 data/pets.csv 的每一行对应到一张头像。"""
    catalog = load_lua_module(CATALOG_CACHE)
    by_pair = {}
    for key, row in catalog.items():
        if not isinstance(row, dict):
            continue
        image = row.get("image") or {}
        filename = image.get("head")
        if not filename:
            continue
        pair = (str(row.get("number", "")), row.get("name", ""))
        by_pair.setdefault(pair, []).append(row)

    entries = []
    import csv

    with (DATA_DIR / "pets.csv").open(encoding="utf-8-sig", newline="") as handle:
        for pet in csv.DictReader(handle):
            number = pet.get("number", "")
            name = pet.get("name", "")
            form = pet.get("form", "") or ""
            candidates = by_pair.get((number, name), [])
            chosen = None
            for row in candidates:
                if (row.get("form", "") or "") == form:
                    chosen = row
                    break
            if chosen is None and candidates:
                # 少数精灵在 pets.csv 里没有写形态；使用资料表中排在最前面的基础形态。
                chosen = candidates[0]
            image = (chosen.get("image") or {}) if chosen else {}
            filename = image.get("head")
            shiny_filename = image.get("shiny_head")
            catalog_types = (chosen.get("types") or []) if chosen else []
            if isinstance(catalog_types, str):
                catalog_types = catalog_types.split("|")
            types = [value for value in catalog_types
                     if isinstance(value, str) and value]
            if not types:
                types = [value for value in (pet.get("types") or "").split("|")
                         if value]
            entries.append(
                {
                    "number": number,
                    "name": name,
                    "form": form,
                    "types": types,
                    "avatar_file": filename,
                    "avatar": f"/assets/pets/{filename}" if filename else None,
                    "shiny_avatar_file": shiny_filename,
                    "shiny_avatar": (
                        f"/assets/pets/{shiny_filename}" if shiny_filename else None
                    ),
                }
            )
    return entries


def build_skill_entries():
    """把攻击技能对应到图标。界面目前只显示攻击技能。"""
    skills = load_lua_module(SKILLS_CACHE)
    entries = {}
    for row in skills.values():
        if not isinstance(row, dict):
            continue
        if row.get("category") != "攻击":
            continue
        if row.get("damage_class") not in ("物攻", "魔攻"):
            continue
        if row.get("power") is None:
            continue
        filename = row.get("icon")
        name = row.get("name")
        if not filename or not name or name in entries:
            continue
        entries[name] = {
            "name": name,
            "icon_file": filename,
            "icon": f"/assets/skills/{filename}",
        }
    return [entries[name] for name in sorted(entries)]


def build_plan():
    pets = build_pet_entries()
    skills = build_skill_entries()
    jobs = {}
    for pet in pets:
        for filename in (pet["avatar_file"], pet.get("shiny_avatar_file")):
            if filename:
                jobs.setdefault(
                    filename,
                    {
                        "kind": "pets",
                        "filename": filename,
                        "target": PET_DIR / filename,
                    },
                )
    for skill in skills:
        filename = skill["icon_file"]
        if filename:
            jobs.setdefault(
                filename,
                {"kind": "skills", "filename": filename, "target": SKILL_DIR / filename},
            )
    for _, filename in TYPE_FILES.values():
        jobs.setdefault(
            filename,
            {"kind": "types", "filename": filename, "target": TYPE_DIR / filename},
        )
    return pets, skills, jobs


def load_meta():
    if not META_CACHE.exists():
        return {}
    try:
        return json.loads(META_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_meta(meta):
    META_CACHE.parent.mkdir(parents=True, exist_ok=True)
    META_CACHE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def request_json(url):
    """带重试的 JSON 请求。网站返回 567 时说明触发了流量管控。"""
    last_error = None
    for attempt, wait in enumerate((0,) + RETRY_WAITS):
        if wait:
            print(f"  请求暂时失败，等待 {wait} 秒后重试……")
            time.sleep(wait)
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504, 567):
                if attempt < len(RETRY_WAITS):
                    continue
                raise AssetError(
                    f"网站返回 {exc.code}，可能触发了流量管控。请等一会儿再继续。"
                ) from exc
            raise AssetError(f"查询图片地址失败：HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < len(RETRY_WAITS):
                continue
            raise AssetError(f"查询图片地址失败：{exc}") from exc
    raise AssetError(f"查询图片地址失败：{last_error}")


def normalize_file_title(title, normalized):
    """把 API 返回的“文件:xxx”标题还原成原始文件名。"""
    original = normalized.get(title)
    if original:
        return original.split(":", 1)[1].strip()
    if ":" in title:
        title = title.split(":", 1)[1]
    return title.strip().replace(" ", "_")


def query_batch(filenames):
    """查询一批图片的直链。返回 {文件名: 信息}。"""
    titles = "|".join("File:" + name for name in filenames)
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": titles,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "format": "json",
            "formatversion": "2",
        }
    )
    data = request_json(API_URL + "?" + params)
    query = data.get("query") or {}
    normalized = {
        item["to"]: item["from"]
        for item in query.get("normalized", [])
        if "to" in item and "from" in item
    }
    requested = set(filenames)
    result = {}
    for page in query.get("pages", []):
        title = page.get("title", "")
        filename = normalize_file_title(title, normalized)
        if filename not in requested:
            continue
        info = (page.get("imageinfo") or [None])[0]
        if not info or not info.get("url"):
            result[filename] = {"missing": True}
            continue
        result[filename] = {
            "url": info["url"],
            "size": info.get("size"),
            "width": info.get("width"),
            "height": info.get("height"),
            "mime": info.get("mime"),
        }
    return result


def resolve_meta(filenames, meta, delay):
    """补齐还没有查询过的图片地址，并把结果写入缓存。"""
    missing = [name for name in filenames if name not in meta]
    if not missing:
        return meta

    batch_size = 50
    batches = [missing[i : i + batch_size] for i in range(0, len(missing), batch_size)]
    print(f"查询图片地址：{len(missing)} 张，分 {len(batches)} 批……")
    for index, batch in enumerate(batches, 1):
        print(f"  [{index}/{len(batches)}] 查询 {len(batch)} 张")
        meta.update(query_batch(batch))
        save_meta(meta)
        if index < len(batches):
            time.sleep(delay)
    return meta


def read_png_size(path):
    """读取 PNG 宽高，用来确认下载到的确实是图片。"""
    data = path.read_bytes()[:24]
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def download_one(job, info, delay):
    """下载一张图片。返回 (状态, 说明)。状态是 ok / skipped / failed。"""
    target = job["target"]
    filename = job["filename"]
    if target.exists() and target.stat().st_size > 0:
        return "skipped", None
    if info.get("missing"):
        return "failed", "wiki 上找不到这张图"
    url = info.get("url")
    if not url:
        return "failed", "没有图片地址"

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    last_error = None
    for attempt, wait in enumerate((0,) + RETRY_WAITS):
        if wait:
            print(f"  下载暂时失败，等待 {wait} 秒后重试……")
            time.sleep(wait)
        try:
            request = urllib.request.Request(url, headers=IMAGE_HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
            if not body:
                raise AssetError("下载到空文件")
            if not body.startswith(PNG_SIGNATURE):
                raise AssetError("下载到的内容不是 PNG 图片")
            if info.get("size") and len(body) != info["size"]:
                print(
                    f"  提醒：{filename} 的大小和 wiki 记录不同"
                    f"（{len(body)} 字节，记录 {info['size']} 字节），仍按下载内容保存。"
                )
            temp.write_bytes(body)
            os.replace(temp, target)
            size = read_png_size(target)
            if size is None:
                return "failed", "保存后检查不是有效 PNG"
            return "ok", f"{size[0]}×{size[1]}"
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504, 567):
                if attempt < len(RETRY_WAITS):
                    continue
                return "failed", f"HTTP {exc.code}，可能触发了流量管控"
            return "failed", f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, AssetError) as exc:
            last_error = exc
            if attempt < len(RETRY_WAITS):
                continue
            return "failed", str(exc)
    return "failed", str(last_error)


def write_manifest(pets, skills):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pets": [
            {
                "number": pet["number"],
                "name": pet["name"],
                "form": pet["form"],
                "types": pet.get("types") or [],
                "avatar": pet["avatar"],
                "shiny_avatar": pet.get("shiny_avatar"),
            }
            for pet in pets
        ],
        "skills": [
            {"name": skill["name"], "icon": skill["icon"]}
            for skill in skills
        ],
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"图片名单已写入 {MANIFEST.relative_to(ROOT)}")


def select_jobs(jobs, args):
    """根据 --sample / --limit / --only 选择这次要下载的图片。"""
    pet_jobs = [job for job in jobs.values() if job["kind"] == "pets"]
    skill_jobs = [job for job in jobs.values() if job["kind"] == "skills"]
    type_jobs = [job for job in jobs.values() if job["kind"] == "types"]

    if args.only == "pets":
        selected = pet_jobs
    elif args.only == "skills":
        selected = skill_jobs
    elif args.only == "types":
        selected = type_jobs
    elif args.sample:
        selected = pet_jobs[: args.sample] + skill_jobs[: args.sample]
    else:
        selected = pet_jobs + skill_jobs + type_jobs

    if args.limit:
        selected = selected[: args.limit]
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="下载洛克王国精灵头像和攻击技能图标（限速、可续传）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不联网")
    parser.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="试下 N 张精灵头像和 N 张技能图标",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="这次最多下载 N 张，适合分批下载",
    )
    parser.add_argument(
        "--only",
        choices=("pets", "skills", "types"),
        help="只处理精灵头像、技能图标或属性图标",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="只查图片地址，不下载图片",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="每次请求之间等待的秒数，默认 0.6",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="已经存在的图片也重新下载",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="即使只试下少量图片，也生成完整图片名单",
    )
    args = parser.parse_args(argv)

    pets, skills, jobs = build_plan()
    pet_files = {job["filename"] for job in jobs.values() if job["kind"] == "pets"}
    skill_files = {job["filename"] for job in jobs.values() if job["kind"] == "skills"}
    type_files = {job["filename"] for job in jobs.values() if job["kind"] == "types"}
    print(
        f"精灵 {len(pets)} 条，对应头像 {len(pet_files)} 张；"
        f"技能 {len(skills)} 条，对应图标 {len(skill_files)} 张；"
        f"属性图标 {len(type_files)} 张；"
        f"去重后共 {len(jobs)} 张图片。"
    )

    if args.dry_run:
        print("干跑模式：没有联网，也没有下载。")
        print("前 5 只精灵：")
        for pet in pets[:5]:
            print(f"  {pet['number']} {pet['name']} -> {pet['avatar'] or '无头像'}")
        print("前 5 个技能：")
        for skill in skills[:5]:
            print(f"  {skill['name']} -> {skill['icon']}")
        return 0

    selected = select_jobs(jobs, args)
    if args.force:
        for job in selected:
            if job["target"].exists():
                job["target"].unlink()
    print(f"本次准备处理 {len(selected)} 张图片。")

    meta = load_meta()
    meta = resolve_meta([job["filename"] for job in selected], meta, args.delay)
    if args.metadata_only:
        print("只查地址模式：没有下载图片。")
        if args.write_manifest:
            write_manifest(pets, skills)
        return 0

    results = {"ok": 0, "skipped": 0, "failed": 0}
    failures = []
    fatal = False
    for index, job in enumerate(selected, 1):
        if fatal:
            break
        info = meta.get(job["filename"], {"missing": True})
        status, note = download_one(job, info, args.delay)
        results[status] += 1
        if status == "failed":
            failures.append((job["filename"], note))
            print(f"  [{index}/{len(selected)}] 失败：{job['filename']}（{note}）")
            if note and ("流量管控" in note or "HTTP 429" in note):
                fatal = True
        elif status == "ok":
            detail = f"，{note}" if note else ""
            print(f"  [{index}/{len(selected)}] 下载：{job['filename']}{detail}")
        if index < len(selected) and status != "skipped":
            time.sleep(args.delay)

    if not args.sample or args.write_manifest:
        write_manifest(pets, skills)

    print(
        f"结束：成功 {results['ok']} 张，跳过 {results['skipped']} 张，"
        f"失败 {results['failed']} 张。"
    )
    if failures:
        print("失败清单：")
        for filename, note in failures:
            print(f"  {filename}：{note}")
        if fatal:
            print("因为可能触发了流量管控，本次提前停止。请等一段时间后再继续。")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssetError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
