"""文档版本与解析修订：伴随元数据表保留 documents.id 的物理版本语义。

这里只声明模型，不进行建表、回填或调用模型。现有库必须经显式迁移启用。
"""
from __future__ import annotations
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from ..session import Base
from .entity import _now

class SchemaMigration(Base):
    __tablename__ = 'schema_migrations'
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    baseline_json: Mapped[str] = mapped_column(Text, default='{}')

class DocumentSeries(Base):
    __tablename__ = 'document_series'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    title: Mapped[str] = mapped_column(String(255))
    business_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    state: Mapped[str] = mapped_column(String(16), default='active', server_default='active')
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (
        UniqueConstraint('kb_id', 'business_key', name='uq_series_business_key'),
        CheckConstraint("state IN ('active','archived')", name='ck_series_state'),
        CheckConstraint('revision >= 1', name='ck_series_revision'),
    )

class DocumentVersion(Base):
    __tablename__ = 'document_versions'
    # This is exactly documents.id, not a newly allocated version identifier.
    version_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey('document_series.id'), index=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    version_label: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    publication_state: Mapped[str] = mapped_column(String(16), default='draft', server_default='draft')
    validity: Mapped[str] = mapped_column(String(16), default='unknown', server_default='unknown')
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicability_json: Mapped[str] = mapped_column(Text, default='{}', server_default='{}')
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey('documents.id'), nullable=True)
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('0'))
    coexistence_note: Mapped[str] = mapped_column(Text, default='', server_default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (
        UniqueConstraint('series_id', 'version_label', name='uq_series_version_label'),
        CheckConstraint("publication_state IN ('draft','published','withdrawn','archived')", name='ck_version_publication'),
        CheckConstraint("validity IN ('unknown','known')", name='ck_version_validity'),
        CheckConstraint("validity != 'unknown' OR (effective_from IS NULL AND effective_to IS NULL)", name='ck_version_unknown_dates'),
        CheckConstraint('effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to', name='ck_version_interval'),
        CheckConstraint('supersedes_id IS NULL OR supersedes_id != version_id', name='ck_version_not_self_superseded'),
        CheckConstraint('metadata_revision >= 1', name='ck_version_metadata_revision'),
        CheckConstraint('is_legacy IN (0,1)', name='ck_version_legacy_flag'),
        CheckConstraint('length(sha256) = 64', name='ck_version_sha256'),
        # Legacy duplicate rows are retained; new concurrent duplicates must not create fake versions.
        Index('uq_new_version_content', 'kb_id', 'sha256', unique=True, sqlite_where=text('is_legacy = 0')),
    )

class ParseRevision(Base):
    __tablename__ = 'parse_revisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey('parse_tasks.id'), nullable=True, unique=True)
    parser_version: Mapped[str] = mapped_column(String(120))
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default='')
    source_integrity: Mapped[str] = mapped_column(String(24), default='unverified', server_default='unverified')
    state: Mapped[str] = mapped_column(String(24), default='building', server_default='building')
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('0'))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    error_message: Mapped[str] = mapped_column(Text, default='', server_default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        CheckConstraint("state IN ('building','success','failed','legacy_empty')", name='ck_parse_revision_state'),
        CheckConstraint("source_integrity IN ('verified','legacy_unverified','unverified')", name='ck_parse_source_integrity'),
        CheckConstraint("is_current = 0 OR state = 'success'", name='ck_parse_current_success'),
        CheckConstraint('chunk_count >= 0', name='ck_parse_chunk_count'),
        CheckConstraint("state != 'success' OR (chunk_count > 0 AND length(content_hash) = 64 AND source_integrity IN ('verified','legacy_unverified'))", name='ck_parse_success_evidence'),
        CheckConstraint("source_integrity != 'verified' OR (file_sha256 IS NOT NULL AND length(file_sha256) = 64)", name='ck_verified_source_hash'),
        CheckConstraint('is_current IN (0,1)', name='ck_parse_current_flag'),
        Index('uq_current_parse_revision', 'version_id', unique=True, sqlite_where=text('is_current = 1')),
    )

class RevisionChunk(Base):
    __tablename__ = 'parse_revision_chunks'
    chunk_id: Mapped[int] = mapped_column(ForeignKey('chunks.id'), primary_key=True)
    parse_revision_id: Mapped[int] = mapped_column(ForeignKey('parse_revisions.id'), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    locator_json: Mapped[str] = mapped_column(Text, default='{}', server_default='{}')

class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    kb_id: Mapped[int | None] = mapped_column(ForeignKey('knowledge_bases.id'), nullable=True, index=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    reason: Mapped[str] = mapped_column(Text, default='')
    before_json: Mapped[str] = mapped_column(Text, default='{}')
    after_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

VERSIONING_TABLE_NAMES = frozenset({'schema_migrations', 'document_series', 'document_versions', 'parse_revisions', 'parse_revision_chunks', 'audit_events'})
