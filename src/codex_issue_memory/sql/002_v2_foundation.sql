CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('schema_generation', 'v2-foundation', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

CREATE TABLE IF NOT EXISTS ranker_state (
    model_name TEXT PRIMARY KEY,
    weights_json TEXT NOT NULL,
    bias REAL NOT NULL DEFAULT 0.0,
    learning_rate REAL NOT NULL DEFAULT 0.05,
    fit_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL,
    variant_key TEXT NOT NULL,
    title TEXT NOT NULL,
    applicability_json TEXT NOT NULL DEFAULT '{}',
    negative_applicability_json TEXT NOT NULL DEFAULT '{}',
    repo_fingerprint TEXT NOT NULL DEFAULT '',
    env_fingerprint TEXT NOT NULL DEFAULT '',
    command_signature TEXT NOT NULL DEFAULT '',
    file_path_signature TEXT NOT NULL DEFAULT '',
    stack_signature TEXT NOT NULL DEFAULT '',
    patch_hash TEXT NOT NULL DEFAULT '',
    patch_summary TEXT NOT NULL DEFAULT '',
    canonical_fix TEXT NOT NULL DEFAULT '',
    verification_steps TEXT NOT NULL DEFAULT '',
    rollback_steps TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    search_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('provisional', 'active', 'archived')),
    times_used INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    reject_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.50,
    memory_strength REAL NOT NULL DEFAULT 0.50,
    last_used_at TEXT,
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE CASCADE,
    UNIQUE(pattern_id, variant_key)
);

CREATE INDEX IF NOT EXISTS idx_issue_variants_pattern
    ON issue_variants(pattern_id);
CREATE INDEX IF NOT EXISTS idx_issue_variants_env
    ON issue_variants(env_fingerprint);
CREATE INDEX IF NOT EXISTS idx_issue_variants_status_updated
    ON issue_variants(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS issue_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER,
    variant_id INTEGER,
    session_id TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT 'global',
    repo_name TEXT NOT NULL DEFAULT '',
    repo_fingerprint TEXT NOT NULL DEFAULT '',
    git_commit TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    raw_error TEXT NOT NULL DEFAULT '',
    normalized_error TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    stack_excerpt TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    exception_types_json TEXT NOT NULL DEFAULT '[]',
    query_tokens_json TEXT NOT NULL DEFAULT '[]',
    env_fingerprint TEXT NOT NULL DEFAULT '',
    env_json TEXT NOT NULL DEFAULT '{}',
    patch_hash TEXT NOT NULL DEFAULT '',
    patch_summary TEXT NOT NULL DEFAULT '',
    verification_command TEXT NOT NULL DEFAULT '',
    verification_output TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'verified'
        CHECK(outcome IN ('verified', 'rejected', 'partial', 'unresolved')),
    consolidation_status TEXT NOT NULL DEFAULT 'attached'
        CHECK(consolidation_status IN ('pending', 'attached', 'review', 'archived')),
    resolution_notes TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE SET NULL,
    FOREIGN KEY(variant_id) REFERENCES issue_variants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_issue_episodes_pattern
    ON issue_episodes(pattern_id);
CREATE INDEX IF NOT EXISTS idx_issue_episodes_variant
    ON issue_episodes(variant_id);
CREATE INDEX IF NOT EXISTS idx_issue_episodes_session
    ON issue_episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_issue_episodes_created
    ON issue_episodes(created_at DESC);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_uuid TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT 'global',
    repo_name TEXT NOT NULL DEFAULT '',
    repo_fingerprint TEXT NOT NULL DEFAULT '',
    raw_query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    error_family TEXT NOT NULL,
    root_cause_class TEXT NOT NULL,
    exception_types_json TEXT NOT NULL DEFAULT '[]',
    env_fingerprint TEXT NOT NULL DEFAULT '',
    retrieval_mode TEXT NOT NULL DEFAULT 'match'
        CHECK(retrieval_mode IN ('match', 'search')),
    decision_status TEXT NOT NULL DEFAULT 'abstain'
        CHECK(decision_status IN ('match', 'ambiguous', 'abstain')),
    decision_confidence REAL NOT NULL DEFAULT 0.0,
    abstain_reason TEXT NOT NULL DEFAULT '',
    selected_pattern_id INTEGER,
    selected_variant_id INTEGER,
    selected_candidate_rank INTEGER,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    token_cost_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(selected_pattern_id) REFERENCES issue_patterns(id) ON DELETE SET NULL,
    FOREIGN KEY(selected_variant_id) REFERENCES issue_variants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_created
    ON retrieval_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_events_session
    ON retrieval_events(session_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_events_decision
    ON retrieval_events(decision_status);

CREATE TABLE IF NOT EXISTS retrieval_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retrieval_event_id INTEGER NOT NULL,
    candidate_rank INTEGER NOT NULL,
    candidate_type TEXT NOT NULL DEFAULT 'variant'
        CHECK(candidate_type IN ('pattern', 'variant')),
    pattern_id INTEGER,
    variant_id INTEGER,
    total_score REAL NOT NULL,
    scope_score REAL NOT NULL DEFAULT 0.0,
    family_score REAL NOT NULL DEFAULT 0.0,
    root_score REAL NOT NULL DEFAULT 0.0,
    sparse_score REAL NOT NULL DEFAULT 0.0,
    dense_score REAL NOT NULL DEFAULT 0.0,
    text_overlap_score REAL NOT NULL DEFAULT 0.0,
    example_score REAL NOT NULL DEFAULT 0.0,
    env_score REAL NOT NULL DEFAULT 0.0,
    success_prior_score REAL NOT NULL DEFAULT 0.0,
    recency_score REAL NOT NULL DEFAULT 0.0,
    session_penalty_score REAL NOT NULL DEFAULT 0.0,
    feature_json TEXT NOT NULL DEFAULT '{}',
    reason_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(retrieval_event_id) REFERENCES retrieval_events(id) ON DELETE CASCADE,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE SET NULL,
    FOREIGN KEY(variant_id) REFERENCES issue_variants(id) ON DELETE SET NULL,
    UNIQUE(retrieval_event_id, candidate_rank)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_candidates_event
    ON retrieval_candidates(retrieval_event_id);

CREATE TABLE IF NOT EXISTS feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retrieval_event_id INTEGER,
    retrieval_candidate_id INTEGER,
    pattern_id INTEGER,
    variant_id INTEGER,
    episode_id INTEGER,
    feedback_type TEXT NOT NULL
        CHECK(feedback_type IN (
            'candidate_accepted',
            'candidate_rejected',
            'fix_verified',
            'false_positive',
            'merge_confirmed',
            'merge_rejected',
            'split_confirmed',
            'split_rejected'
        )),
    reward REAL NOT NULL DEFAULT 0.0,
    actor TEXT NOT NULL DEFAULT 'user'
        CHECK(actor IN ('user', 'agent', 'system')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(retrieval_event_id) REFERENCES retrieval_events(id) ON DELETE SET NULL,
    FOREIGN KEY(retrieval_candidate_id) REFERENCES retrieval_candidates(id) ON DELETE SET NULL,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE SET NULL,
    FOREIGN KEY(variant_id) REFERENCES issue_variants(id) ON DELETE SET NULL,
    FOREIGN KEY(episode_id) REFERENCES issue_episodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_created
    ON feedback_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_events_type
    ON feedback_events(feedback_type);

CREATE TABLE IF NOT EXISTS session_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project_scope TEXT NOT NULL DEFAULT 'global',
    repo_name TEXT NOT NULL DEFAULT '',
    memory_key TEXT NOT NULL,
    memory_value_json TEXT NOT NULL DEFAULT '{}',
    salience REAL NOT NULL DEFAULT 0.50,
    ttl_seconds INTEGER NOT NULL DEFAULT 21600,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_session_memory_expires
    ON session_memory(expires_at);

CREATE TABLE IF NOT EXISTS memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type TEXT NOT NULL CHECK(src_type IN ('pattern', 'variant', 'episode')),
    src_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL
        CHECK(relation_type IN (
            'similar_to',
            'derived_from',
            'contradicts',
            'depends_on',
            'co_occurs_with'
        )),
    dst_type TEXT NOT NULL CHECK(dst_type IN ('pattern', 'variant', 'episode')),
    dst_id INTEGER NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(src_type, src_id, relation_type, dst_type, dst_id)
);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT NOT NULL CHECK(object_type IN ('pattern', 'variant')),
    object_id INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_dim INTEGER NOT NULL,
    vector_blob BLOB NOT NULL,
    norm REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(object_type, object_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_object
    ON embeddings(object_type, object_id);

CREATE VIRTUAL TABLE IF NOT EXISTS issue_variants_fts
USING fts5(search_text, content='issue_variants', content_rowid='id');

CREATE VIRTUAL TABLE IF NOT EXISTS issue_episodes_fts
USING fts5(search_text, content='issue_episodes', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS issue_variants_ai AFTER INSERT ON issue_variants BEGIN
  INSERT INTO issue_variants_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_variants_au AFTER UPDATE ON issue_variants BEGIN
  UPDATE issue_variants_fts SET search_text = new.search_text WHERE rowid = new.id;
END;

CREATE TRIGGER IF NOT EXISTS issue_variants_ad AFTER DELETE ON issue_variants BEGIN
  DELETE FROM issue_variants_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_ai AFTER INSERT ON issue_episodes BEGIN
  INSERT INTO issue_episodes_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_au AFTER UPDATE ON issue_episodes BEGIN
  UPDATE issue_episodes_fts SET search_text = new.search_text WHERE rowid = new.id;
END;

CREATE TRIGGER IF NOT EXISTS issue_episodes_ad AFTER DELETE ON issue_episodes BEGIN
  DELETE FROM issue_episodes_fts WHERE rowid = old.id;
END;
