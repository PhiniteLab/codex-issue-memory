CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER,
    variant_id INTEGER,
    episode_id INTEGER,
    project_scope TEXT NOT NULL DEFAULT 'global',
    user_scope TEXT NOT NULL DEFAULT '',
    repo_name TEXT NOT NULL DEFAULT '',
    strategy_key TEXT NOT NULL DEFAULT '',
    review_reason TEXT NOT NULL DEFAULT '',
    entity_slots_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected', 'archived')),
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE SET NULL,
    FOREIGN KEY(variant_id) REFERENCES issue_variants(id) ON DELETE SET NULL,
    FOREIGN KEY(episode_id) REFERENCES issue_episodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_review_queue_status_created
    ON review_queue(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_review_queue_variant
    ON review_queue(variant_id, status, created_at DESC);

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('review_queue_ops', 'enabled', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
