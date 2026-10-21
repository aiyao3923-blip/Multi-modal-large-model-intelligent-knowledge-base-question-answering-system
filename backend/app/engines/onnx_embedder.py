"""Local-only multilingual E5: verified bytes, bounded windows, masked mean/L2."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat

import numpy as np

DIMENSION, MAX_TOKENS = 384, 512
MODEL_FILES = ('onnx/model_quantized.onnx', 'tokenizer.json')
# Xenova/multilingual-e5-small@761b726dd34fb83930e26aab4e9ac3899aa1fa78.
# Pin the approved self-contained graph: ORT can follow external-data paths even
# for in-memory models, so a caller-supplied manifest alone is not a trust anchor.
PINNED_SHA256 = {
    MODEL_FILES[0]: 'f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193',
    MODEL_FILES[1]: '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',
}
ALGORITHM = 'e5-prefix-maskmean-l2-charwindows-v1'


def local_root(configured, backend: Path) -> Path:
    path = Path(configured).expanduser()
    if '..' in path.parts or str(path).startswith(('\\\\', '//')):
        raise ValueError('ONNX model must use a local, non-traversing path')
    if not path.is_absolute():
        if path.drive or path.root:
            raise ValueError('Ambiguous ONNX model path')
        path = Path(backend) / path
    return path.absolute()


def model_stamp(root: Path) -> tuple:
    """Inspect only the three named files; never follow symlinks/junctions."""
    stamps = []
    for name in ('model_manifest.json', *MODEL_FILES):
        path = root / name
        for component in (path, *path.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Linked ONNX model paths are not allowed')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('ONNX model assets must be regular, unlinked files')
        stamps.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return tuple(stamps)



def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    if not np.isfinite(vectors).all() or not np.isfinite(norms).all() or (norms <= 1e-12).any():
        raise ValueError('ONNX embeddings must be finite and nonzero')
    return vectors / norms


class OnnxE5:
    def __init__(self, root: Path, *, threads: int = 4, batch_size: int = 8):
        if any(type(value) is not int or value < 1 for value in (threads, batch_size)):
            raise ValueError('ONNX threads and batch_size must be positive integers')
        self.stamp = model_stamp(root)
        manifest = json.loads((root / 'model_manifest.json').read_text(encoding='utf-8-sig'))
        if not isinstance(manifest, dict):
            raise ValueError('ONNX manifest must be an object')
        hashes = manifest.get('sha256')
        if manifest.get('dimension') != DIMENSION or not isinstance(hashes, dict) or set(hashes) != set(MODEL_FILES):
            raise ValueError('ONNX manifest requires dimension 384 and exactly the two model hashes')
        expected = {'max_tokens': MAX_TOKENS, 'model_file': MODEL_FILES[0], 'tokenizer_file': MODEL_FILES[1]}
        if any(manifest.get(key, value) != value for key, value in expected.items()):
            raise ValueError('Unsupported E5 token limit or model file names')
        data = {name: (root / name).read_bytes() for name in MODEL_FILES}
        actual = {name: hashlib.sha256(value).hexdigest() for name, value in data.items()}
        if any(not isinstance(hashes[name], str) or hashes[name].lower() != actual[name] for name in MODEL_FILES):
            raise ValueError('ONNX model/tokenizer SHA256 mismatch')
        if actual != PINNED_SHA256:
            raise ValueError('ONNX assets are not the approved multilingual-e5-small revision')
        identity = json.dumps([ALGORITHM, DIMENSION, MAX_TOKENS, 'passage: ', 'query: ', actual], sort_keys=True).encode('utf-8')
        self.identity = 'onnx_e5:' + ALGORITHM + ':' + hashlib.sha256(identity).hexdigest()
        # Optional dependencies are imported only after local files pass validation.
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_str(data[MODEL_FILES[1]].decode('utf-8'))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.pad_id = self.tokenizer.token_to_id('<pad>')
        if self.pad_id is None:
            raise ValueError('E5 tokenizer is missing <pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        # Consume the same pinned bytes that were hashed, not mutable file paths.
        self.session = ort.InferenceSession(data[MODEL_FILES[0]], sess_options=options, providers=['CPUExecutionProvider'])
        self.inputs = {item.name for item in self.session.get_inputs()}
        if not {'input_ids', 'attention_mask'} <= self.inputs or self.inputs - {'input_ids', 'attention_mask', 'token_type_ids'}:
            raise ValueError('Unsupported E5 ONNX inputs')
        if 'last_hidden_state' not in {item.name for item in self.session.get_outputs()}:
            raise ValueError('E5 ONNX must expose last_hidden_state')
        if model_stamp(root) != self.stamp:
            raise ValueError('ONNX assets changed during loading; retry with stable files')
        self.batch_size = batch_size

    def _windows(self, text: str, prefix: str):
        if not isinstance(text, str):
            raise TypeError('Embedding inputs must be strings')
        # One full tokenization; subsequent encodes only touch bounded windows,
        # not successively shorter copies of the whole remaining document.
        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets
        budget = MAX_TOKENS - len(self.tokenizer.encode(prefix).ids) - 4
        if budget < 1:
            raise ValueError('E5 prefix exceeds the token budget')
        start = token = 0
        while start < len(text) or (start == 0 and not text):
            while token < len(offsets) and offsets[token][1] <= start:
                token += 1
            stop = min(token + budget, len(offsets))
            end = len(text) if stop == len(offsets) else max(start + 1, offsets[stop - 1][1])
            if not start <= end <= len(text):
                raise ValueError('Invalid tokenizer character offsets')
            encoded = self.tokenizer.encode(prefix + text[start:end])
            # Boundary retokenization can add tokens. Halving is bounded by
            # log2(window characters), guarantees progress and never drops a tail.
            while len(encoded.ids) > MAX_TOKENS:
                if end - start <= 1:
                    raise ValueError('A single character exceeds the E5 token limit')
                end = start + (end - start) // 2
                encoded = self.tokenizer.encode(prefix + text[start:end])
            weight = sum(not char.isspace() for char in text[start:end])
            yield encoded, weight
            if end == len(text):
                break
            start = end

    def _infer(self, encodings):
        lengths = [len(item.ids) for item in encodings]
        if not lengths or min(lengths) < 1 or max(lengths) > MAX_TOKENS:
            raise ValueError('Invalid ONNX token shape')
        shape = (len(encodings), max(lengths))
        ids = np.full(shape, self.pad_id, dtype=np.int64)
        mask = np.zeros(shape, dtype=np.int64)
        types = np.zeros(shape, dtype=np.int64)
        for row, (item, length) in enumerate(zip(encodings, lengths)):
            ids[row, :length], mask[row, :length] = item.ids, item.attention_mask
            if 'token_type_ids' in self.inputs:
                types[row, :length] = item.type_ids
        feed = {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': types}
        hidden = np.asarray(self.session.run(['last_hidden_state'], {key: feed[key] for key in self.inputs})[0])
        if hidden.shape != (*shape, DIMENSION) or not np.isfinite(hidden).all():
            raise ValueError('Invalid ONNX last_hidden_state shape or values')
        counts = mask.sum(axis=1, keepdims=True)
        if (counts < 1).any():
            raise ValueError('ONNX attention mask cannot be empty')
        mean = (hidden * mask[..., None]).sum(axis=1, dtype=np.float64) / counts
        return _unit(mean)

    def encode(self, texts: list[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        sums = np.zeros((len(texts), DIMENSION), dtype=np.float64)
        batches, owners, weights = [], [], []

        def flush():
            for owner, weight, vector in zip(owners, weights, self._infer(batches)):
                sums[owner] += weight * vector
            batches.clear(); owners.clear(); weights.clear()

        for index, text in enumerate(texts):
            blank = isinstance(text, str) and not text.strip()
            for encoded, weight in self._windows(text, prefix):
                batches.append(encoded); owners.append(index); weights.append(1 if blank else weight)
                if len(batches) == self.batch_size:
                    flush()
        if batches:
            flush()
        return _unit(sums).tolist()
