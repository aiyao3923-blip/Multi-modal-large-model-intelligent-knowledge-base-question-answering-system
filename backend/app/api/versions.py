"""W2 版本 API：只编排真实版本服务，权限/发布/事务仍由服务再次核验。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .deps import CurrentUser, get_current_user
from ..db.models import User
from ..db.session import get_db
from ..schemas.common import PageOut
from ..schemas.versions import (
    MetadataUpdate, RevisionOut, SeriesCreate, SeriesOut, SeriesVersionUpload,
    VersionAction, VersionOut, VersionPublish,
)
from ..services import version_service

router = APIRouter(prefix="/api", tags=["versions"])
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ResourceId = Annotated[int, Path(ge=1)]


def _actor(db: Session, current: CurrentUser) -> User:
    actor = db.get(User, current.id)
    if actor is None or not actor.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    return actor


@contextmanager
def _service_errors():
    try:
        yield
    except version_service.VersionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/versions/capabilities", response_model=dict)
def version_capabilities(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.capabilities(db)


@router.post("/kbs/{kb_id}/document-series", response_model=SeriesOut)
def create_document_series(
    kb_id: ResourceId,
    data: SeriesCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.create_series(
            db, _actor(db, current), kb_id=kb_id, title=data.title, business_key=data.business_key,
        )


@router.get("/document-series/{series_id}", response_model=SeriesOut)
def document_series_detail(
    series_id: ResourceId,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.series_detail(db, _actor(db, current), series_id)


@router.get("/document-series/{series_id}/versions", response_model=PageOut[VersionOut])
def document_series_versions(
    series_id: ResourceId,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.list_series_versions(
            db, _actor(db, current), series_id=series_id, page=page, size=size,
        )


@router.get("/documents/{version_id}/version", response_model=VersionOut)
def document_version_detail(
    version_id: ResourceId,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.version_detail(db, _actor(db, current), version_id)


@router.get("/documents/{version_id}/revisions", response_model=PageOut[RevisionOut])
def document_revisions(
    version_id: ResourceId,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.list_revisions(
            db, _actor(db, current), version_id=version_id, page=page, size=size,
        )


@router.patch("/documents/{version_id}/metadata", response_model=VersionOut)
def patch_document_metadata(
    version_id: ResourceId,
    data: MetadataUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.update_metadata(
            db, _actor(db, current), version_id=version_id,
            expected_revision=data.expected_revision, reason=data.reason,
            changes=data.model_dump(
                exclude_unset=True, exclude={"expected_revision", "reason"},
            ),
        )


@router.post("/documents/{version_id}/publish", response_model=VersionOut)
def publish_document_version(
    version_id: ResourceId,
    data: VersionPublish,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.publish_version(
            db, _actor(db, current), version_id=version_id,
            expected_revision=data.expected_revision, reason=data.reason,
            acknowledge_unknown=data.acknowledge_unknown,
        )


@router.post("/documents/{version_id}/withdraw", response_model=VersionOut)
def withdraw_document_version(
    version_id: ResourceId,
    data: VersionAction,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.withdraw_version(
            db, _actor(db, current), version_id=version_id,
            expected_revision=data.expected_revision, reason=data.reason,
        )


@router.post("/documents/{version_id}/archive", response_model=VersionOut)
def archive_document_version(
    version_id: ResourceId,
    data: VersionAction,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    with _service_errors():
        return version_service.archive_version(
            db, _actor(db, current), version_id=version_id,
            expected_revision=data.expected_revision, reason=data.reason,
        )


@router.post("/document-series/{series_id}/versions", response_model=VersionOut)
async def upload_document_series_version(
    series_id: ResourceId,
    request: Request,
    file: UploadFile = File(...),
    version_label: str = Form(...),
    expected_series_revision: str = Form(...),
    supersedes_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    # FastAPI's individual Form parameters do not reject extras or duplicate parts.
    # Check the parsed form without re-reading the file body or trusting Content-Length.
    form = await request.form()
    allowed = {"file", "version_label", "expected_series_revision", "supersedes_id"}
    unexpected = sorted(set(form.keys()) - allowed)
    if unexpected:
        raise RequestValidationError([
            {"type": "extra_forbidden", "loc": ("body", key), "msg": "Extra inputs are not permitted"}
            for key in unexpected
        ])
    repeated = sorted(key for key in allowed if len(form.getlist(key)) > 1)
    if repeated:
        raise RequestValidationError([
            {"type": "value_error", "loc": ("body", key), "msg": "Only one value/file is allowed"}
            for key in repeated
        ])
    try:
        data = SeriesVersionUpload(
            version_label=version_label,
            expected_series_revision=expected_series_revision,
            supersedes_id=supersedes_id,
        )
    except ValidationError as exc:
        errors = [
            {**error, "loc": ("body", *error["loc"])}
            for error in exc.errors(include_context=False)
        ]
        raise RequestValidationError(errors) from exc
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=422, detail="文件名不能为空")
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="单个文件不能超过50MB")

    with _service_errors():
        return version_service.upload_series_version(
            db, _actor(db, current), series_id=series_id,
            filename=file.filename, content=content, version_label=data.version_label,
            expected_series_revision=data.expected_series_revision, supersedes_id=data.supersedes_id,
        )
