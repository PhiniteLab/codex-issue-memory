CREATE TABLE IF NOT EXISTS feature_outcome_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_event_id   INTEGER NOT NULL,
    retrieval_candidate_id INTEGER NOT NULL,
    feature_name    TEXT NOT NULL,
    feature_value   REAL NOT NULL,
    feedback_type   TEXT NOT NULL,
    reward          REAL NOT NULL,
    error_family    TEXT NOT NULL DEFAULT '',
    project_scope   TEXT NOT NULL DEFAULT 'global',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_outcome_feature_name
    ON feature_outcome_log(feature_name);
CREATE INDEX IF NOT EXISTS idx_feature_outcome_feedback_type
    ON feature_outcome_log(feedback_type);
CREATE INDEX IF NOT EXISTS idx_feature_outcome_created_at
    ON feature_outcome_log(created_at);
