"""Append a hash-verified paper package to three private libraries, with resumable receipts.

No migrations, channel/credential changes, generation calls, or automatic rollback.
Default is preflight only. The caller must explicitly select the existing admin owner.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BUCKETS = {"paper_original": ("01_论文原文", "矿区论文_原文"), "paper_note": ("02_阅读笔记", "矿区论文_阅读笔记"),
           "research_contract": ("03_研究方向", "矿区研究_方向与约束"), "research_design": ("03_研究方向", "矿区研究_方向与约束")}
PROTECTED = ("knowledge_bases", "documents", "chunks", "parse_tasks", "user_kb_access", "citations")


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def no_links(path):
    for p in (path, *path.parents):
        if p.is_symlink() or p.is_junction():
            raise ValueError("Link/junction rejected")
    return path


def atomic_json(path, data):
    no_links(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    assert temporary.parent.resolve() == path.parent.resolve()
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def row_snapshot(db_path, ids=None):
    with closing(sqlite3.connect(str(db_path))) as db:
        db.execute("PRAGMA query_only=ON")
        output = {}
        for table in PROTECTED:
            query = f'SELECT * FROM "{table}"'
            selected = list(ids.get(table, {})) if ids is not None else None
            if selected is not None and not selected:
                output[table] = {}
                continue
            values = []
            if selected is not None:
                query += " WHERE id IN (" + ",".join("?" for _ in selected) + ")"
                values = selected
            cursor = db.execute(query + " ORDER BY id", values)
            id_column = [c[0] for c in cursor.description].index("id")
            output[table] = {str(row[id_column]): hashlib.sha256(json.dumps(row, ensure_ascii=False, default=str).encode()).hexdigest()
                             for row in cursor.fetchall()}
    return output


def business_snapshot(db_path):
    """Consistent data-only snapshot: never copy authentication/provider tables.

    The application's full DB backup deliberately rejects databases containing
    credentials. Preserve that boundary: this is not a full-system backup and
    must not replace a live database. Original upload files stay in place.
    """
    data = {"schema_version": 1, "kind": "business-data-only", "tables": {},
            "excluded": ["users", "llm_channels", "security/provider configuration"],
            "not_a_full_database_backup": True}
    hashes = {}
    with closing(sqlite3.connect(str(db_path))) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Live database quick_check failed")
        db.execute("BEGIN")
        for table in PROTECTED:
            cursor = db.execute(f'SELECT * FROM "{table}" ORDER BY id')
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
            data["tables"][table] = {"columns": columns, "rows": rows}
            index = columns.index("id")
            hashes[table] = {str(row[index]): hashlib.sha256(json.dumps(row, ensure_ascii=False, default=str).encode()).hexdigest() for row in rows}
        db.rollback()
    return data, hashes

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--expected-db", type=Path, required=True)
    p.add_argument("--owner-id", type=int, required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    run = no_links(args.run_dir.absolute()).resolve()
    if not run.is_relative_to(ROOT / "storage/paper-rag") or not (run / "source-baseline.json").is_file():
        raise ValueError("Run must belong to this integration task")
    package = no_links(args.package.absolute()).resolve()
    manifest_path = no_links(args.manifest.absolute()).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle = json.loads((package / "build_receipt.json").read_text(encoding="utf-8"))
    if bundle["config"]["manifest_sha256"] != sha(manifest_path) or bundle["config"]["profile"] != "all":
        raise ValueError("Package/manifest binding mismatch")
    docs = manifest["documents"]
    expected_ids = {f"P{i:03d}" for i in range(1, 35)} | {f"N{i:03d}" for i in range(1, 31)} | {f"R{i:03d}" for i in range(1, 6)}
    if len(docs) != 69 or {d["id"] for d in docs} != expected_ids:
        raise ValueError("Only the approved 34/30/5 corpus is accepted")
    records = {d["id"]: d for d in bundle["documents"]}
    for d in docs:
        if d["kind"] not in BUCKETS or Path(d["output_name"]).name != d["output_name"]:
            raise ValueError("Invalid document category/name")
        source = no_links(package / BUCKETS[d["kind"]][0] / d["output_name"]).resolve()
        record = records[d["id"]]
        if not source.is_relative_to(package) or source.relative_to(package).as_posix() != record["output_path"]:
            raise ValueError("Package path escaped or changed")
        if sha(source) != record["output_sha256"] or record["source_sha256"] != d["sha256"]:
            raise ValueError("Input content changed: " + d["id"])
        if not 0 < source.stat().st_size <= 50 * 1024 * 1024:
            raise ValueError("Upload size outside supported range")
    os.environ["APP_CONFIG"] = str(ROOT / "backend/config.yaml")
    os.environ.pop("APP_EMBEDDING_MODEL_DIR", None)
    os.environ["APP_OCR_ENGINE"] = "none"
    os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import settings
    if settings.db_path.resolve() != no_links(args.expected_db.absolute()).resolve() or not settings.db_path.is_file():
        raise ValueError("Live database identity mismatch")
    if not settings.db_path.resolve().is_relative_to(ROOT / "backend/storage"):
        raise ValueError("Database outside approved application storage")
    from sqlalchemy import event, func, select
    from sqlalchemy.orm import load_only
    from app.db.session import get_engine, get_session_factory
    from app.db.models import Chunk, Document, KnowledgeBase, ParseTask, User, UserKbAccess
    from app.engines.doc_ingest import ingest_document
    from app.engines.embedder import get_embedding_identity
    from app.engines.vector_store import vector_store
    from app.schemas.kb import KbCreate
    from app.services import kb_service, version_service
    engine = get_engine()
    forbidden = []
    allowed_writes = {"knowledge_bases", "user_kb_access", "documents", "parse_tasks", "chunks", "citations"}

    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and column in ("api_key", "password_salt", "password_digest"):
            forbidden.append("secret_column_read")
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table not in allowed_writes:
            forbidden.append("write_outside_import_tables")
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_CREATE_TABLE):
            forbidden.append("schema_change")
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @event.listens_for(engine, "connect")
    def protect(dbapi, record):
        dbapi.set_authorizer(authorize)

    network = []
    def network_guard(event_name, values):
        if event_name in ("socket.connect", "socket.sendto", "socket.getaddrinfo"):
            network.append(event_name)
            raise PermissionError("Import is local-only; external requests forbidden")
    sys.addaudithook(network_guard)
    factory = get_session_factory()
    actor_columns = load_only(User.id, User.username, User.role_code, User.is_active)
    receipt_path = run / "import-receipt.json"
    digest = sha(manifest_path)
    marker = "paper-rag-batch:" + run.name + ":" + digest
    with factory() as db:
        actor = db.scalar(select(User).options(actor_columns).where(User.id == args.owner_id))
        if not actor or not actor.is_active or actor.role_code != "admin":
            raise ValueError("Expected active admin owner")
        if version_service.versioning_enabled(db):
            raise ValueError("This batch was approved for the verified legacy database; re-evaluate versioned publication policy first")
        active = db.scalar(select(func.count(ParseTask.id)).where(ParseTask.phase.notin_(["success", "failed"])))
        if active:
            raise ValueError("Active parsing detected; do not overlap a live task")
        for name in dict.fromkeys(v[1] for v in BUCKETS.values()):
            found = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
            if found and (found.owner_id != args.owner_id or not found.description.startswith(marker)):
                raise ValueError("Same-named library belongs to another task: " + name)
    if not args.execute:
        print(json.dumps({"status": "preflight_pass", "documents": 69, "private_owner_id": args.owner_id,
                          "new_library_names": list(dict.fromkeys(v[1] for v in BUCKETS.values())), "database_writes": 0}, ensure_ascii=False))
        engine.dispose()
        return 0
    identity = get_embedding_identity()
    expected_identity = json.loads((run / "real-embedding-smoke.json").read_text(encoding="utf-8"))["identity"]
    if not identity or identity != expected_identity:
        raise ValueError("Actual embedding identity is not the validated model")
    lock = (run / "import.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0"); lock.flush()
    lock.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    started = time.perf_counter()
    try:
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if any(receipt.get(k) != v for k, v in {"run_id": run.name, "manifest_sha256": digest, "embedding_model_id": identity,
                                                   "database": str(settings.db_path.resolve()), "owner_id": args.owner_id}.items()):
                raise ValueError("Resume identity conflict")
        else:
            backup = run / "private-backup/business-data-before.json"
            if backup.exists():
                raise ValueError("Existing data snapshot without receipt; recover before writing")
            data, before = business_snapshot(settings.db_path)
            original_files = {}
            doc_data = data["tables"]["documents"]
            path_index = doc_data["columns"].index("storage_path")
            id_index = doc_data["columns"].index("id")
            for values in doc_data["rows"]:
                path = no_links(settings.root_dir / values[path_index]).resolve()
                if not path.is_relative_to(settings.upload_dir.resolve()) or not path.is_file():
                    raise ValueError("Original upload missing or outside known storage")
                original_files[str(values[id_index])] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
            atomic_json(backup, data)
            atomic_json(run / "protected-original-rows.json", before)
            atomic_json(run / "protected-original-uploads.json", original_files)
            receipt = {"schema_version": 1, "run_id": run.name, "manifest_sha256": digest, "embedding_model_id": identity,
                       "database": str(settings.db_path.resolve()), "owner_id": args.owner_id, "backup_sha256": sha(backup),
                       "backup_kind": "business-data-only-no-auth-provider", "status": "creating_private_libraries", "knowledge_bases": {}, "documents": {}, "generation_calls": 0}
            atomic_json(receipt_path, receipt)
        for name in dict.fromkeys(v[1] for v in BUCKETS.values()):
            with factory() as db:
                kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
                if kb is None:
                    actor = db.scalar(select(User).options(actor_columns).where(User.id == args.owner_id))
                    kb = kb_service.create_kb(db, actor, KbCreate(name=name, description=marker + "；私人论文研究资料；原文/解读/设计分层，非已完成实验。", role_codes=[], import_samples=False))
                if kb.owner_id != args.owner_id or not kb.description.startswith(marker):
                    raise ValueError("Concurrent library conflict")
                if db.scalar(select(func.count(UserKbAccess.id)).where(UserKbAccess.kb_id == kb.id)):
                    raise ValueError("Private library unexpectedly grants shared roles")
                receipt["knowledge_bases"][name] = kb.id
                atomic_json(receipt_path, receipt)
        receipt["status"] = "importing"
        atomic_json(receipt_path, receipt)
        for d in docs:
            source = package / BUCKETS[d["kind"]][0] / d["output_name"]
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != records[d["id"]]["output_sha256"]:
                raise ValueError("Source changed during import")
            with factory() as db:
                kb = db.get(KnowledgeBase, receipt["knowledge_bases"][BUCKETS[d["kind"]][1]])
                actor = db.scalar(select(User).options(actor_columns).where(User.id == args.owner_id))
                doc, is_new = kb_service.save_upload(db, kb, actor, source.name, content)
                existing = list(db.scalars(select(Chunk).where(Chunk.document_id == doc.id)))
                ready = doc.status == "success" and existing and all(c.vector_json and json.loads(c.meta_json or "{}").get("embedding_model_id") == identity for c in existing)
                doc_id = doc.id
                if not ready:
                    task_id = kb_service.create_parse_task(db, doc, actor).id
            if not ready:
                outcome = ingest_document(task_id)
                if outcome.get("status") != "success" or outcome.get("vectorized") is not True:
                    raise RuntimeError(f"{d['id']} did not complete real vector ingestion: {outcome}")
            with factory() as db:
                doc = db.get(Document, doc_id)
                stored = (settings.root_dir / doc.storage_path).resolve()
                if not stored.is_relative_to(settings.upload_dir.resolve()) or sha(stored) != records[d["id"]]["output_sha256"]:
                    raise ValueError("Stored upload bytes/path mismatch")
                chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == doc_id)))
                for c in chunks:
                    import math
                    v = json.loads(c.vector_json or "null")
                    if not isinstance(v, list) or len(v) != 384 or not all(isinstance(n, (int,float)) and math.isfinite(n) for n in v):
                        raise ValueError("Invalid real vector")
                    if abs(sum(n*n for n in v)-1) > .001 or json.loads(c.meta_json or "{}").get("embedding_model_id") != identity:
                        raise ValueError("Model identity/vector norm mismatch")
                if not chunks or doc.status != "success":
                    raise ValueError("Missing successful indexed document")
                receipt["documents"][d["id"]] = {"document_id": doc_id, "kb_id": doc.kb_id, "filename": doc.filename,
                    "source_sha256": d["sha256"], "stored_sha256": sha(stored), "chunks": len(chunks),
                    "vector_dimension": 384, "page_count": doc.page_count, "status": "verified"}
                vector_store.invalidate(doc.kb_id)
            receipt["elapsed_seconds"] = round(time.perf_counter()-started, 3)
            atomic_json(receipt_path, receipt)
            print(d["id"], "VERIFIED", len(chunks), "vectors; completed", len(receipt["documents"]), "/69", flush=True)
        original = json.loads((run / "protected-original-rows.json").read_text(encoding="utf-8"))
        if row_snapshot(settings.db_path, original) != original:
            raise ValueError("Original business rows changed; do not auto-restore live DB")
        original_files = json.loads((run / "protected-original-uploads.json").read_text(encoding="utf-8"))
        for record in original_files.values():
            path = no_links(Path(record["path"]))
            if not path.is_file() or path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]:
                raise ValueError("Original upload changed; do not auto-restore")
        if forbidden or network:
            raise ValueError("A safety guard was triggered")
        with factory() as db:
            for name, kb_id in receipt["knowledge_bases"].items():
                expected = 34 if name.endswith("原文") else 30 if name.endswith("阅读笔记") else 5
                count = db.scalar(select(func.count(Document.id)).where(Document.kb_id == kb_id))
                if count != expected:
                    raise ValueError("Unexpected extra/missing document in batch library")
        receipt.update(status="complete", verified_documents=len(receipt["documents"]),
                       total_vectors=sum(x["chunks"] for x in receipt["documents"].values()),
                       original_rows_unchanged=True, original_uploads_unchanged=len(original_files), network_attempts=0, forbidden_sql_attempts=0,
                       elapsed_seconds=round(time.perf_counter()-started, 3))
        atomic_json(receipt_path, receipt)
        print(json.dumps({k: v for k, v in receipt.items() if k != "documents"}, ensure_ascii=False, indent=2))
    except Exception as exc:
        if "receipt" in locals():
            receipt.update(status="stopped", error_type=type(exc).__name__, error=str(exc)[:500])
            atomic_json(receipt_path, receipt)
        raise
    finally:
        lock.close()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())