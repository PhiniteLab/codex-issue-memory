-- Phase 2.1: Implicit rejection detection
-- Add has_feedback flag to retrieval_events for tracking implicit rejections
ALTER TABLE retrieval_events ADD COLUMN has_feedback INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_retrieval_events_has_feedback
    ON retrieval_events(has_feedback, created_at);

-- Phase 2.3: Cross-session preference learning
-- Track per-user rejection counts across sessions
CREATE TABLE IF NOT EXISTS user_rejection_stats (
    user_scope TEXT NOT NULL DEFAULT '',
    pattern_id INTEGER NOT NULL,
    variant_id INTEGER NOT NULL DEFAULT 0,
    rejection_count INTEGER NOT NULL DEFAULT 0,
    last_rejected_at TEXT NOT NULL,
    PRIMARY KEY(user_scope, pattern_id, variant_id),
    FOREIGN KEY(pattern_id) REFERENCES issue_patterns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_rejection_stats_count
    ON user_rejection_stats(rejection_count DESC);

-- Rebuild feedback_events to add 'implicit_ignore' to the CHECK constraint
CREATE TABLE IF NOT EXISTS feedback_events_new (
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
            'split_rejected',
            'implicit_ignore'
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

INSERT INTO feedback_events_new
    SELECT * FROM feedback_events;

DROP TABLE feedback_events;

ALTER TABLE feedback_events_new RENAME TO feedback_events;

CREATE INDEX IF NOT EXISTS idx_feedback_events_created
    ON feedback_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_events_type
    ON feedback_events(feedback_type);
