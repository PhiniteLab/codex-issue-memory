DROP TABLE IF EXISTS contextual_bandit_state;
DROP TABLE IF EXISTS ranker_state;

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('learning_state_cleanup', 'dropped', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
