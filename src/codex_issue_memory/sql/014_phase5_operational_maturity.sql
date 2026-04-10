-- Phase 5: Operational Maturity
-- 5.2: Per-stage latency tracking columns on retrieval_events
ALTER TABLE retrieval_events ADD COLUMN retrieval_latency_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE retrieval_events ADD COLUMN ranking_latency_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE retrieval_events ADD COLUMN bandit_latency_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE retrieval_events ADD COLUMN decision_latency_ms INTEGER NOT NULL DEFAULT 0;

-- 5.4: Batch learning safety gate — pending feedback queue
CREATE TABLE IF NOT EXISTS feedback_batch_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    retrieval_event_id  INTEGER NOT NULL,
    retrieval_candidate_id INTEGER,
    pattern_id      INTEGER,
    variant_id      INTEGER,
    feedback_type   TEXT NOT NULL,
    reward          REAL NOT NULL,
    actor           TEXT NOT NULL DEFAULT 'user',
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | applied | discarded
    created_at      TEXT NOT NULL,
    applied_at      TEXT,
    FOREIGN KEY(retrieval_event_id) REFERENCES retrieval_events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feedback_batch_queue_status
    ON feedback_batch_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_batch_queue_pattern
    ON feedback_batch_queue(pattern_id, feedback_type, status);
