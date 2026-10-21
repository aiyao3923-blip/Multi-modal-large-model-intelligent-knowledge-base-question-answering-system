"""可选本地图片 OCR；必须显式启用，未配置时不加载引擎、不下载模型。"""
from __future__ import annotations
import os
from pathlib import Path
from . import ParseError, Part


def _try_paddle(path: Path) -> str | None:
    # PaddleOCR 2.x 本地模型接口。三个模型目录都完整时才允许初始化。
    model_dirs = {name: os.environ.get("APP_PADDLE_" + name.upper() + "_MODEL_DIR", "") for name in ("det", "rec", "cls")}
    for value in model_dirs.values():
        if not value:
            return None
        folder = Path(value).expanduser()
        if not folder.is_dir() or not (folder / "inference.pdiparams").is_file() or not (folder / "inference.pdmodel").is_file():
            return None
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False, **{name + "_model_dir": str(Path(value).expanduser().resolve()) for name, value in model_dirs.items()})
        result = ocr.ocr(str(path), cls=True)
        return "\n".join(item[1][0] for block in result or [] for item in block or [] if item and len(item) >= 2 and item[1]) or None
    except Exception:
        return None


def _try_tesseract(path: Path) -> str | None:
    try:
        import pytesseract
        from PIL import Image
        with Image.open(path) as image:
            text = pytesseract.image_to_string(image, lang="chi_sim+eng", timeout=30)
        return text.strip() or None
    except Exception:
        return None


class ImageParser:
    def parse(self, path: Path) -> list[Part]:
        engine = os.environ.get("APP_OCR_ENGINE", "none").strip().lower()
        if engine not in ("paddle", "tesseract"):
            raise ParseError("图片 OCR 未启用；请先配置本地引擎并设置 APP_OCR_ENGINE，或上传已提取的文字文档")
        text = _try_paddle(path) if engine == "paddle" else _try_tesseract(path)
        if not text:
            raise ParseError("本地 OCR 未就绪或未识别到文字；请检查引擎、语言包与本地模型，系统不会自动下载模型")
        return [Part(text=text, kind="image_text", meta={"ocr": True, "engine": engine})]
