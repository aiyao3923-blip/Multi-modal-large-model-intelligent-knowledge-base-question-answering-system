"""可选本地 Embedding：仅加载显式配置且已存在的本地模型，绝不自动下载。"""
from __future__ import annotations
import os
import threading
from pathlib import Path
from ..core.config import settings

_model = None
_model_path = None
_onnx_model = None
_onnx_key = None
_lock = threading.Lock()

def _load_local_model():
    global _model, _model_path
    configured = os.environ.get('APP_EMBEDDING_MODEL_DIR') or settings.get('embedding', 'model_path', default='')
    if not configured:
        return None
    target = Path(configured).expanduser()
    if not target.is_absolute():
        target = settings.root_dir / target
    if not target.is_dir():
        return None
    with _lock:
        if _model_path == str(target):
            return _model
        _model_path = str(target)
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(str(target), local_files_only=True)
        except Exception:
            _model = None
        return _model

def _load_onnx_model():
    global _onnx_model, _onnx_key
    configured = os.environ.get('APP_EMBEDDING_MODEL_DIR') or settings.get('embedding', 'model_path', default='')
    if not configured:
        return None
    from .onnx_embedder import OnnxE5, local_root, model_stamp
    root = local_root(configured, settings.root_dir)
    threads = settings.get('embedding', 'threads', default=4)
    batch_size = settings.get('embedding', 'batch_size', default=8)
    with _lock:
        key = (str(root), threads, batch_size, model_stamp(root))
        if _onnx_model is not None and key == _onnx_key:
            return _onnx_model
        model = OnnxE5(root, threads=threads, batch_size=batch_size)
        _onnx_model, _onnx_key = model, (str(root), threads, batch_size, model.stamp)
        return model

def get_embedding_identity() -> str | None:
    """Lazily verify/load ONNX identity; legacy/unconfigured vectors stay untagged.

    Changed file stamps force SHA verification and reload; invalid ONNX settings
    raise rather than silently presenting an old model's identity or vectors.
    """
    if settings.get('embedding', 'backend', default='sentence_transformers') == 'onnx_e5':
        model = _load_onnx_model()
        return model.identity if model is not None else None
    return None

def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if settings.get('embedding', 'backend', default='sentence_transformers') == 'onnx_e5':
        model = _load_onnx_model()
        return model.encode(texts, prefix='passage: ') if model is not None else None
    model = _load_local_model()
    if model is None:
        return None
    try:
        return [v.tolist() for v in model.encode(texts, normalize_embeddings=True, show_progress_bar=False)]
    except Exception:
        return None

def embed_query(text: str) -> list[float] | None:
    if settings.get('embedding', 'backend', default='sentence_transformers') == 'onnx_e5':
        model = _load_onnx_model()
        return model.encode([text], prefix='query: ')[0] if model is not None else None
    vectors = embed_texts([text])
    return vectors[0] if vectors else None
