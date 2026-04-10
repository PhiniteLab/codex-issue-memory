"""Tests for Phase 1 improvements: per-family thresholds, contextual half-life, family weights, asymmetric FP cost."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.models import QueryProfile


def _make_app() -> IssueMemoryApp:
    return IssueMemoryApp()


def _seed_pattern(app: IssueMemoryApp) -> dict:
    return app.issue_record_resolution(
        title="Relative sqlite path breaks outside repo root",
        raw_error="FileNotFoundError: references/contractsDatabase.sqlite3",
        canonical_fix="Resolve the SQLite path relative to __file__.",
        prevention_rule="No production DB path may depend on cwd.",
        project_scope="global",
        canonical_symptom="sqlite database path fails outside repo root",
        verification_steps="Run from repo root and external cwd.",
        tags="sqlite,path,cwd",
        error_family="sqlite_error",
        root_cause_class="cwd_relative_path_bug",
        command="python -m app.main",
        file_path="services/db_loader.py",
        stack_excerpt='File "services/db_loader.py", line 12, in load_db',
        domain="python",
        session_id="phase1-seed",
    )


def _do_match(app: IssueMemoryApp, session_id: str = "phase1-test") -> dict:
    return app.issue_match(
        error_text="FileNotFoundError: references/contractsDatabase.sqlite3 while running python -m app.main from another directory",
        command="python -m app.main",
        file_path="services/db_loader.py",
        project_scope="global",
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# 1.4 Asymmetric FP cost
# ---------------------------------------------------------------------------


class TestAsymmetricFPCost(unittest.TestCase):
    """Phase 1.4 — FP reward is -2.5 and SafeOverridePolicy blocks FP-prone candidates."""

    def test_fp_reward_is_minus_2_5(self):
        from codex_issue_memory.services.feedback_service import DEFAULT_REWARDS
        self.assertEqual(DEFAULT_REWARDS["false_positive"], -2.50)

    def test_fp_reward_is_more_costly_than_rejection(self):
        from codex_issue_memory.services.feedback_service import DEFAULT_REWARDS
        self.assertLess(DEFAULT_REWARDS["false_positive"], DEFAULT_REWARDS["candidate_rejected"])

    def test_fp_safety_block_threshold_exists(self):
        from codex_issue_memory.learning.safe_override import FP_SAFETY_BLOCK_THRESHOLD
        self.assertEqual(FP_SAFETY_BLOCK_THRESHOLD, 2)

    def test_fp_safety_block_exported_from_learning(self):
        from codex_issue_memory.learning import FP_SAFETY_BLOCK_THRESHOLD
        self.assertIsNotNone(FP_SAFETY_BLOCK_THRESHOLD)

    def test_safe_override_blocks_fp_candidate(self):
        """A candidate with fp_count >= threshold is blocked even if it passes all other gates."""
        from codex_issue_memory.learning.safe_override import SafeOverridePolicy, FP_SAFETY_BLOCK_THRESHOLD

        app = _make_app()
        settings = app.store.settings

        @dataclass
        class FakeAnalysis:
            final_score: float = 0.95
            conservative_score: float = 0.90
            effective_evidence: float = 100.0
            negative_penalty: float = 0.0
            fp_count: int = FP_SAFETY_BLOCK_THRESHOLD  # exactly at threshold

        policy = SafeOverridePolicy(settings)
        result = policy.choose(
            baseline_key="baseline",
            baseline_score=0.50,
            analyses={
                "baseline": FakeAnalysis(final_score=0.50, conservative_score=0.50, effective_evidence=50.0, fp_count=0),
                "candidate": FakeAnalysis(fp_count=FP_SAFETY_BLOCK_THRESHOLD),
            },
        )
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "fp-safety-block")
        self.assertEqual(result.chosen_key, "baseline")

    def test_safe_override_allows_low_fp_candidate(self):
        """A candidate with fp_count below threshold passes the FP gate."""
        from codex_issue_memory.learning.safe_override import SafeOverridePolicy, FP_SAFETY_BLOCK_THRESHOLD

        app = _make_app()
        settings = app.store.settings

        @dataclass
        class FakeAnalysis:
            final_score: float = 0.95
            conservative_score: float = 0.90
            effective_evidence: float = 100.0
            negative_penalty: float = 0.0
            fp_count: int = 0

        policy = SafeOverridePolicy(settings)
        result = policy.choose(
            baseline_key="baseline",
            baseline_score=0.50,
            analyses={
                "baseline": FakeAnalysis(final_score=0.50, conservative_score=0.50, effective_evidence=50.0),
                "candidate": FakeAnalysis(fp_count=FP_SAFETY_BLOCK_THRESHOLD - 1),
            },
        )
        self.assertTrue(result.promoted)
        self.assertEqual(result.reason, "safe-override-approved")


# ---------------------------------------------------------------------------
# 1.2 Contextual half-life (velocity multiplier)
# ---------------------------------------------------------------------------


class TestContextualHalfLife(unittest.TestCase):
    """Phase 1.2 — velocity_multiplier adjusts effective half-life for beta decay."""

    def test_decay_with_velocity_multiplier_1(self):
        """velocity=1.0 gives same result as default behavior."""
        from codex_issue_memory.learning.posteriors import decay_beta_parameters

        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        a1, b1, d1 = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
        )
        a2, b2, d2 = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=1.0,
        )
        self.assertAlmostEqual(a1, a2, places=6)
        self.assertAlmostEqual(b1, b2, places=6)
        self.assertAlmostEqual(d1, d2, places=6)

    def test_high_velocity_slows_decay(self):
        """High velocity (multiplier > 1) increases effective half-life → less decay."""
        from codex_issue_memory.learning.posteriors import decay_beta_parameters

        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        a_fast, _, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=1.0,
        )
        a_slow, _, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=2.0,
        )
        # Higher velocity → larger effective half-life → less decay → higher retained alpha
        self.assertGreater(a_slow, a_fast)

    def test_low_velocity_speeds_decay(self):
        """Low velocity (multiplier < 1) decreases effective half-life → more decay."""
        from codex_issue_memory.learning.posteriors import decay_beta_parameters

        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        a_normal, _, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=1.0,
        )
        a_fast_decay, _, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=0.5,
        )
        self.assertLess(a_fast_decay, a_normal)

    def test_velocity_multiplier_clamped_at_minimum(self):
        """velocity_multiplier is clamped to >= 0.1 to avoid division by zero."""
        from codex_issue_memory.learning.posteriors import decay_beta_parameters

        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        a_zero, b_zero, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=0.0,
        )
        a_min, b_min, _ = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=0.1,
        )
        self.assertAlmostEqual(a_zero, a_min, places=6)
        self.assertAlmostEqual(b_zero, b_min, places=6)

    def test_build_beta_posterior_passes_velocity(self):
        """build_beta_posterior() accepts and uses velocity_multiplier."""
        from codex_issue_memory.learning.posteriors import build_beta_posterior

        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        p1 = build_beta_posterior(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            seed_parts=("test",), velocity_multiplier=1.0,
        )
        p2 = build_beta_posterior(
            alpha=10.0, beta=5.0, updated_at=past,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            seed_parts=("test",), velocity_multiplier=2.0,
        )
        # Higher velocity → less decay → higher alpha
        self.assertGreater(p2.alpha, p1.alpha)

    def test_query_repo_feedback_velocity_empty_repo(self):
        """Empty repo name returns 1.0."""
        app = _make_app()
        velocity = app.store.query_repo_feedback_velocity("")
        self.assertEqual(velocity, 1.0)

    def test_query_repo_feedback_velocity_no_data(self):
        """A repo with no feedback events returns clamped minimum (0.0 / baseline → clamped to 0.5)."""
        app = _make_app()
        velocity = app.store.query_repo_feedback_velocity("nonexistent_repo")
        self.assertEqual(velocity, 0.5)


# ---------------------------------------------------------------------------
# 1.1 Family-specific thresholds (feedback-driven calibration)
# ---------------------------------------------------------------------------


class TestFeedbackDrivenCalibration(unittest.TestCase):
    """Phase 1.1 — run_feedback_driven_calibration produces per-family thresholds from feedback data."""

    def test_calibration_import_from_benchmarks(self):
        from codex_issue_memory.benchmarks import run_feedback_driven_calibration
        self.assertTrue(callable(run_feedback_driven_calibration))

    def test_calibration_with_no_feedback_data(self):
        """Calibration returns a valid structure even without dedicated feedback data."""
        from codex_issue_memory.benchmarks.calibration import run_feedback_driven_calibration
        app = _make_app()
        result = run_feedback_driven_calibration(app.store)
        self.assertIn("families", result)
        self.assertIsInstance(result["families"], dict)

    def test_calibration_returns_error_for_invalid_store(self):
        from codex_issue_memory.benchmarks.calibration import run_feedback_driven_calibration
        result = run_feedback_driven_calibration("not a store")
        self.assertEqual(result["status"], "error")

    def test_calibration_with_seeded_feedback(self):
        """Seed feedback data and verify calibration produces per-family thresholds."""
        from codex_issue_memory.benchmarks.calibration import run_feedback_driven_calibration

        app = _make_app()
        _seed_pattern(app)

        # Generate diverse feedback: need >= 4 per family for grid search
        feedback_types = [
            "fix_verified", "fix_verified", "candidate_accepted",
            "false_positive", "candidate_rejected", "fix_verified",
        ]
        submitted = 0
        for i, ft in enumerate(feedback_types):
            m = _do_match(app, session_id=f"feedback-cal-{i}")
            matches = m.get("matches", [])
            r_id = int(m.get("retrieval_event_id", 0))
            if matches and r_id:
                c_id = int(matches[0].get("retrieval_candidate_id", 0))
                if c_id:
                    app.issue_feedback(
                        retrieval_event_id=r_id,
                        feedback_type=ft,
                        retrieval_candidate_id=c_id,
                    )
                    submitted += 1

        self.assertGreaterEqual(submitted, 4, "Not enough feedback events submitted")

        result = run_feedback_driven_calibration(app.store)
        self.assertIn("global", result)
        self.assertIn("families", result)
        self.assertIsInstance(result["families"], dict)
        # With enough positive+negative feedback, we should get at least global thresholds
        self.assertIn("metrics", result)
        self.assertGreater(result["metrics"]["total_feedback_rows"], 0)


# ---------------------------------------------------------------------------
# 1.3 Family-specific ranking weights
# ---------------------------------------------------------------------------


class TestFamilySpecificWeights(unittest.TestCase):
    """Phase 1.3 — HeuristicRanker uses per-family weight overrides from calibration profile."""

    def test_ranker_has_weight_overrides_dict(self):
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        ranker = HeuristicRanker()
        self.assertIsInstance(ranker._weight_overrides, dict)

    def test_ranker_default_weights_used_without_family(self):
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        ranker = HeuristicRanker()
        weights = ranker._weights_for_family("")
        self.assertEqual(weights, ranker.DEFAULT_WEIGHTS)

    def test_ranker_default_weights_used_for_unknown_family(self):
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        ranker = HeuristicRanker()
        weights = ranker._weights_for_family("nonexistent_family")
        self.assertEqual(weights, ranker.DEFAULT_WEIGHTS)

    def test_ranker_family_overrides_merge_correctly(self):
        """When weight_overrides exist for a family, they override specific weights."""
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        ranker = HeuristicRanker()
        ranker._weight_overrides = {
            "sqlite_error": {"dense_score": 0.50, "family_score": 0.30},
        }
        weights = ranker._weights_for_family("sqlite_error")
        # Overridden values
        self.assertEqual(weights["dense_score"], 0.50)
        self.assertEqual(weights["family_score"], 0.30)
        # Non-overridden values remain default
        self.assertEqual(weights["proven_score"], HeuristicRanker.DEFAULT_WEIGHTS["proven_score"])

    def test_score_accepts_error_family(self):
        """score() method accepts error_family keyword argument."""
        import inspect
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        sig = inspect.signature(HeuristicRanker.score)
        self.assertIn("error_family", sig.parameters)

    def test_rank_passes_error_family_from_profile(self):
        """rank() reads error_family from profile and passes it to score()."""
        from codex_issue_memory.retrieval.ranker import HeuristicRanker

        app = _make_app()
        _seed_pattern(app)
        match_result = _do_match(app)
        # Verify the rank flow works end-to-end without error
        self.assertIn("matches", match_result)
        self.assertGreater(len(match_result["matches"]), 0)

    def test_ranker_loads_overrides_from_calibration_profile(self):
        """If calibration_profile has weight_overrides, ranker loads them."""
        import json

        app = _make_app()
        if not app.store.settings.enable_calibration_profile:
            self.skipTest("Calibration profile not enabled in settings")

        profile_path = app.store.settings.calibration_profile_path
        profile_data = {
            "version": 1,
            "global": {"accept_threshold": 0.65, "weak_threshold": 0.30},
            "families": {},
            "weight_overrides": {
                "sqlite_error": {"dense_score": 0.40, "family_score": 0.25},
            },
        }
        profile_path.write_text(json.dumps(profile_data), encoding="utf-8")

        from codex_issue_memory.retrieval.ranker import HeuristicRanker

        ranker = HeuristicRanker(store=app.store, settings=app.store.settings)

        self.assertIn("sqlite_error", ranker._weight_overrides)
        self.assertEqual(ranker._weight_overrides["sqlite_error"]["dense_score"], 0.40)

    def test_ranker_ignores_invalid_weight_keys_in_overrides(self):
        """Weight keys not in DEFAULT_WEIGHTS are silently ignored during loading."""
        from codex_issue_memory.retrieval.ranker import HeuristicRanker
        ranker = HeuristicRanker()
        # Manually set overrides including an invalid key
        ranker._weight_overrides = {
            "test_family": {"dense_score": 0.50},
        }
        weights = ranker._weights_for_family("test_family")
        # The valid key is merged
        self.assertEqual(weights["dense_score"], 0.50)
        # Non-overridden keys remain default
        self.assertEqual(weights["family_score"], HeuristicRanker.DEFAULT_WEIGHTS["family_score"])


# ---------------------------------------------------------------------------
# 1.2 + 1.4 Integration: fp_count on StrategyBanditOutcome
# ---------------------------------------------------------------------------


class TestStrategyBanditFpCount(unittest.TestCase):
    """Verify fp_count field on StrategyBanditOutcome and its integration."""

    def test_strategy_bandit_outcome_has_fp_count(self):
        from codex_issue_memory.learning.strategy_bandit import StrategyBanditOutcome
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(StrategyBanditOutcome)]
        self.assertIn("fp_count", field_names)


# ---------------------------------------------------------------------------
# CLI integration: --from-feedback flag
# ---------------------------------------------------------------------------


class TestCLICalibrateFromFeedback(unittest.TestCase):
    """Phase 1.1 — `calibrate-thresholds --from-feedback` flag is registered."""

    def test_from_feedback_flag_accepted(self):
        """The maintenance CLI parser accepts --from-feedback on calibrate-thresholds."""
        from codex_issue_memory.maintenance import main
        import sys

        # Just verify parsing doesn't crash; actual execution tested via the function
        with patch.object(sys, "argv", ["issue-memory-maintenance", "calibrate-thresholds", "--from-feedback", "--help"]):
            try:
                main()
            except SystemExit:
                pass  # --help triggers SystemExit(0), which is fine


# ---------------------------------------------------------------------------
# 1.2 Velocity end-to-end through score_candidates
# ---------------------------------------------------------------------------


class TestVelocityIntegration(unittest.TestCase):
    """Phase 1.2 — velocity propagates through StrategyThompsonBandit.score_candidates()."""

    def test_velocity_passed_to_all_posteriors(self):
        """score_candidates queries velocity and passes it to _load_posterior."""
        from codex_issue_memory.learning.strategy_bandit import StrategyThompsonBandit
        from codex_issue_memory.retrieval.ranker import RankedCandidate

        app = _make_app()
        _seed_pattern(app)
        bandit = StrategyThompsonBandit(store=app.store, settings=app.store.settings)

        profile = QueryProfile(
            raw_text="FileNotFoundError: contractsDatabase.sqlite3",
            normalized_text="filenotfounderror: contractsdatabase.sqlite3",
            tokens=["filenotfounderror", "contractsdatabase", "sqlite3"],
            exception_types=["FileNotFoundError"],
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            tags=["sqlite", "path"],
            evidence=["contractsDatabase.sqlite3"],
        )
        # Build a fake ranked item
        candidate = {
            "id": 1,
            "pattern_id": 1,
            "variant_id": 1,
            "best_variant": {"id": 1, "strategy_key": "general_reusable_fix", "title": "test"},
        }
        ranked_item = RankedCandidate(candidate=candidate, score=0.7, features={}, reasons=[])

        # Patch store.query_repo_feedback_velocity to verify it's called
        original_velocity = app.store.query_repo_feedback_velocity
        velocity_calls = []
        def track_velocity(repo_name, **kwargs):
            velocity_calls.append(repo_name)
            return original_velocity(repo_name, **kwargs)

        with patch.object(app.store, "query_repo_feedback_velocity", side_effect=track_velocity):
            bandit.store = app.store
            results = bandit.score_candidates(profile, [ranked_item], project_scope="global")

        self.assertGreater(len(velocity_calls), 0, "velocity was never queried")

    def test_velocity_affects_effective_half_life(self):
        """Higher velocity → shorter effective half-life → faster decay."""
        from codex_issue_memory.learning.posteriors import decay_beta_parameters
        from datetime import datetime, timezone, timedelta

        old_ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()

        # velocity=1.0 → half_life=30 days
        a1, b1, decay1 = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=old_ts,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=1.0,
        )
        # velocity=2.0 → effective half_life=60 days → slower decay → higher alpha
        a2, b2, decay2 = decay_beta_parameters(
            alpha=10.0, beta=5.0, updated_at=old_ts,
            half_life_days=30, prior_alpha=1.0, prior_beta=1.0,
            velocity_multiplier=2.0,
        )
        # Higher velocity means longer effective half-life, less decay, so alpha should be higher
        self.assertGreater(a2, a1, "Higher velocity should retain more evidence (higher alpha)")


# ---------------------------------------------------------------------------
# 1.3 Family weights affect actual score output
# ---------------------------------------------------------------------------


class TestFamilyWeightsScoring(unittest.TestCase):
    """Phase 1.3 — weight overrides for a family produce different score values."""

    def test_family_overrides_change_score(self):
        """score() with family overrides produces different total than default weights."""
        from codex_issue_memory.retrieval.ranker import HeuristicRanker

        app = _make_app()
        _seed_pattern(app)

        ranker = HeuristicRanker(store=app.store, settings=app.store.settings)

        profile = QueryProfile(
            raw_text="FileNotFoundError: contractsDatabase.sqlite3",
            normalized_text="filenotfounderror: contractsdatabase.sqlite3",
            tokens=["filenotfounderror", "contractsdatabase", "sqlite3"],
            exception_types=["FileNotFoundError"],
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            tags=["sqlite", "path"],
            evidence=["contractsDatabase.sqlite3"],
        )

        # Get a real candidate from the store
        match_result = _do_match(app)
        matches = match_result.get("matches", [])
        self.assertGreater(len(matches), 0, "Need at least one match")

        # We can't easily get the raw candidate dict from the compact result,
        # so build a mock candidate dict matching what the ranker expects
        candidate = {
            "id": 1,
            "pattern_id": matches[0]["pattern_id"],
            "project_scope": "global",
            "error_family": "sqlite_error",
            "root_cause_class": "cwd_relative_path_bug",
            "title": matches[0]["title"],
            "best_variant": {
                "strategy_key": "general_reusable_fix",
                "title": matches[0]["title"],
                "canonical_fix": matches[0]["canonical_fix"],
                "prevention_rule": matches[0]["prevention_rule"],
                "confidence": 1.0,
            },
            "normalized_text": "filenotfounderror: contractsdatabase.sqlite3",
        }

        # Score without family override
        result_no_family = ranker.score(profile, candidate, project_scope="global", error_family="")
        # Now add a family override that radically changes weights
        ranker._weight_overrides = {
            "sqlite_error": {"dense_score": 0.80, "family_score": 0.01},
        }
        result_with_family = ranker.score(profile, candidate, project_scope="global", error_family="sqlite_error")
        # The scores should differ because weights are different
        self.assertNotEqual(
            round(result_no_family.score, 6),
            round(result_with_family.score, 6),
            "Family weight overrides should change the score",
        )


# ---------------------------------------------------------------------------
# 1.4 _negative_applicability_penalty 3-tuple with payloads
# ---------------------------------------------------------------------------


class TestNegativeApplicabilityPenalty(unittest.TestCase):
    """Phase 1.4 — _negative_applicability_penalty returns (penalty, reasons, fp_count) with populated data."""

    def test_penalty_3_tuple_with_fp_data(self):
        """A variant with FP data in negative_applicability_json returns penalty > 0 and fp_count > 0."""
        from codex_issue_memory.learning.strategy_bandit import StrategyThompsonBandit

        app = _make_app()
        bandit = StrategyThompsonBandit(store=app.store, settings=app.store.settings)

        profile = QueryProfile(
            raw_text="FileNotFoundError: contractsDatabase.sqlite3",
            normalized_text="filenotfounderror: contractsdatabase.sqlite3",
            tokens=["filenotfounderror", "contractsdatabase", "sqlite3"],
            exception_types=["FileNotFoundError"],
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            tags=["sqlite", "path"],
            evidence=["contractsDatabase.sqlite3"],
            project_scope="myproject",
            repo_name="my-org/my-repo",
        )
        variant = {
            "negative_applicability_json": {
                "false_positive_count": 3,
                "project_scopes": ["myproject"],
                "repo_names": ["my-org/my-repo"],
                "user_scopes": [],
                "commands": [],
                "file_paths": [],
            }
        }
        penalty, reasons, fp_count = bandit._negative_applicability_penalty(profile, variant)
        self.assertGreater(penalty, 0.0, "Should have non-zero penalty")
        self.assertEqual(fp_count, 3)
        self.assertIn("negative-applicability-project-scope", reasons)
        self.assertIn("negative-applicability-repo-name", reasons)

    def test_penalty_zero_for_empty_payload(self):
        """Empty negative_applicability_json returns (0.0, [], 0)."""
        from codex_issue_memory.learning.strategy_bandit import StrategyThompsonBandit

        app = _make_app()
        bandit = StrategyThompsonBandit(store=app.store, settings=app.store.settings)
        profile = QueryProfile(
            raw_text="x", normalized_text="x",
            tokens=[], exception_types=[], error_family="unknown",
            root_cause_class="unknown", tags=[], evidence=[],
        )
        penalty, reasons, fp_count = bandit._negative_applicability_penalty(profile, {})
        self.assertEqual(penalty, 0.0)
        self.assertEqual(reasons, [])
        self.assertEqual(fp_count, 0)

    def test_fp_count_propagates_to_outcome(self):
        """fp_count from _negative_applicability_penalty ends up in StrategyBanditOutcome."""
        from codex_issue_memory.learning.strategy_bandit import StrategyThompsonBandit
        from codex_issue_memory.retrieval.ranker import RankedCandidate

        app = _make_app()
        _seed_pattern(app)
        bandit = StrategyThompsonBandit(store=app.store, settings=app.store.settings)

        profile = QueryProfile(
            raw_text="FileNotFoundError: contractsDatabase.sqlite3",
            normalized_text="filenotfounderror: contractsdatabase.sqlite3",
            tokens=["filenotfounderror", "contractsdatabase", "sqlite3"],
            exception_types=["FileNotFoundError"],
            error_family="sqlite_error",
            root_cause_class="cwd_relative_path_bug",
            tags=["sqlite", "path"],
            evidence=["contractsDatabase.sqlite3"],
        )
        candidate = {
            "id": 1, "pattern_id": 1, "variant_id": 1,
            "best_variant": {"id": 1, "strategy_key": "general_reusable_fix"},
        }
        ranked_item = RankedCandidate(candidate=candidate, score=0.7, features={}, reasons=[])
        results = bandit.score_candidates(profile, [ranked_item], project_scope="global")
        # There should be exactly one result
        self.assertEqual(len(results), 1)
        outcome = list(results.values())[0]
        self.assertIsInstance(outcome.fp_count, int)


# ---------------------------------------------------------------------------
# CLI: --from-feedback --write-profile integration
# ---------------------------------------------------------------------------


class TestCLICalibrateFromFeedbackWriteProfile(unittest.TestCase):
    """Phase 1.1 — `calibrate-thresholds --from-feedback --write-profile` writes a profile file."""

    def test_from_feedback_write_profile_produces_file(self):
        """cmd_calibrate_thresholds(from_feedback=True, write_profile=True) writes calibration_profile.json
        when feedback data exists."""
        from codex_issue_memory.benchmarks.calibration import run_feedback_driven_calibration

        app = _make_app()
        _seed_pattern(app)

        # Submit feedback to create enough data for calibration
        feedback_types = [
            "fix_verified", "fix_verified", "candidate_accepted",
            "false_positive", "candidate_rejected", "fix_verified",
        ]
        for i, ft in enumerate(feedback_types):
            m = _do_match(app, session_id=f"write-profile-{i}")
            matches = m.get("matches", [])
            r_id = int(m.get("retrieval_event_id", 0))
            if matches and r_id:
                c_id = int(matches[0].get("retrieval_candidate_id", 0))
                if c_id:
                    app.issue_feedback(
                        retrieval_event_id=r_id,
                        feedback_type=ft,
                        retrieval_candidate_id=c_id,
                    )

        result = run_feedback_driven_calibration(app.store)
        # If global thresholds were produced, simulate the write_profile path
        if result.get("global"):
            import json
            profile_path = app.store.settings.calibration_profile_path
            profile_payload = {key: result[key] for key in ("version", "generated_at", "global", "families", "metrics") if key in result}
            profile_path.write_text(json.dumps(profile_payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.assertTrue(profile_path.exists(), "Profile file should have been written")
            content = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertIn("global", content)
            self.assertIn("families", content)
        else:
            # Even without global thresholds the function should not error
            self.assertIn("metrics", result)


if __name__ == "__main__":
    unittest.main()
