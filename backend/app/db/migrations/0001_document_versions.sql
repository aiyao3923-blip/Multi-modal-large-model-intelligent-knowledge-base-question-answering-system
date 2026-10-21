-- Migration 0001 under development; freeze before any non-ephemeral application.
-- Only companion tables/indexes/triggers are added; base rows are not rewritten.

CREATE TABLE IF NOT EXISTS schema_migrations (
	version VARCHAR(80) NOT NULL, 
	checksum VARCHAR(64) NOT NULL, 
	applied_at DATETIME NOT NULL, 
	baseline_json TEXT NOT NULL, 
	PRIMARY KEY (version)
);

CREATE TABLE IF NOT EXISTS audit_events (
	id INTEGER NOT NULL, 
	actor_id INTEGER, 
	kb_id INTEGER, 
	object_type VARCHAR(40) NOT NULL, 
	object_id INTEGER, 
	action VARCHAR(80) NOT NULL, 
	reason TEXT NOT NULL, 
	before_json TEXT NOT NULL, 
	after_json TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id)
);

CREATE TABLE IF NOT EXISTS document_series (
	id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	business_key VARCHAR(160), 
	owner_id INTEGER NOT NULL, 
	state VARCHAR(16) DEFAULT 'active' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_series_business_key UNIQUE (kb_id, business_key), 
	CONSTRAINT ck_series_state CHECK (state IN ('active','archived')), 
	CONSTRAINT ck_series_revision CHECK (revision >= 1), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS document_versions (
	version_id INTEGER NOT NULL, 
	series_id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	version_label VARCHAR(80) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	publication_state VARCHAR(16) DEFAULT 'draft' NOT NULL, 
	validity VARCHAR(16) DEFAULT 'unknown' NOT NULL, 
	effective_from DATE, 
	effective_to DATE, 
	applicability_json TEXT DEFAULT '{}' NOT NULL, 
	supersedes_id INTEGER, 
	metadata_revision INTEGER DEFAULT '1' NOT NULL, 
	is_legacy BOOLEAN DEFAULT 0 NOT NULL, 
	coexistence_note TEXT DEFAULT '' NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (version_id), 
	CONSTRAINT uq_series_version_label UNIQUE (series_id, version_label), 
	CONSTRAINT ck_version_publication CHECK (publication_state IN ('draft','published','withdrawn','archived')), 
	CONSTRAINT ck_version_validity CHECK (validity IN ('unknown','known')), 
	CONSTRAINT ck_version_unknown_dates CHECK (validity != 'unknown' OR (effective_from IS NULL AND effective_to IS NULL)), 
	CONSTRAINT ck_version_interval CHECK (effective_from IS NULL OR effective_to IS NULL OR effective_from < effective_to), 
	CONSTRAINT ck_version_not_self_superseded CHECK (supersedes_id IS NULL OR supersedes_id != version_id), 
	CONSTRAINT ck_version_metadata_revision CHECK (metadata_revision >= 1), 
	CONSTRAINT ck_version_legacy_flag CHECK (is_legacy IN (0,1)), 
	CONSTRAINT ck_version_sha256 CHECK (length(sha256) = 64), 
	FOREIGN KEY(version_id) REFERENCES documents (id), 
	FOREIGN KEY(series_id) REFERENCES document_series (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id), 
	FOREIGN KEY(supersedes_id) REFERENCES documents (id)
);

CREATE TABLE IF NOT EXISTS parse_revisions (
	id INTEGER NOT NULL, 
	version_id INTEGER NOT NULL, 
	task_id INTEGER, 
	parser_version VARCHAR(120) NOT NULL, 
	file_sha256 VARCHAR(64), 
	content_hash VARCHAR(64) NOT NULL, 
	source_integrity VARCHAR(24) DEFAULT 'unverified' NOT NULL, 
	state VARCHAR(24) DEFAULT 'building' NOT NULL, 
	is_current BOOLEAN DEFAULT 0 NOT NULL, 
	chunk_count INTEGER DEFAULT '0' NOT NULL, 
	error_message TEXT DEFAULT '' NOT NULL, 
	created_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_parse_revision_state CHECK (state IN ('building','success','failed','legacy_empty')), 
	CONSTRAINT ck_parse_source_integrity CHECK (source_integrity IN ('verified','legacy_unverified','unverified')), 
	CONSTRAINT ck_parse_current_success CHECK (is_current = 0 OR state = 'success'), 
	CONSTRAINT ck_parse_chunk_count CHECK (chunk_count >= 0), 
	CONSTRAINT ck_parse_success_evidence CHECK (state != 'success' OR (chunk_count > 0 AND length(content_hash) = 64 AND source_integrity IN ('verified','legacy_unverified'))), 
	CONSTRAINT ck_verified_source_hash CHECK (source_integrity != 'verified' OR (file_sha256 IS NOT NULL AND length(file_sha256) = 64)), 
	CONSTRAINT ck_parse_current_flag CHECK (is_current IN (0,1)), 
	FOREIGN KEY(version_id) REFERENCES documents (id), 
	UNIQUE (task_id), 
	FOREIGN KEY(task_id) REFERENCES parse_tasks (id)
);

CREATE TABLE IF NOT EXISTS parse_revision_chunks (
	chunk_id INTEGER NOT NULL, 
	parse_revision_id INTEGER NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	locator_json TEXT DEFAULT '{}' NOT NULL, 
	PRIMARY KEY (chunk_id), 
	FOREIGN KEY(chunk_id) REFERENCES chunks (id), 
	FOREIGN KEY(parse_revision_id) REFERENCES parse_revisions (id)
);
CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action);

CREATE INDEX IF NOT EXISTS ix_audit_events_kb_id ON audit_events (kb_id);

CREATE INDEX IF NOT EXISTS ix_document_series_kb_id ON document_series (kb_id);

CREATE INDEX IF NOT EXISTS ix_document_versions_kb_id ON document_versions (kb_id);

CREATE INDEX IF NOT EXISTS ix_document_versions_series_id ON document_versions (series_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_new_version_content ON document_versions (kb_id, sha256) WHERE is_legacy = 0;

CREATE INDEX IF NOT EXISTS ix_parse_revisions_version_id ON parse_revisions (version_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_parse_revision ON parse_revisions (version_id) WHERE is_current = 1;

CREATE INDEX IF NOT EXISTS ix_parse_revision_chunks_parse_revision_id ON parse_revision_chunks (parse_revision_id);

-- Physical identity is immutable once a version is registered.
CREATE TRIGGER IF NOT EXISTS versioned_document_no_mutation
BEFORE UPDATE OF kb_id, filename, file_type, file_size, storage_path, fingerprint ON documents
WHEN EXISTS (SELECT 1 FROM document_versions WHERE version_id=OLD.id)
 AND (NEW.kb_id IS NOT OLD.kb_id OR NEW.filename IS NOT OLD.filename OR NEW.file_type IS NOT OLD.file_type
 OR NEW.file_size IS NOT OLD.file_size OR NEW.storage_path IS NOT OLD.storage_path OR NEW.fingerprint IS NOT OLD.fingerprint)
BEGIN SELECT RAISE(ABORT, 'versioned_document_content_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS versioned_document_no_delete BEFORE DELETE ON documents
WHEN EXISTS (SELECT 1 FROM document_versions WHERE version_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'versioned_document_must_be_archived'); END;
CREATE TRIGGER IF NOT EXISTS version_metadata_identity BEFORE UPDATE ON document_versions
WHEN NEW.version_id IS NOT OLD.version_id OR NEW.series_id IS NOT OLD.series_id OR NEW.kb_id IS NOT OLD.kb_id
 OR NEW.sha256 IS NOT OLD.sha256 OR NEW.is_legacy IS NOT OLD.is_legacy OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'version_identity_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS version_metadata_no_delete BEFORE DELETE ON document_versions
BEGIN SELECT RAISE(ABORT, 'version_metadata_must_be_archived'); END;
CREATE TRIGGER IF NOT EXISTS version_scope_insert BEFORE INSERT ON document_versions
WHEN (SELECT kb_id FROM documents WHERE id=NEW.version_id) IS NOT NEW.kb_id
 OR (SELECT kb_id FROM document_series WHERE id=NEW.series_id) IS NOT NEW.kb_id
 OR (NEW.supersedes_id IS NOT NULL AND (SELECT series_id FROM document_versions WHERE version_id=NEW.supersedes_id) IS NOT NEW.series_id)
BEGIN SELECT RAISE(ABORT, 'version_scope_or_supersession_mismatch'); END;
CREATE TRIGGER IF NOT EXISTS version_scope_update BEFORE UPDATE ON document_versions
WHEN (SELECT kb_id FROM documents WHERE id=NEW.version_id) IS NOT NEW.kb_id
 OR (SELECT kb_id FROM document_series WHERE id=NEW.series_id) IS NOT NEW.kb_id
 OR (NEW.supersedes_id IS NOT NULL AND (SELECT series_id FROM document_versions WHERE version_id=NEW.supersedes_id) IS NOT NEW.series_id)
BEGIN SELECT RAISE(ABORT, 'version_scope_or_supersession_mismatch'); END;

-- Revisions may switch their current pointer; finalized evidence cannot be rewritten.
CREATE TRIGGER IF NOT EXISTS finalized_parse_revision_immutable BEFORE UPDATE ON parse_revisions
WHEN OLD.state IN ('success','failed','legacy_empty') AND
 (NEW.id IS NOT OLD.id OR NEW.version_id IS NOT OLD.version_id OR NEW.task_id IS NOT OLD.task_id
 OR NEW.parser_version IS NOT OLD.parser_version OR NEW.file_sha256 IS NOT OLD.file_sha256
 OR NEW.content_hash IS NOT OLD.content_hash OR NEW.source_integrity IS NOT OLD.source_integrity
 OR NEW.state IS NOT OLD.state OR NEW.chunk_count IS NOT OLD.chunk_count
 OR NEW.error_message IS NOT OLD.error_message OR NEW.created_at IS NOT OLD.created_at OR NEW.finished_at IS NOT OLD.finished_at)
BEGIN SELECT RAISE(ABORT, 'finalized_parse_revision_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS parse_revision_no_delete BEFORE DELETE ON parse_revisions
BEGIN SELECT RAISE(ABORT, 'parse_revision_history_is_retained'); END;
CREATE TRIGGER IF NOT EXISTS revision_chunk_scope BEFORE INSERT ON parse_revision_chunks
WHEN (SELECT document_id FROM chunks WHERE id=NEW.chunk_id) IS NOT (SELECT version_id FROM parse_revisions WHERE id=NEW.parse_revision_id)
 OR (SELECT kb_id FROM chunks WHERE id=NEW.chunk_id) IS NOT
    (SELECT d.kb_id FROM documents d JOIN parse_revisions r ON r.version_id=d.id WHERE r.id=NEW.parse_revision_id)
BEGIN SELECT RAISE(ABORT, 'revision_chunk_scope_mismatch'); END;
CREATE TRIGGER IF NOT EXISTS revision_chunk_no_update BEFORE UPDATE ON parse_revision_chunks
BEGIN SELECT RAISE(ABORT, 'revision_chunk_locator_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS revision_chunk_no_delete BEFORE DELETE ON parse_revision_chunks
BEGIN SELECT RAISE(ABORT, 'revision_chunk_history_is_retained'); END;
CREATE TRIGGER IF NOT EXISTS versioned_chunk_no_update BEFORE UPDATE ON chunks
WHEN EXISTS (SELECT 1 FROM parse_revision_chunks WHERE chunk_id=OLD.id)
 AND (NEW.id IS NOT OLD.id OR NEW.document_id IS NOT OLD.document_id OR NEW.kb_id IS NOT OLD.kb_id
 OR NEW.seq_no IS NOT OLD.seq_no OR NEW.content IS NOT OLD.content OR NEW.page_no IS NOT OLD.page_no
 OR NEW.meta_json IS NOT OLD.meta_json OR NEW.chunk_hash IS NOT OLD.chunk_hash OR NEW.vector_json IS NOT OLD.vector_json
 OR NEW.created_at IS NOT OLD.created_at)
BEGIN SELECT RAISE(ABORT, 'versioned_chunk_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS versioned_chunk_no_delete BEFORE DELETE ON chunks
WHEN EXISTS (SELECT 1 FROM parse_revision_chunks WHERE chunk_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'versioned_chunk_history_is_retained'); END;
CREATE TRIGGER IF NOT EXISTS audit_event_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_event_is_append_only'); END;
CREATE TRIGGER IF NOT EXISTS audit_event_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_event_is_append_only'); END;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_parse_task_per_document ON parse_tasks (document_id) WHERE phase NOT IN ('success','failed');

CREATE TRIGGER IF NOT EXISTS revision_chunk_only_building BEFORE INSERT ON parse_revision_chunks
WHEN (SELECT state FROM parse_revisions WHERE id=NEW.parse_revision_id) IS NOT 'building'
BEGIN SELECT RAISE(ABORT, 'cannot_append_to_finalized_parse_revision'); END;
