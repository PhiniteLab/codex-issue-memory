CREATE TABLE IF NOT EXISTS issue_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    project_scope TEXT NOT NULL DEFAULT 'global',
    domain TEXT NOT NULL DEFAULT 'generic',
    error_family TEXT NOT NULL,
    root_cause_class TEXT NOT NULL,
    canonical_symptom TEXT NOT NULL,
    canonical_fix TEXT NOT NULL,
    prevention_rule TEXT NOT NULL,
    verification_steps TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 0.70,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_scope, signature)
);

CREATE INDEX IF NOT EXISTS idx_issue_patterns_scope
    ON issue_patterns(project_scope);
CREATE INDEX IF NOT EXISTS idx_issue_patterns_family
    ON issue_patterns(error_family);
CREATE INDEX IF NOT EXISTS idx_issue_patterns_root
    ON issue_patterns(root_cause_class);
CREATE INDEX IF NOT EXISTS idx_issue_patterns_updated
    ON issue_patterns(updated_at DESC);

CREATE TABLE IF NOT EXISTS issue_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL,
    raw_error TEXT NOT NULL,
    normalized_error TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    verified_fix TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_issue_examples_pattern
    ON issue_examples(pattern_id);
CREATE INDEX IF NOT EXISTS idx_issue_examples_created
    ON issue_examples(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS issue_patterns_fts USING fts5(
    title,
    canonical_symptom,
    canonical_fix,
    prevention_rule,
    tags,
    root_cause_class,
    error_family,
    domain
);

CREATE VIRTUAL TABLE IF NOT EXISTS issue_examples_fts USING fts5(
    raw_error,
    normalized_error,
    context,
    command,
    file_path,
    verified_fix
);
