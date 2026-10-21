"""Shared SQL opt-out for default retrieval; no text/query heuristics.

Only JSON boolean true excludes. Missing, malformed and other JSON types retain
legacy eligibility; writers must update Document.updated_at and invalidate caches.
"""
from sqlalchemy import case, func


def retrieval_allowed(metadata):
    # SQLite CASE is lazy: malformed JSON must never reach json_type().
    flag_type = case((func.json_valid(metadata) == 1,
                      func.json_type(metadata, '$.retrieval_excluded')), else_=None)
    return func.coalesce(flag_type, '') != 'true'
