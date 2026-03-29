ALTER TABLE issue_variants ADD COLUMN strategy_key TEXT NOT NULL DEFAULT '';
ALTER TABLE issue_variants ADD COLUMN entity_slots_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE issue_variants ADD COLUMN strategy_hints_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE issue_episodes ADD COLUMN user_scope TEXT NOT NULL DEFAULT '';
ALTER TABLE issue_episodes ADD COLUMN entity_slots_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE issue_episodes ADD COLUMN strategy_hints_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE retrieval_events ADD COLUMN user_scope TEXT NOT NULL DEFAULT '';
ALTER TABLE retrieval_events ADD COLUMN entity_slots_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE retrieval_events ADD COLUMN strategy_hints_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_issue_variants_strategy_key
    ON issue_variants(strategy_key);
CREATE INDEX IF NOT EXISTS idx_issue_episodes_user_scope
    ON issue_episodes(user_scope);
CREATE INDEX IF NOT EXISTS idx_retrieval_events_user_scope
    ON retrieval_events(user_scope);

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('strategy_foundation', 'enabled', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
