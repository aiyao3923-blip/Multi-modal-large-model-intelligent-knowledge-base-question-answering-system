-- Schema-only regression fixture generated from the unchanged V1 legacy models.
-- No production data or credentials are included.

CREATE TABLE industry_templates (
	id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description TEXT NOT NULL, 
	category_framework_json TEXT NOT NULL, 
	prompt_template TEXT NOT NULL, 
	sample_questions_json TEXT NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE llm_channels (
	id INTEGER NOT NULL, 
	channel_type VARCHAR(32) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	base_url VARCHAR(512) NOT NULL, 
	api_key VARCHAR(512) NOT NULL, 
	model_name VARCHAR(128) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	health_status VARCHAR(32) NOT NULL, 
	last_checked_at DATETIME, 
	temperature FLOAT NOT NULL, 
	top_p FLOAT NOT NULL, 
	timeout_s INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE roles (
	id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE sys_params (
	id INTEGER NOT NULL, 
	"key" VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_salt VARCHAR(64) NOT NULL, 
	password_digest VARCHAR(128) NOT NULL, 
	display_name VARCHAR(64) NOT NULL, 
	email VARCHAR(128) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	role_code VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(role_code) REFERENCES roles (code)
);

CREATE TABLE knowledge_bases (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	description TEXT NOT NULL, 
	template_id INTEGER, 
	status VARCHAR(32) NOT NULL, 
	owner_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_kb_name UNIQUE (name), 
	FOREIGN KEY(template_id) REFERENCES industry_templates (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE TABLE documents (
	id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	file_type VARCHAR(32) NOT NULL, 
	file_size INTEGER NOT NULL, 
	storage_path VARCHAR(512) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	page_count INTEGER NOT NULL, 
	error_message TEXT NOT NULL, 
	uploaded_by INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id), 
	FOREIGN KEY(uploaded_by) REFERENCES users (id)
);

CREATE TABLE sessions (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	kb_id INTEGER, 
	title VARCHAR(255) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id)
);

CREATE TABLE user_kb_access (
	id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	role_code VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_kb_role UNIQUE (kb_id, role_code), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);

CREATE TABLE chunks (
	id INTEGER NOT NULL, 
	document_id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	seq_no INTEGER NOT NULL, 
	content TEXT NOT NULL, 
	page_no INTEGER NOT NULL, 
	meta_json TEXT NOT NULL, 
	chunk_hash VARCHAR(64) NOT NULL, 
	vector_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id)
);

CREATE TABLE messages (
	id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	question TEXT NOT NULL, 
	kb_id INTEGER, 
	mode VARCHAR(16) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES sessions (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id)
);

CREATE TABLE parse_tasks (
	id INTEGER NOT NULL, 
	document_id INTEGER NOT NULL, 
	phase VARCHAR(32) NOT NULL, 
	progress INTEGER NOT NULL, 
	chunk_count INTEGER NOT NULL, 
	error_message TEXT NOT NULL, 
	operator_id INTEGER NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(operator_id) REFERENCES users (id)
);

CREATE TABLE stat_events (
	id INTEGER NOT NULL, 
	event_type VARCHAR(32) NOT NULL, 
	kb_id INTEGER, 
	user_id INTEGER, 
	session_id INTEGER, 
	mode VARCHAR(16) NOT NULL, 
	payload_json TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(session_id) REFERENCES sessions (id)
);

CREATE TABLE citations (
	id INTEGER NOT NULL, 
	message_id INTEGER NOT NULL, 
	chunk_id INTEGER, 
	document_id INTEGER NOT NULL, 
	kb_id INTEGER NOT NULL, 
	doc_name VARCHAR(255) NOT NULL, 
	content_excerpt TEXT NOT NULL, 
	page_no INTEGER NOT NULL, 
	similarity FLOAT NOT NULL, 
	rank INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(message_id) REFERENCES messages (id), 
	FOREIGN KEY(chunk_id) REFERENCES chunks (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(kb_id) REFERENCES knowledge_bases (id)
);

CREATE TABLE qa_feedbacks (
	id INTEGER NOT NULL, 
	message_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	feedback_type VARCHAR(32) NOT NULL, 
	comment TEXT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	handler_id INTEGER, 
	handled_at DATETIME, 
	handler_note TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(message_id) REFERENCES messages (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(handler_id) REFERENCES users (id)
);
CREATE UNIQUE INDEX ix_industry_templates_code ON industry_templates (code);

CREATE INDEX ix_llm_channels_channel_type ON llm_channels (channel_type);

CREATE UNIQUE INDEX ix_roles_code ON roles (code);

CREATE UNIQUE INDEX ix_sys_params_key ON sys_params ("key");

CREATE INDEX ix_users_role_code ON users (role_code);

CREATE UNIQUE INDEX ix_users_username ON users (username);

CREATE INDEX ix_knowledge_bases_status ON knowledge_bases (status);

CREATE INDEX ix_doc_kb_status ON documents (kb_id, status);

CREATE INDEX ix_documents_kb_id ON documents (kb_id);

CREATE INDEX ix_documents_status ON documents (status);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE INDEX ix_user_kb_access_kb_id ON user_kb_access (kb_id);

CREATE INDEX ix_chunk_doc ON chunks (document_id, seq_no);

CREATE INDEX ix_chunk_kb ON chunks (kb_id);

CREATE INDEX ix_chunks_document_id ON chunks (document_id);

CREATE INDEX ix_chunks_kb_id ON chunks (kb_id);

CREATE INDEX ix_messages_created_at ON messages (created_at);

CREATE INDEX ix_messages_session_id ON messages (session_id);

CREATE INDEX ix_parse_tasks_document_id ON parse_tasks (document_id);

CREATE INDEX ix_parse_tasks_phase ON parse_tasks (phase);

CREATE INDEX ix_stat_events_created_at ON stat_events (created_at);

CREATE INDEX ix_stat_events_event_type ON stat_events (event_type);

CREATE INDEX ix_citations_message_id ON citations (message_id);

CREATE INDEX ix_qa_feedbacks_message_id ON qa_feedbacks (message_id);

CREATE INDEX ix_qa_feedbacks_status ON qa_feedbacks (status);
