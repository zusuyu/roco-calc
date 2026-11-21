#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主服务调用独立 OCR 环境的小桥接。

主计算器仍然只依赖 Python 标准库；OCR 放在单独的虚拟环境里，通过子进程
调用。没有安装 OCR 环境时，网页只显示“未安装”，不影响伤害计算。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "configs"
MODEL_DIR = ROOT / "models" / "ocr"
UPLOAD_DIR = ROOT / ".cache" / "ocr_uploads"
OCR_FRAME = ROOT / "ocr_frame.py"
STATUS_CACHE_SECONDS = 5

_VENV_CANDIDATES = (
    ROOT / ".venv-ocr314" / "Scripts" / "python.exe",
    ROOT / ".venv-ocr314" / "bin" / "python",
    ROOT / ".venv-ocr" / "Scripts" / "python.exe",
    ROOT / ".venv-ocr" / "bin" / "python",
)


class OcrError(Exception):
    """OCR 失败；message 可以直接显示给用户。"""


class OcrUnavailable(OcrError):
    """OCR 环境或模型还没有准备好。"""


def ocr_python():
    """返回 OCR 虚拟环境的 Python；优先环境变量，方便测试或换目录。"""
    override = os.environ.get("ROCO_OCR_PYTHON")
    if override:
        path = Path(override)
        if path.is_file():
            return path
    for path in _VENV_CANDIDATES:
        if path.is_file():
            return path
    return None


def _relative(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def list_layouts():
    """列出 configs/ocr_*.json；每个布局必须有合法的 image_size。"""
    layouts = []
    if not CONFIG_DIR.is_dir():
        return layouts
    for path in sorted(CONFIG_DIR.glob("ocr_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        size = payload.get("image_size")
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(type(value) is not int or value <= 0 for value in size)
        ):
            continue
        layouts.append(
            {
                "id": path.stem,
                "label": f"{path.stem} · {size[0]}×{size[1]}",
                "path": _relative(path),
                "image_size": size,
            }
        )
    return layouts


def find_layout(layout_id):
    for layout in list_layouts():
        if layout["id"] == layout_id:
            return layout
    raise OcrError("找不到这个 OCR 布局；请刷新页面后重新选择。")


def _check_dependencies(python):
    code = "import rapidocr, onnxruntime, cv2, PIL, yaml"
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return False, message[-500:]
    return True, None


def _check_models():
    if not MODEL_DIR.is_dir():
        return False, []
    files = sorted(
        path.name for path in MODEL_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in (".onnx", ".txt", ".yml", ".yaml")
    )
    return bool(files), files


_status_cache = {"at": 0.0, "value": None}


def status(force=False):
    """返回 OCR 环境、模型、头像和布局状态；短时间缓存，避免反复子进程。"""
    now = time.monotonic()
    if (
        not force
        and _status_cache["value"] is not None
        and now - _status_cache["at"] < STATUS_CACHE_SECONDS
    ):
        return _status_cache["value"]

    python = ocr_python()
    layouts = list_layouts()
    models_ok, model_files = _check_models()
    avatar_count = 0
    avatar_dir = ROOT / "web" / "assets" / "pets"
    if avatar_dir.is_dir():
        avatar_count = sum(1 for path in avatar_dir.glob("*.png"))
    deps_ok = False
    deps_error = None
    if python is not None:
        deps_ok, deps_error = _check_dependencies(python)

    if python is None:
        message = (
            "还没有 OCR 虚拟环境。请用 Python 3.10+ 创建 .venv-ocr314，"
            "再安装 requirements-ocr.txt。"
        )
    elif not deps_ok:
        message = "OCR 虚拟环境存在，但 RapidOCR/ONNX Runtime 还没装好。"
    elif not models_ok:
        message = "OCR 模型还没下载；请在 OCR 环境运行 python download_ocr.py。"
    elif not layouts:
        message = "没有找到 OCR 布局配置。"
    elif not avatar_count:
        message = "还没有精灵头像；OCR 仍可读文字，但头像身份检索不可用。"
    else:
        message = "OCR 环境、模型、布局和头像都已就绪。"

    value = {
        "ready": bool(python and deps_ok and models_ok and layouts),
        "python": str(python) if python else None,
        "python_exists": python is not None,
        "dependencies": deps_ok,
        "dependencies_error": deps_error,
        "models": models_ok,
        "model_files": model_files[:20],
        "avatar_count": avatar_count,
        "layouts": layouts,
        "message": message,
    }
    _status_cache.update(at=now, value=value)
    return value


def save_upload(data, suffix=".png"):
    """把网页上传的图片写到 .cache，返回路径；不覆盖已有文件。"""
    if not data:
        raise OcrError("上传的图片是空的。")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
    path = UPLOAD_DIR / name
    path.write_bytes(data)
    return path


def run_frame(
    image_path,
    layout_id,
    our_pet=None,
    opponent_pet=None,
    our_form=None,
    opponent_form=None,
    debug=False,
    no_avatar=False,
    timeout=120,
):
    """调用独立 OCR 环境识别一张静态操作帧，返回 ocr_frame.py 的 JSON。"""
    python = ocr_python()
    if python is None:
        raise OcrUnavailable("还没有 OCR 虚拟环境，请先按 README 安装。")
    current = status()
    if not current["dependencies"]:
        raise OcrUnavailable("OCR 虚拟环境里的依赖还没装好。")
    if not current["models"]:
        raise OcrUnavailable("OCR 模型还没下载，请先在 OCR 环境运行 download_ocr.py。")
    layout = find_layout(layout_id)
    layout_path = (ROOT / layout["path"]).resolve()
    image_path = Path(image_path).resolve()
    if not image_path.is_file():
        raise OcrError("找不到上传的图片。")
    command = [
        str(python),
        str(OCR_FRAME),
        str(image_path),
        "--layout",
        str(layout_path),
    ]
    if our_pet:
        command += ["--our-pet", str(our_pet)]
    if our_form:
        command += ["--our-form", str(our_form)]
    if opponent_pet:
        command += ["--opponent-pet", str(opponent_pet)]
    if opponent_form:
        command += ["--opponent-form", str(opponent_form)]
    if no_avatar:
        command.append("--no-avatar")
    if debug:
        command.append("--debug")
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"OCR 超过 {timeout} 秒没有完成；请先裁小图片或检查模型。") from exc
    except OSError as exc:
        raise OcrError(f"无法启动 OCR 环境：{exc}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise OcrError(message[-1500:] or "OCR 进程失败，但没有返回原因。")
    text = (completed.stdout or "").strip()
    if not text:
        raise OcrError("OCR 没有输出 JSON。")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OcrError(f"OCR 输出的不是合法 JSON：{exc}") from exc


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(force=True), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
