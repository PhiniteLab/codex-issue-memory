CREATE TABLE IF NOT EXISTS strategy_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL
        CHECK(scope_type IN ('global', 'repo', 'user')),
    scope_key TEXT NOT NULL DEFAULT '',
    strategy_key TEXT NOT NULL,
    alpha REAL NOT NULL DEFAULT 2.0,
    beta REAL NOT NULL DEFAULT 2.0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_key, strategy_key)
);

CREATE INDEX IF NOT EXISTS idx_strategy_stats_lookup
    ON strategy_stats(scope_type, scope_key, strategy_key);

CREATE INDEX IF NOT EXISTS idx_strategy_stats_strategy
    ON strategy_stats(strategy_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS variant_stats (
    variant_id INTEGER PRIMARY KEY,
    alpha REAL NOT NULL DEFAULT 1.0,
    beta REAL NOT NULL DEFAULT 1.0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(variant_id) REFERENCES issue_variants(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_variant_stats_updated
    ON variant_stats(updated_at DESC);

INSERT OR IGNORE INTO variant_stats(
    variant_id, alpha, beta, success_count, failure_count, updated_at
)
SELECT
    v.id,
    1.0 + CAST(v.success_count AS REAL),
    1.0 + CAST(v.reject_count AS REAL),
    v.success_count,
    v.reject_count,
    COALESCE(v.updated_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
FROM issue_variants AS v;

INSERT OR IGNORE INTO strategy_stats(
    scope_type, scope_key, strategy_key, alpha, beta, success_count, failure_count, updated_at
)
SELECT
    'global',
    '',
    v.strategy_key,
    2.0 + CAST(SUM(v.success_count) AS REAL),
    2.0 + CAST(SUM(v.reject_count) AS REAL),
    SUM(v.success_count),
    SUM(v.reject_count),
    MAX(COALESCE(v.updated_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))
FROM issue_variants AS v
WHERE v.strategy_key <> ''
GROUP BY v.strategy_key;

INSERT OR REPLACE INTO app_metadata(key, value, updated_at)
VALUES ('thompson_stats', 'enabled', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
