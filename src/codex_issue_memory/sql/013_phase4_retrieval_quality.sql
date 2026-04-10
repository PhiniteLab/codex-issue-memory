-- Phase 4.1: IDF-based token prioritization
CREATE TABLE IF NOT EXISTS token_idf (
    token TEXT PRIMARY KEY,
    doc_count INTEGER NOT NULL DEFAULT 1,
    idf_score REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT NOT NULL
);

-- Phase 4.3: Entity slot importance learning
CREATE TABLE IF NOT EXISTS entity_importance (
    entity_key TEXT NOT NULL,
    error_family TEXT NOT NULL DEFAULT '',
    importance_weight REAL NOT NULL DEFAULT 1.0,
    match_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    positive_outcome_count INTEGER NOT NULL DEFAULT 0,
    negative_outcome_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity_key, error_family)
);
