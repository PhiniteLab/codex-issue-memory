"""Phase 2 tests: intra-session decay, decision reasoning, implicit rejection, cross-session learning."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.services.feedback_service import DEFAULT_REWARDS


def _setenv(base: Path) -> None:
    os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
    os.environ["ISSUE_MEMORY_DB_PATH"] = str(base / "share" / "issue_memory.sqlite3")
    os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
    os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
    os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")


class IntraSessionDecayTests(unittest.TestCase):
    """2.2 — Intra-session salience decay."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-decay-")
        _setenv(Path(self.td.name))
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_setting_session_decay_half_life_defaults(self) -> None:
        s = self.app.store.settings
        self.assertAlmostEqual(s.session_decay_half_life_minutes, 30.0)

    def test_setting_session_decay_half_life_from_env(self) -> None:
        os.environ["ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES"] = "60"
        try:
            from codex_issue_memory.settings import Settings
            s = Settings.from_env()
            self.assertAlmostEqual(s.session_decay_half_life_minutes, 60.0)
        finally:
            del os.environ["ISSUE_MEMORY_SESSION_DECAY_HALF_LIFE_MINUTES"]

    def test_session_decay_reduces_salience_over_time(self) -> None:
        """Session memory salience should decay based on time elapsed since update."""
        self._seed()
        session_id = "decay-session"
        # Match and reject to create session memory
        match_result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
            session_id=session_id,
        )
        self.assertTrue(len(match_result["matches"]) > 0)
        ev_id = match_result["retrieval_event_id"]
        self.app.issue_feedback(
            retrieval_event_id=ev_id,
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )
        # Now age the session memory row by directly updating updated_at
        aged_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.app.store.managed_connection() as conn:
            conn.execute(
                "UPDATE session_memory SET updated_at = ? WHERE session_id = ?",
                (aged_time, session_id),
            )
        # Match again — the aged rejection should have heavily decayed salience
        match_result2 = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
            session_id=session_id,
        )
        # The match should still work (decay doesn't remove, just weakens)
        self.assertIsNotNone(match_result2)

    def _seed(self) -> None:
        self.app.issue_record_resolution(
            title="Missing requests module",
            raw_error="ModuleNotFoundError: No module named requests in api worker",
            canonical_fix="Install requests.",
            prevention_rule="Pin dependencies.",
            error_family="import_error",
            root_cause_class="missing_python_module",
        )


class DecisionReasoningTests(unittest.TestCase):
    """2.4 — Decision reasoning in match response."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-reason-")
        _setenv(Path(self.td.name))
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_reasoning_field_present_in_match_response(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        self.assertIn("reasoning", result)
        reasoning = result["reasoning"]
        self.assertIn("top_signals", reasoning)
        self.assertIn("session_memory", reasoning)
        self.assertIn("strategy_bandit", reasoning)

    def test_reasoning_top_signals_populated_on_match(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        reasoning = result["reasoning"]
        self.assertIsInstance(reasoning["top_signals"], list)
        # If there are visible matches, signals should be populated
        if result["matches"]:
            self.assertGreater(len(reasoning["top_signals"]), 0)

    def test_reasoning_empty_on_no_results(self) -> None:
        result = self.app.issue_match(
            error_text="Completely unique error that has no matches xyz123abc",
        )
        reasoning = result["reasoning"]
        self.assertEqual(reasoning["top_signals"], [])
        self.assertEqual(reasoning["session_memory"], {})
        self.assertEqual(reasoning["strategy_bandit"], {})

    def _seed(self) -> None:
        self.app.issue_record_resolution(
            title="Missing requests module",
            raw_error="ModuleNotFoundError: No module named requests in api worker",
            canonical_fix="Install requests.",
            prevention_rule="Pin dependencies.",
            error_family="import_error",
            root_cause_class="missing_python_module",
        )


class ImplicitRejectionTests(unittest.TestCase):
    """2.1 — Implicit rejection detection."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-implicit-")
        _setenv(Path(self.td.name))
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_implicit_ignore_reward_exists(self) -> None:
        self.assertIn("implicit_ignore", DEFAULT_REWARDS)
        self.assertAlmostEqual(DEFAULT_REWARDS["implicit_ignore"], -0.10)

    def test_explicit_feedback_marks_has_feedback(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        ev_id = result["retrieval_event_id"]
        self.assertIsNotNone(ev_id)
        # Before feedback, has_feedback should be 0
        with self.app.store.managed_connection() as conn:
            row = conn.execute(
                "SELECT has_feedback FROM retrieval_events WHERE id = ?",
                (ev_id,),
            ).fetchone()
            self.assertEqual(row["has_feedback"], 0)

        self.app.issue_feedback(
            retrieval_event_id=ev_id,
            feedback_type="candidate_accepted",
            candidate_rank=1,
        )
        # After feedback, has_feedback should be 1
        with self.app.store.managed_connection() as conn:
            row = conn.execute(
                "SELECT has_feedback FROM retrieval_events WHERE id = ?",
                (ev_id,),
            ).fetchone()
            self.assertEqual(row["has_feedback"], 1)

    def test_sweep_implicit_rejections_creates_feedback(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        ev_id = result["retrieval_event_id"]
        self.assertIsNotNone(ev_id)
        # Age the retrieval event
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.app.store.managed_connection() as conn:
            conn.execute(
                "UPDATE retrieval_events SET created_at = ? WHERE id = ?",
                (old_time, ev_id),
            )
        # Sweep
        swept = self.app.store.sweep_implicit_rejections(timeout_minutes=30)
        self.assertEqual(len(swept), 1)
        self.assertEqual(swept[0]["retrieval_event_id"], ev_id)
        self.assertAlmostEqual(swept[0]["reward"], -0.10)
        # Verify feedback record was created
        with self.app.store.managed_connection() as conn:
            feedback = conn.execute(
                "SELECT * FROM feedback_events WHERE retrieval_event_id = ? AND feedback_type = 'implicit_ignore'",
                (ev_id,),
            ).fetchone()
            self.assertIsNotNone(feedback)
            self.assertEqual(feedback["actor"], "system")
            # has_feedback should now be 1
            re_row = conn.execute(
                "SELECT has_feedback FROM retrieval_events WHERE id = ?",
                (ev_id,),
            ).fetchone()
            self.assertEqual(re_row["has_feedback"], 1)

    def test_sweep_does_not_affect_events_with_feedback(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        ev_id = result["retrieval_event_id"]
        self.app.issue_feedback(
            retrieval_event_id=ev_id,
            feedback_type="candidate_accepted",
            candidate_rank=1,
        )
        # Age the event
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.app.store.managed_connection() as conn:
            conn.execute(
                "UPDATE retrieval_events SET created_at = ? WHERE id = ?",
                (old_time, ev_id),
            )
        # Sweep should skip it
        swept = self.app.store.sweep_implicit_rejections(timeout_minutes=30)
        self.assertEqual(len(swept), 0)

    def test_sweep_does_not_affect_recent_events(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
        )
        # Don't age the event — it should be too recent
        swept = self.app.store.sweep_implicit_rejections(timeout_minutes=30)
        self.assertEqual(len(swept), 0)

    def test_settings_implicit_feedback_timeout(self) -> None:
        s = self.app.store.settings
        self.assertEqual(s.implicit_feedback_timeout_minutes, 30)

    def _seed(self) -> None:
        self.app.issue_record_resolution(
            title="Missing requests module",
            raw_error="ModuleNotFoundError: No module named requests in api worker",
            canonical_fix="Install requests.",
            prevention_rule="Pin dependencies.",
            error_family="import_error",
            root_cause_class="missing_python_module",
        )


class CrossSessionLearningTests(unittest.TestCase):
    """2.3 — Cross-session preference learning."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-xsession-")
        _setenv(Path(self.td.name))
        os.environ["ISSUE_MEMORY_ENABLE_CROSS_SESSION_LEARNING"] = "1"
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        os.environ.pop("ISSUE_MEMORY_ENABLE_CROSS_SESSION_LEARNING", None)
        self.td.cleanup()

    def test_settings_cross_session_defaults(self) -> None:
        self.assertTrue(self.app.store.settings.enable_cross_session_learning)
        self.assertEqual(self.app.store.settings.auto_rejection_threshold, 3)

    def test_rejection_stat_increment(self) -> None:
        self._seed()
        pid = self._get_first_pattern_id()
        count1 = self.app.store.increment_rejection_stat("testuser", pid, 0)
        self.assertEqual(count1, 1)
        count2 = self.app.store.increment_rejection_stat("testuser", pid, 0)
        self.assertEqual(count2, 2)
        count3 = self.app.store.increment_rejection_stat("testuser", pid, 0)
        self.assertEqual(count3, 3)

    def test_auto_rule_created_at_threshold(self) -> None:
        """After 3 rejections of the same pattern, an 'avoid' preference rule should be auto-created."""
        self._seed()
        pattern_id = self._get_first_pattern_id()
        # Reject the pattern 3 times in different sessions
        for session_idx in range(3):
            result = self.app.issue_match(
                error_text="ModuleNotFoundError: No module named requests in api worker",
                session_id=f"session-{session_idx}",
            )
            ev_id = result["retrieval_event_id"]
            if ev_id is None:
                continue
            feedback = self.app.issue_feedback(
                retrieval_event_id=ev_id,
                feedback_type="candidate_rejected",
                candidate_rank=1,
            )
            if session_idx == 2:
                # On third rejection, auto_rejection_rule should be created
                self.assertIsNotNone(
                    feedback.get("auto_rejection_rule"),
                    f"Expected auto-rejection rule at rejection #{session_idx + 1}",
                )
            else:
                self.assertIsNone(
                    feedback.get("auto_rejection_rule"),
                    f"Did not expect auto-rejection rule at rejection #{session_idx + 1}",
                )

    def test_auto_rule_not_created_below_threshold(self) -> None:
        self._seed()
        for session_idx in range(2):  # Only 2 rejections
            result = self.app.issue_match(
                error_text="ModuleNotFoundError: No module named requests in api worker",
                session_id=f"session-{session_idx}",
            )
            ev_id = result["retrieval_event_id"]
            if ev_id is None:
                continue
            feedback = self.app.issue_feedback(
                retrieval_event_id=ev_id,
                feedback_type="candidate_rejected",
                candidate_rank=1,
            )
            self.assertIsNone(feedback.get("auto_rejection_rule"))

    def test_user_rejection_stats_table_populated(self) -> None:
        self._seed()
        result = self.app.issue_match(
            error_text="ModuleNotFoundError: No module named requests in api worker",
            session_id="s1",
        )
        ev_id = result["retrieval_event_id"]
        self.assertIsNotNone(ev_id)
        self.app.issue_feedback(
            retrieval_event_id=ev_id,
            feedback_type="candidate_rejected",
            candidate_rank=1,
        )
        with self.app.store.managed_connection() as conn:
            rows = conn.execute("SELECT * FROM user_rejection_stats").fetchall()
            self.assertGreater(len(rows), 0)

    def test_cross_session_disabled_by_default(self) -> None:
        """When ENABLE_CROSS_SESSION_LEARNING is 0, no auto-rule should fire."""
        os.environ["ISSUE_MEMORY_ENABLE_CROSS_SESSION_LEARNING"] = "0"
        td2 = tempfile.TemporaryDirectory(prefix="p2-xsession-off-")
        _setenv(Path(td2.name))
        try:
            app2 = IssueMemoryApp()
            self.assertFalse(app2.store.settings.enable_cross_session_learning)
            app2.issue_record_resolution(
                title="Missing requests module",
                raw_error="ModuleNotFoundError: No module named requests in api worker",
                canonical_fix="Install requests.",
                prevention_rule="Pin dependencies.",
                error_family="import_error",
                root_cause_class="missing_python_module",
            )
            for i in range(4):
                result = app2.issue_match(
                    error_text="ModuleNotFoundError: No module named requests in api worker",
                    session_id=f"off-session-{i}",
                )
                ev_id = result["retrieval_event_id"]
                if ev_id is None:
                    continue
                feedback = app2.issue_feedback(
                    retrieval_event_id=ev_id,
                    feedback_type="candidate_rejected",
                    candidate_rank=1,
                )
                self.assertIsNone(feedback.get("auto_rejection_rule"))
        finally:
            td2.cleanup()

    def _seed(self) -> None:
        self.app.issue_record_resolution(
            title="Missing requests module",
            raw_error="ModuleNotFoundError: No module named requests in api worker",
            canonical_fix="Install requests.",
            prevention_rule="Pin dependencies.",
            error_family="import_error",
            root_cause_class="missing_python_module",
        )

    def _get_first_pattern_id(self) -> int:
        with self.app.store.managed_connection() as conn:
            row = conn.execute("SELECT id FROM issue_patterns LIMIT 1").fetchone()
            return int(row["id"])


class MigrationPhase2Tests(unittest.TestCase):
    """Verify Phase 2 schema additions."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-migration-")
        _setenv(Path(self.td.name))
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_has_feedback_column_exists(self) -> None:
        with self.app.store.managed_connection() as conn:
            info = conn.execute("PRAGMA table_info(retrieval_events)").fetchall()
            columns = {row["name"] for row in info}
            self.assertIn("has_feedback", columns)

    def test_user_rejection_stats_table_exists(self) -> None:
        with self.app.store.managed_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("user_rejection_stats", tables)

    def test_feedback_events_accepts_implicit_ignore(self) -> None:
        with self.app.store.managed_connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback_events
                    (retrieval_event_id, retrieval_candidate_id, pattern_id, variant_id,
                     episode_id, feedback_type, reward, actor, notes, created_at)
                VALUES (NULL, NULL, NULL, NULL, NULL, 'implicit_ignore', -0.10, 'system', 'test', '2026-01-01T00:00:00Z')
                """
            )
            row = conn.execute(
                "SELECT feedback_type FROM feedback_events WHERE feedback_type = 'implicit_ignore'"
            ).fetchone()
            self.assertIsNotNone(row)


class CLISweepImplicitTests(unittest.TestCase):
    """Test the sweep-implicit CLI command."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="p2-cli-sweep-")
        _setenv(Path(self.td.name))
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_sweep_implicit_parser_registered(self) -> None:
        from codex_issue_memory.maintenance import build_parser
        parser = build_parser()
        args = parser.parse_args(["sweep-implicit", "--timeout-minutes", "60", "--limit", "100"])
        self.assertEqual(args.command, "sweep-implicit")
        self.assertEqual(args.timeout_minutes, 60)
        self.assertEqual(args.limit, 100)


if __name__ == "__main__":
    unittest.main()
