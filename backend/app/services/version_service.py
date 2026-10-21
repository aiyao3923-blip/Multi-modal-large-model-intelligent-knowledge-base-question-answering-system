"""Version governance: explicit schema, object authorization, CAS and evidence identity.

Metadata eligibility is not factual correctness. No function in this module
configures a provider, calls a model, or migrates a database implicitly.
"""
from __future__ import annotations
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from sqlalchemy import and_, bindparam, exists, func, inspect, literal, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from ..core.config import settings
from ..db.models import (AuditEvent, ChatSession, Chunk, Citation, Document, DocumentSeries, DocumentVersion, KnowledgeBase, Message, ParseRevision, ParseTask, RevisionChunk, SchemaMigration, User, UserKbAccess, VERSIONING_TABLE_NAMES)
from ..db.migrations import VERSION, migration_checksum
from . import version_policy as policy

class VersionError(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else detail.get('message', '版本操作未完成'))

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=lambda item: item.isoformat() if isinstance(item, (date, datetime)) else str(item))

def _schema_state(db):
    if 'version_schema_state' in db.info:
        return db.info['version_schema_state']
    names = set(inspect(db.get_bind()).get_table_names())
    records = []
    if 'schema_migrations' in names:
        records = db.execute(select(SchemaMigration.version, SchemaMigration.checksum)).all()
    # A known migration does not make an additional future/foreign migration safe.
    # Keep older code from writing a database produced by a different checkout.
    if any(record.version != VERSION for record in records):
        raise VersionError(503, '发现未知版本迁移记录，不能使用当前代码写入。请先核对数据库与代码版本。')
    applied = next((r for r in records if r.version == VERSION), None)
    if applied is not None:
        if applied.checksum != migration_checksum() or not VERSIONING_TABLE_NAMES.issubset(names):
            raise VersionError(503, '版本迁移状态与当前代码不一致，请停止写入并核对迁移记录。')
        state = {'enabled': True, 'schema_version': VERSION, 'mode': 'versioned'}
    else:
        present = sorted((VERSIONING_TABLE_NAMES - {'schema_migrations'}) & names)
        has_data = bool(present and db.scalar(text('SELECT EXISTS(' + ' UNION ALL '.join('SELECT 1 FROM "' + table + '"' for table in present) + ' LIMIT 1)')))
        if records or has_data:
            raise VersionError(503, '发现未完成或未知版本元数据，不能回退到旧写入方式。请先核对迁移。')
        state = {'enabled': False, 'schema_version': None, 'mode': 'legacy'}
    db.info['version_schema_state'] = state
    return state

def capabilities(db):
    try:
        return dict(_schema_state(db))
    except VersionError as error:
        return {'enabled': False, 'schema_version': None, 'mode': 'inconsistent', 'message': str(error)}

def versioning_enabled(db):
    # Unlike capabilities(), this must fail closed on a corrupted migration.
    return _schema_state(db)['enabled']

def require_ready(db):
    if not versioning_enabled(db):
        raise VersionError(409, '此库尚未启用显式版本迁移。不能自动迁移，请先完成经授权的数据准备。')

def _authorize_kb(db, actor, kb_id, *, write=False):
    # Import locally: legacy ingestion and the shared permission service reference us.
    from .kb_service import ensure_kb_access
    return ensure_kb_access(db, actor, db.get(KnowledgeBase, kb_id), write=write)

def _owner_clause(actor):
    if actor is None or not actor.is_active:
        return literal(False)
    return exists(select(KnowledgeBase.id).where(KnowledgeBase.id == DocumentVersion.kb_id,
                  KnowledgeBase.owner_id == actor.id).correlate(DocumentVersion))

def _reader_clause(actor):
    if actor is None or not actor.is_active:
        return literal(False)
    historical = exists(select(Citation.id).join(Message, Message.id == Citation.message_id)
        .join(ChatSession, ChatSession.id == Message.session_id).where(
            Citation.document_id == DocumentVersion.version_id, Citation.kb_id == DocumentVersion.kb_id,
            Message.kb_id == DocumentVersion.kb_id, ChatSession.kb_id == DocumentVersion.kb_id,
            ChatSession.user_id == actor.id).correlate(DocumentVersion))
    return or_(_owner_clause(actor), DocumentVersion.publication_state == 'published', historical)

def _load_version(db, actor, version_id, *, write=False):
    require_ready(db)
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise VersionError(404, '版本不存在或治理元数据尚未建立')
    kb = _authorize_kb(db, actor, version.kb_id, write=write)
    doc, series = db.get(Document, version_id), db.get(DocumentSeries, version.series_id)
    if doc is None or series is None or doc.kb_id != version.kb_id or series.kb_id != version.kb_id:
        raise VersionError(409, '资料与版本归属不一致，请联系维护人员核对')
    if not write and kb.owner_id != actor.id and not db.scalar(select(DocumentVersion.version_id).where(DocumentVersion.version_id == version_id, _reader_clause(actor))):
        raise VersionError(404, '版本不存在或不可读取')
    return version, doc, series

def ensure_document_readable(db, actor, doc):
    if versioning_enabled(db):
        _load_version(db, actor, doc.id)
    else:
        _authorize_kb(db, actor, doc.kb_id)

def _applications(version):
    try:
        value = json.loads(version.applicability_json)
        if not isinstance(value, dict):
            raise ValueError('stored applicability must be an object')
        return policy.normalize_applicability(value)
    except (ValueError, TypeError) as error:
        raise VersionError(503, '版本适用条件数据无效，请维护人员核对；不会默认扩大适用范围。') from error

def revision_dict(row):
    if row is None:
        return None
    return {key: getattr(row, key) for key in ('id', 'version_id', 'task_id', 'parser_version', 'file_sha256', 'content_hash', 'source_integrity', 'state', 'is_current', 'chunk_count', 'error_message', 'created_at', 'finished_at')}

def _view(version, doc, series, current, latest, actor, can_manage=False):
    assessment = policy.assess_version(publication_state=version.publication_state, validity=version.validity, effective_from=version.effective_from, effective_to=version.effective_to, applicability=_applications(version), as_of=datetime.now(timezone.utc))
    result = {key: getattr(version, key) for key in ('version_id', 'kb_id', 'series_id', 'version_label', 'publication_state', 'validity', 'effective_from', 'effective_to', 'metadata_revision', 'supersedes_id', 'coexistence_note', 'sha256', 'is_legacy', 'created_at', 'updated_at')}
    result.update(series_title=series.title, series_revision=series.revision, filename=doc.filename, file_type=doc.file_type, file_size=doc.file_size, applicability=_applications(version), date_status=assessment.date_status, eligibility_reasons=list(assessment.reasons), can_manage=can_manage, can_publish=bool(can_manage and series.state == 'active' and version.publication_state != 'published' and current and current.state == 'success' and current.source_integrity == 'verified' and current.file_sha256 == version.sha256), current_revision=revision_dict(current), latest_revision=revision_dict(latest))
    return result

def summaries_for_documents(db, actor, docs):
    docs = list(docs)
    if not docs or not versioning_enabled(db):
        return {}
    by_id = {doc.id: doc for doc in docs}
    ownership = {kb_id: _authorize_kb(db, actor, kb_id).owner_id == actor.id for kb_id in {doc.kb_id for doc in docs}}
    rows = db.execute(select(DocumentVersion, DocumentSeries).join(DocumentSeries, DocumentSeries.id == DocumentVersion.series_id).where(DocumentVersion.version_id.in_(by_id), _reader_clause(actor))).all()
    latest_ids = select(func.max(ParseRevision.id)).where(ParseRevision.version_id.in_(by_id)).group_by(ParseRevision.version_id)
    revisions = db.scalars(select(ParseRevision).where(ParseRevision.version_id.in_(by_id), or_(ParseRevision.is_current.is_(True), ParseRevision.id.in_(latest_ids)))).all()
    current, latest = {}, {}
    for revision in revisions:
        if revision.is_current:
            current[revision.version_id] = revision
        if revision.version_id not in latest or revision.id > latest[revision.version_id].id:
            latest[revision.version_id] = revision
    result = {}
    for version, series in rows:
        doc = by_id[version.version_id]
        if version.kb_id != doc.kb_id or series.kb_id != doc.kb_id:
            raise VersionError(409, '发现跨库版本元数据，请停止使用并核对')
        result[doc.id] = _view(version, doc, series, current.get(doc.id), latest.get(doc.id), actor, ownership[doc.kb_id])
    return result

def version_detail(db, actor, version_id):
    version, doc, _ = _load_version(db, actor, version_id)
    return summaries_for_documents(db, actor, [doc])[version.version_id]

def _series(db, actor, series_id, *, write=False):
    require_ready(db)
    series = db.get(DocumentSeries, series_id)
    if series is None:
        raise VersionError(404, '资料系列不存在')
    kb = _authorize_kb(db, actor, series.kb_id, write=write)
    if not write and kb.owner_id != actor.id and not db.scalar(select(DocumentVersion.version_id).where(DocumentVersion.series_id == series_id, _reader_clause(actor)).limit(1)):
        raise VersionError(404, '资料系列不存在或不可读取')
    return series

def series_detail(db, actor, series_id):
    series = _series(db, actor, series_id)
    return {key: getattr(series, key) for key in ('id', 'kb_id', 'title', 'business_key', 'owner_id', 'state', 'revision', 'created_at', 'updated_at')}

def _paging(page, size):
    if type(page) is not int or type(size) is not int or page < 1 or not 1 <= size <= 100:
        raise VersionError(422, '分页参数应为正页码和1–100的页大小')

def list_series_versions(db, actor, *, series_id, page=1, size=20):
    series = _series(db, actor, series_id)
    _paging(page, size)
    where = (DocumentVersion.series_id == series.id, _reader_clause(actor))
    total = db.scalar(select(func.count(DocumentVersion.version_id)).where(*where)) or 0
    ids = list(db.scalars(select(DocumentVersion.version_id).where(*where).order_by(DocumentVersion.version_id.desc()).offset((page-1)*size).limit(size)))
    docs = db.scalars(select(Document).where(Document.id.in_(ids))).all() if ids else []
    summaries = summaries_for_documents(db, actor, docs)
    return {'items': [summaries[i] for i in ids if i in summaries], 'total': total, 'page': page, 'size': size}

def list_revisions(db, actor, *, version_id, page=1, size=20):
    _load_version(db, actor, version_id)
    _paging(page, size)
    total = db.scalar(select(func.count(ParseRevision.id)).where(ParseRevision.version_id == version_id)) or 0
    rows = db.scalars(select(ParseRevision).where(ParseRevision.version_id == version_id).order_by(ParseRevision.id.desc()).offset((page-1)*size).limit(size))
    return {'items': [revision_dict(row) for row in rows], 'total': total, 'page': page, 'size': size}

def _reason(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
        raise VersionError(422, '请填写1–1000字的操作理由')
    return value.strip()

def _expected(value):
    if type(value) is not int or not 1 <= value <= 2**63-1:
        raise VersionError(422, 'revision必须是有效的正整数')
    return value

def _audit(db, actor, kb_id, object_type, object_id, action, reason, before, after):
    db.add(AuditEvent(actor_id=actor.id if actor else None, kb_id=kb_id, object_type=object_type, object_id=object_id, action=action, reason=reason, before_json=json_text(before), after_json=json_text(after)))

def _invalidate(kb_id):
    from ..engines.keyword_index import KeywordIndex
    KeywordIndex.invalidate(kb_id)

def _metadata(version):
    return {key: getattr(version, key) for key in ('version_id', 'series_id', 'kb_id', 'version_label', 'publication_state', 'validity', 'effective_from', 'effective_to', 'applicability_json', 'supersedes_id', 'metadata_revision', 'coexistence_note')}

def _lock_series(db, series, expected=None):
    revision = series.revision if expected is None else _expected(expected)
    if series.state != 'active':
        raise VersionError(409, '资料系列已归档，不能继续写入')
    result = db.execute(update(DocumentSeries).where(DocumentSeries.id == series.id, DocumentSeries.revision == revision).values(revision=DocumentSeries.revision+1, updated_at=utc_now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise VersionError(409, '资料系列已被其他操作更新，请重新读取后核对')

def _write_metadata(db, version, expected_revision, values):
    expected_revision = _expected(expected_revision)
    result = db.execute(update(DocumentVersion).where(DocumentVersion.version_id == version.version_id, DocumentVersion.metadata_revision == expected_revision).values(**values, metadata_revision=expected_revision+1, updated_at=utc_now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise VersionError(409, '版本元数据已变化，请保留草稿并重新读取核对')

def _source_path(doc):
    path = (settings.root_dir / doc.storage_path).resolve()
    if not path.is_relative_to(settings.upload_dir.resolve()) or not path.is_file():
        raise VersionError(409, '原文件缺失或不在允许的上传目录内，不能继续发布/解析')
    return path

def verify_source_file(doc, version):
    path = _source_path(doc)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    if digest.hexdigest() != version.sha256 or path.stat().st_size != doc.file_size:
        raise VersionError(409, '原文件内容与版本SHA-256不符。修改内容必须上传新版本，不能覆盖后重解析。')
    return path

def _normalize_metadata(version, changes):
    allowed = {'version_label', 'validity', 'effective_from', 'effective_to', 'applicability', 'supersedes_id', 'coexistence_note'}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise VersionError(422, '包含不可修改的版本身份字段')
    merged = {key: getattr(version, key) for key in allowed if key != 'applicability'}
    merged['applicability'] = _applications(version)
    merged.update(changes)
    label = merged['version_label']
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise VersionError(422, '版本标签应为1–80字')
    merged['version_label'] = label.strip()
    note = merged['coexistence_note']
    if note is None: note = ''
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise VersionError(422, '版本并存说明不能超过1000字')
    merged['coexistence_note'] = note.strip()
    if merged['supersedes_id'] is not None:
        _expected(merged['supersedes_id'])
    try:
        merged['effective_from'] = policy._parse_date(merged['effective_from'], field='effective_from')
        merged['effective_to'] = policy._parse_date(merged['effective_to'], field='effective_to')
        merged['applicability'] = policy.normalize_applicability(merged['applicability'])
        policy.assess_version(publication_state=version.publication_state, validity=merged['validity'], effective_from=merged['effective_from'], effective_to=merged['effective_to'], applicability=merged['applicability'], as_of=datetime.now(timezone.utc))
    except ValueError as error:
        raise VersionError(422, '日期或适用条件无效：' + str(error)) from error
    merged['applicability_json'] = json_text(merged.pop('applicability'))
    return merged

def _validate_supersession(db, version, supersedes_id):
    if supersedes_id is None:
        return
    pointers = dict(db.execute(select(DocumentVersion.version_id, DocumentVersion.supersedes_id).where(DocumentVersion.series_id == version.series_id)).all())
    if supersedes_id not in pointers:
        raise VersionError(422, '只能明确替代同一资料系列中的已有版本')
    seen = {version.version_id}
    current = supersedes_id
    while current is not None:
        if current in seen:
            raise VersionError(422, '替代关系不能指向自身或形成循环')
        seen.add(current)
        current = pointers.get(current)

def _validate_overlap(db, version, values):
    target_apps = policy.normalize_applicability(json.loads(values['applicability_json']))
    for other in db.scalars(select(DocumentVersion).where(DocumentVersion.series_id == version.series_id, DocumentVersion.version_id != version.version_id, DocumentVersion.publication_state == 'published')):
        if not policy.applicability_overlap(target_apps, _applications(other)):
            continue
        overlap = policy.intervals_overlap(values['effective_from'], values['effective_to'], other.effective_from, other.effective_to, a_validity=values['validity'], b_validity=other.validity)
        if overlap is False:
            continue
        explicit_replacement = (values['supersedes_id'] == other.version_id or other.supersedes_id == version.version_id) and policy.same_applicability(target_apps, _applications(other))
        if not explicit_replacement and not values['coexistence_note']:
            raise VersionError(409, {'message': '与已发布版本的日期/适用范围重叠或尚未明确；请核对替代关系，或填写并存说明。', 'conflicting_version_id': other.version_id})

def create_series(db, actor, *, kb_id, title, business_key=None):
    require_ready(db); _authorize_kb(db, actor, kb_id, write=True)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 255:
        raise VersionError(422, '资料标题应为1–255字')
    if business_key is not None and (not isinstance(business_key, str) or not 1 <= len(business_key.strip()) <= 160):
        raise VersionError(422, '业务标识应为空或1–160字')
    try:
        series = DocumentSeries(kb_id=kb_id, title=title.strip(), business_key=business_key.strip() if business_key else None, owner_id=actor.id)
        db.add(series); db.flush()
        identity = series.id
        _audit(db, actor, kb_id, 'document_series', identity, 'series_created', '创建逻辑资料，不按同名自动合并', {}, {'title': series.title})
        db.commit(); db.expire_all()
        return series_detail(db, actor, identity)
    except IntegrityError as error:
        db.rollback(); raise VersionError(409, '同库业务标识已存在') from error
    except Exception:
        db.rollback(); raise

def update_metadata(db, actor, *, version_id, expected_revision, reason, changes):
    version, doc, series = _load_version(db, actor, version_id, write=True)
    reason = _reason(reason); expected_revision = _expected(expected_revision)
    if version.metadata_revision != expected_revision:
        raise VersionError(409, '版本元数据已变化，请保留草稿并重新读取核对')
    values = _normalize_metadata(version, changes)
    verify_source_file(doc, version)
    before = _metadata(version)
    try:
        _lock_series(db, series)
        _validate_supersession(db, version, values['supersedes_id'])
        if version.publication_state == 'published':
            _validate_overlap(db, version, values)
        _write_metadata(db, version, expected_revision, values)
        _audit(db, actor, doc.kb_id, 'document_version', version_id, 'metadata_updated', reason, before, {**values, 'metadata_revision': expected_revision+1})
        db.commit(); db.expire_all(); _invalidate(doc.kb_id)
        return version_detail(db, actor, version_id)
    except IntegrityError as error:
        db.rollback(); raise VersionError(409, '版本标签已存在或元数据约束冲突，请核对后重试') from error
    except Exception:
        db.rollback(); raise

def publish_version(db, actor, *, version_id, expected_revision, reason, acknowledge_unknown=False):
    version, doc, series = _load_version(db, actor, version_id, write=True)
    reason = _reason(reason); expected_revision = _expected(expected_revision)
    if type(acknowledge_unknown) is not bool:
        raise VersionError(422, '未知日期确认必须是明确布尔值')
    if version.metadata_revision != expected_revision:
        raise VersionError(409, '版本元数据已变化，请重新读取后核对')
    if version.publication_state == 'published':
        raise VersionError(409, '此版本已经发布，无需重复发布')
    if version.validity == 'unknown' and not acknowledge_unknown:
        raise VersionError(422, '生效日期尚未确认；请明确确认保留未知日期，不能自动当作当前有效')
    current = db.scalar(select(ParseRevision).where(ParseRevision.version_id == version_id, ParseRevision.is_current.is_(True)))
    if current is None or current.state != 'success' or current.source_integrity != 'verified' or current.file_sha256 != version.sha256:
        raise VersionError(409, '请先完成成功的重新解析，建立可验证的当前修订，再发布此版本')
    verify_source_file(doc, version)
    values = _normalize_metadata(version, {})
    before = _metadata(version)
    try:
        _lock_series(db, series)
        _validate_supersession(db, version, values['supersedes_id'])
        _validate_overlap(db, version, values)
        _write_metadata(db, version, expected_revision, {'publication_state': 'published'})
        _audit(db, actor, doc.kb_id, 'document_version', version_id, 'published', reason, before, {'publication_state': 'published', 'metadata_revision': expected_revision+1, 'unknown_dates_acknowledged': acknowledge_unknown})
        db.commit(); db.expire_all(); _invalidate(doc.kb_id)
        return version_detail(db, actor, version_id)
    except IntegrityError as error:
        db.rollback(); raise VersionError(409, '并发发布或版本约束冲突，请重新读取') from error
    except Exception:
        db.rollback(); raise

def _change_publication(db, actor, *, version_id, expected_revision, reason, state):
    version, doc, series = _load_version(db, actor, version_id, write=True)
    reason = _reason(reason); expected_revision = _expected(expected_revision)
    if version.metadata_revision != expected_revision:
        raise VersionError(409, '版本元数据已变化，请重新读取后核对')
    if state == 'withdrawn' and version.publication_state != 'published':
        raise VersionError(409, '只有已发布版本可以撤回')
    if version.publication_state == state:
        raise VersionError(409, '此版本已处于目标状态')
    before = _metadata(version)
    try:
        _lock_series(db, series)
        _write_metadata(db, version, expected_revision, {'publication_state': state})
        _audit(db, actor, doc.kb_id, 'document_version', version_id, state, reason, before, {'publication_state': state, 'metadata_revision': expected_revision+1})
        db.commit(); db.expire_all(); _invalidate(doc.kb_id)
        return version_detail(db, actor, version_id)
    except Exception:
        db.rollback(); raise

def withdraw_version(db, actor, *, version_id, expected_revision, reason):
    return _change_publication(db, actor, version_id=version_id, expected_revision=expected_revision, reason=reason, state='withdrawn')

def archive_version(db, actor, *, version_id, expected_revision, reason):
    return _change_publication(db, actor, version_id=version_id, expected_revision=expected_revision, reason=reason, state='archived')


def register_uploaded_document(db, actor, doc, sha256, *, series_id=None, version_label=None, expected_series_revision=None, supersedes_id=None):
    """Register a new physical file in the caller's upload transaction; never publish."""
    require_ready(db); _authorize_kb(db, actor, doc.kb_id, write=True)
    label = version_label if version_label is not None else '初始草稿'
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise VersionError(422, '版本标签应为1–80字')
    if series_id is None:
        series = DocumentSeries(kb_id=doc.kb_id, title=doc.filename, owner_id=actor.id, state='active', revision=1)
        db.add(series); db.flush()
    else:
        series = _series(db, actor, series_id, write=True)
        if series.kb_id != doc.kb_id:
            raise VersionError(403, '不能跨知识库追加版本')
        _lock_series(db, series, _expected(expected_series_revision))
    version = DocumentVersion(version_id=doc.id, series_id=series.id, kb_id=doc.kb_id, version_label=label.strip(), sha256=sha256, publication_state='draft', validity='unknown', applicability_json='{}', supersedes_id=supersedes_id, metadata_revision=1, is_legacy=False, coexistence_note='')
    _validate_supersession(db, version, supersedes_id)
    db.add(version); db.flush()
    _audit(db, actor, doc.kb_id, 'document_version', doc.id, 'version_uploaded', '上传内容只创建草稿，发布须单独确认', {}, {'series_id': series.id, 'version_label': version.version_label, 'sha256': sha256})
    return version

def current_revision(db, version_id):
    if not versioning_enabled(db):
        return None
    return db.scalar(select(ParseRevision).where(ParseRevision.version_id == version_id, ParseRevision.is_current.is_(True)))

def register_parse_task(db, doc, task):
    require_ready(db)
    if db.get(DocumentVersion, doc.id) is None:
        raise VersionError(409, '该文件缺少版本元数据，不能以旧方式覆盖解析')
    revision = ParseRevision(version_id=doc.id, task_id=task.id, parser_version='builtin-parser-revision-1', source_integrity='unverified', state='building', is_current=False, chunk_count=0, content_hash='')
    db.add(revision); db.flush()
    return revision

def upload_series_version(db, actor, *, series_id, filename, content, version_label, expected_series_revision, supersedes_id=None):
    series = _series(db, actor, series_id, write=True)
    _expected(expected_series_revision)
    if series.revision != expected_series_revision:
        raise VersionError(409, '资料系列已变化，请重新读取后再上传')
    kb = _authorize_kb(db, actor, series.kb_id, write=True)
    from .kb_service import save_upload, create_parse_task
    from ..workers.tasks import submit_parse
    doc = None
    is_new = False
    created_path = None
    try:
        doc, is_new = save_upload(db, kb, actor, filename, content, series_id=series_id, version_label=version_label, expected_series_revision=expected_series_revision, supersedes_id=supersedes_id, commit_changes=False)
        if not is_new:
            raise VersionError(409, {'message': '同库已存在相同文件内容，未创建新版本。可核对已有版本或恢复归档。', 'existing_version_id': doc.id})
        created_path = doc.storage_path
        task = create_parse_task(db, doc, actor, commit_changes=False)
        identity, task_id = doc.id, task.id
        db.commit(); db.expire_all()
    except Exception as error:
        candidate = (settings.root_dir / created_path).resolve() if created_path and is_new else None
        db.rollback()
        try:
            persisted = bool(created_path and db.scalar(select(Document.id).where(Document.storage_path == created_path).limit(1)))
        except Exception:
            persisted = True
        # Only the unique uncommitted file created in this upload may be removed.
        if candidate and not persisted and candidate.is_relative_to(settings.upload_dir.resolve()) and candidate.is_file():
            candidate.unlink()
        if isinstance(error, ValueError):
            raise VersionError(422, str(error)) from error
        if isinstance(error, IntegrityError):
            raise VersionError(409, '版本标签或相同内容已存在，请重新读取后核对') from error
        raise
    submit_parse(task_id)
    _invalidate(kb.id)
    return version_detail(db, actor, identity)

def _json_membership(column, values):
    values = list(values)
    if not values:
        return literal(False)
    # One JSON parameter avoids silently truncating large version sets or
    # exceeding SQLite's per-statement bound-parameter count.
    array = func.json_each(bindparam('version_scope_json', json_text(values), unique=True)).table_valued('value')
    return column.in_(select(array.c.value))

def retrieval_scope(db, kb_ids, *, as_of=None, conditions=None, include_unknown=True):
    """Compute a pinned metadata/current-revision scope without inferring identity.

    Callers must authorize the KBs first. Legacy mode returns None. Default old
    questions explicitly retain unknown-date records, with a notice available to
    the caller; they are not relabeled as current/verified.
    """
    if not versioning_enabled(db):
        return None
    pinned_time = as_of if as_of is not None else datetime.now(timezone.utc)
    try:
        day = policy._business_date(pinned_time, 'Asia/Shanghai')
        supplied = policy.normalize_applicability(conditions)
        if type(include_unknown) is not bool:
            raise ValueError('include_unknown must be a bool')
    except ValueError as error:
        raise VersionError(422, '检索范围条件无效：' + str(error)) from error
    query = (select(DocumentVersion, DocumentSeries.revision, ParseRevision.id, ParseRevision.source_integrity)
             .join(Document, Document.id == DocumentVersion.version_id)
             .join(DocumentSeries, DocumentSeries.id == DocumentVersion.series_id)
             .join(ParseRevision, and_(ParseRevision.version_id == DocumentVersion.version_id, ParseRevision.is_current.is_(True), ParseRevision.state == 'success'))
             .where(DocumentVersion.kb_id.in_(list(kb_ids)), DocumentVersion.publication_state == 'published', DocumentSeries.state == 'active', Document.kb_id == DocumentVersion.kb_id, DocumentSeries.kb_id == DocumentVersion.kb_id)
             .execution_options(yield_per=500))
    matches = {}
    for version, series_revision, revision_id, integrity in db.execute(query):
        apps = _applications(version)
        try:
            assessment = policy.assess_version(publication_state=version.publication_state, validity=version.validity, effective_from=version.effective_from, effective_to=version.effective_to, applicability=apps, as_of=pinned_time, conditions=supplied, include_unknown=include_unknown)
        except ValueError as error:
            raise VersionError(503, '已保存版本日期不合法，请维护人员核对') from error
        if assessment.eligible:
            matches[version.version_id] = {
                'id': version.version_id, 'metadata_revision': version.metadata_revision,
                'revision_id': revision_id, 'series_revision': series_revision,
                'apps_key': hashlib.sha256(json_text(apps).encode('utf-8')).hexdigest(),
                'supersedes_id': version.supersedes_id, 'date_status': assessment.date_status,
                'source_integrity': integrity,
            }
    superseded = {row['supersedes_id'] for row in matches.values() if row['supersedes_id'] in matches and row['apps_key'] == matches[row['supersedes_id']]['apps_key']}
    active = [matches[key] for key in sorted(matches) if key not in superseded]
    keys = [f"{row['id']}:{row['metadata_revision']}:{row['revision_id']}:{row['series_revision']}" for row in active]
    unknown = [row['id'] for row in active if row['date_status'] == 'unknown']
    signature = hashlib.sha256(json_text({'keys': keys, 'day': day.isoformat(), 'conditions': supplied, 'include_unknown': include_unknown}).encode('utf-8')).hexdigest()
    return {'version_ids': [row['id'] for row in active], 'revision_ids': [row['revision_id'] for row in active], 'keys': keys, 'signature': signature, 'business_date': day.isoformat(), 'conditions': supplied, 'include_unknown': include_unknown, 'unknown_date_version_ids': unknown, 'notice': '部分资料尚未确认生效日期；本次按兼容范围包含未知日期资料，请结合原文核对。' if unknown else ''}

def current_chunk_filter(db, *, scope=None, version_ids=None, include_archived=False):
    if not versioning_enabled(db):
        return literal(True)
    revisions = (select(RevisionChunk.chunk_id)
                 .join(ParseRevision, ParseRevision.id == RevisionChunk.parse_revision_id)
                 .join(DocumentVersion, DocumentVersion.version_id == ParseRevision.version_id)
                 .join(DocumentSeries, DocumentSeries.id == DocumentVersion.series_id)
                 .where(ParseRevision.is_current.is_(True), ParseRevision.state == 'success', DocumentSeries.state == 'active'))
    if not include_archived:
        revisions = revisions.where(DocumentVersion.publication_state != 'archived')
    if scope is not None:
        key = func.printf('%d:%d:%d:%d', DocumentVersion.version_id, DocumentVersion.metadata_revision, ParseRevision.id, DocumentSeries.revision)
        revisions = revisions.where(DocumentVersion.publication_state == 'published', _json_membership(ParseRevision.id, scope['revision_ids']), _json_membership(key, scope['keys']))
    elif version_ids is not None:
        revisions = revisions.where(_json_membership(DocumentVersion.version_id, version_ids))
    return Chunk.id.in_(revisions)

def document_listing_filter(db, actor=None):
    if not versioning_enabled(db):
        return literal(True)
    if actor is not None:
        return Document.id.in_(select(DocumentVersion.version_id).where(or_(_owner_clause(actor), DocumentVersion.publication_state == 'published')))
    return ~exists(select(DocumentVersion.version_id).where(DocumentVersion.version_id == Document.id, DocumentVersion.publication_state == 'archived').correlate(Document))
