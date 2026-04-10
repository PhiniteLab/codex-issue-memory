-- Phase 3.1: Multi-factor posteriors for strategy_stats
-- quality_posterior: fix_verified → quality (solution quality)
-- safety_posterior: non-FP → safety (false-positive avoidance)
-- adoption_posterior: accepted → adoption (usage breadth)

ALTER TABLE strategy_stats ADD COLUMN quality_alpha REAL NOT NULL DEFAULT 2.0;
ALTER TABLE strategy_stats ADD COLUMN quality_beta REAL NOT NULL DEFAULT 2.0;
ALTER TABLE strategy_stats ADD COLUMN safety_alpha REAL NOT NULL DEFAULT 2.0;
ALTER TABLE strategy_stats ADD COLUMN safety_beta REAL NOT NULL DEFAULT 2.0;
ALTER TABLE strategy_stats ADD COLUMN adoption_alpha REAL NOT NULL DEFAULT 2.0;
ALTER TABLE strategy_stats ADD COLUMN adoption_beta REAL NOT NULL DEFAULT 2.0;

-- Backfill quality/safety from existing alpha/beta (single-factor posterior)
UPDATE strategy_stats
SET quality_alpha = alpha,
    quality_beta = beta,
    safety_alpha = alpha,
    safety_beta = beta;

-- Phase 3.2: Strategy family hierarchy
CREATE TABLE IF NOT EXISTS strategy_families (
    family_key TEXT PRIMARY KEY,
    strategy_keys_json TEXT NOT NULL DEFAULT '[]',
    quality_alpha REAL NOT NULL DEFAULT 2.0,
    quality_beta REAL NOT NULL DEFAULT 2.0,
    safety_alpha REAL NOT NULL DEFAULT 2.0,
    safety_beta REAL NOT NULL DEFAULT 2.0,
    adoption_alpha REAL NOT NULL DEFAULT 2.0,
    adoption_beta REAL NOT NULL DEFAULT 2.0,
    updated_at TEXT NOT NULL
);

-- Phase 3.3: A/B test framework
CREATE TABLE IF NOT EXISTS experiment_registry (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    treatment_config_json TEXT NOT NULL DEFAULT '{}',
    control_config_json TEXT NOT NULL DEFAULT '{}',
    traffic_fraction REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'running', 'paused', 'completed', 'cancelled')),
    start_date TEXT,
    end_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE retrieval_events ADD COLUMN experiment_id TEXT NOT NULL DEFAULT '';
ALTER TABLE retrieval_events ADD COLUMN experiment_arm TEXT NOT NULL DEFAULT '';
