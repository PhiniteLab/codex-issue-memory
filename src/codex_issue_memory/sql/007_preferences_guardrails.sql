CREATE TABLE IF NOT EXISTS preference_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL
        CHECK(scope_type IN ('global', 'repo', 'user')),
    scope_key TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT 'global',
    repo_name TEXT NOT NULL DEFAULT '',
    error_family TEXT NOT NULL DEFAULT '',
    strategy_key TEXT NOT NULL DEFAULT '',
    weight REAL NOT NULL DEFAULT 0.12,
    instruction TEXT NOT NULL,
    condition_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'user_prompt',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_preference_rules_unique
    ON preference_rules(scope_type, scope_key, project_scope, repo_name, error_family, strategy_key, instruction, source);

CREATE INDEX IF NOT EXISTS idx_preference_rules_lookup
    ON preference_rules(scope_type, scope_key, project_scope, repo_name, active, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_preference_rules_strategy
    ON preference_rules(strategy_key, error_family, updated_at DESC);

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('preferences_guardrails', 'enabled', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
