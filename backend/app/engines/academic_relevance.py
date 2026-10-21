"""Optional model-bound relevance floor for explicitly selected academic KBs.

This is local calibration, not a probability of correctness or a domain detector.
Other libraries/models retain the existing retrieval behavior.
"""
from __future__ import annotations
import math


def academic_similarity_floor(retrieval, kb_id, model_identity):
    """Return the configured floor only for a matching library and model.

    A missing policy is backward-compatible. A configured but invalid policy
    fails explicitly instead of silently presenting uncalibrated thresholds.
    """
    if not isinstance(retrieval, dict):
        raise ValueError('retrieval must be a mapping')
    policy = retrieval.get('academic_relevance')
    if policy is None:
        return None
    ids = retrieval.get('academic_kb_ids', [])
    if not isinstance(ids, list) or any(type(v) is not int or v < 1 for v in ids):
        raise ValueError('retrieval.academic_kb_ids must be positive integer IDs')
    if kb_id not in ids:
        return None
    if not isinstance(policy, dict):
        raise ValueError('retrieval.academic_relevance must be a mapping')
    bound_model = policy.get('model_identity')
    if not isinstance(bound_model, str) or not bound_model.strip():
        raise ValueError('academic relevance requires an exact model_identity')
    value = policy.get('min_similarity')
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError('academic min_similarity must be a finite number from 0 to 1')
    return float(value) if model_identity == bound_model else None
