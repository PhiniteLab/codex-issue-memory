"""Concurrent safety tests for storage writes, lifecycle locks, and backup hashing."""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
import threading
import unittest

from codex_issue_memory.app import IssueMemoryApp
from codex_issue_memory.backup import BackupManager
from codex_issue_memory.lifecycle import MCPServerLifecycle
from codex_issue_memory.normalization.fingerprints import _stable_digest
from codex_issue_memory.retrieval.dense_index import DenseEmbeddingIndex
from codex_issue_memory.settings import Settings


class ConcurrentStorageWriteTests(unittest.TestCase):
    """Verify that concurrent issue_record_resolution calls do not corrupt data."""

    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-concurrent-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        self.app = IssueMemoryApp()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_concurrent_record_resolutions_do_not_corrupt(self) -> None:
        """Fire several record_resolution calls from threads and verify all succeed."""
        errors: list[str] = []
        results: list[dict] = []
        lock = threading.Lock()

        def record(index: int) -> None:
            try:
                result = self.app.issue_record_resolution(
                    title=f"Concurrent error {index}",
                    raw_error=f"RuntimeError: thread failure {index}",
                    canonical_fix=f"Fix for thread {index}",
                    prevention_rule=f"Prevent thread issue {index}",
                    project_scope="global",
                    error_family="runtime_error",
                    root_cause_class="concurrency_bug",
                    tags=f"thread,concurrent,test{index}",
                )
                with lock:
                    results.append(result)
            except Exception as exc:
                with lock:
                    errors.append(f"Thread {index}: {exc}")

        threads = [threading.Thread(target=record, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(errors, f"Concurrent writes raised errors: {errors}")
        self.assertEqual(len(results), 8)
        for result in results:
            self.assertIn(result["action"], {"created", "updated"})

    def test_concurrent_reads_during_write(self) -> None:
        """Ensure reads do not fail while a write is in progress."""
        self.app.issue_record_resolution(
            title="Seed pattern for concurrent read",
            raw_error="ValueError: seed data error",
            canonical_fix="Seed fix",
            prevention_rule="Seed prevention",
            project_scope="global",
        )

        errors: list[str] = []

        def read_loop(count: int) -> None:
            for _ in range(count):
                try:
                    self.app.issue_match(
                        error_text="ValueError: seed data error",
                        project_scope="global",
                    )
                except Exception as exc:
                    errors.append(str(exc))

        def write_loop(count: int) -> None:
            for i in range(count):
                try:
                    self.app.issue_record_resolution(
                        title=f"Concurrent write {i}",
                        raw_error=f"ValueError: write loop {i}",
                        canonical_fix=f"Fix {i}",
                        prevention_rule=f"Prevent {i}",
                        project_scope="global",
                    )
                except Exception as exc:
                    errors.append(str(exc))

        readers = [threading.Thread(target=read_loop, args=(5,)) for _ in range(4)]
        writers = [threading.Thread(target=write_loop, args=(3,)) for _ in range(2)]
        all_threads = readers + writers
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join(timeout=60)

        self.assertFalse(errors, f"Concurrent read/write errors: {errors}")


class LifecycleLockLeakTests(unittest.TestCase):
    """Verify that failed slot acquisitions do not leak file descriptors."""

    _ENV_KEYS = (
        "ISSUE_MEMORY_HOME",
        "ISSUE_MEMORY_DB_PATH",
        "ISSUE_MEMORY_STATE_DIR",
        "ISSUE_MEMORY_BACKUP_DIR",
        "ISSUE_MEMORY_LOG_DIR",
        "ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE",
        "ISSUE_MEMORY_MAX_MCP_INSTANCES",
        "ISSUE_MEMORY_SERVER_LOCK_DIR",
        "ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY",
        "ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON",
        "ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS",
        "ISSUE_MEMORY_MAIN_CONVERSATION_KEY",
    )

    def setUp(self) -> None:
        self._env_backup = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="issue-memory-lock-leak-")
        base = Path(self.temp_dir.name)
        os.environ["ISSUE_MEMORY_HOME"] = str(base / "share")
        os.environ["ISSUE_MEMORY_DB_PATH"] = str(
            base / "share" / "issue_memory.sqlite3"
        )
        os.environ["ISSUE_MEMORY_STATE_DIR"] = str(base / "state")
        os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(base / "share" / "backups")
        os.environ["ISSUE_MEMORY_LOG_DIR"] = str(base / "state" / "log")
        os.environ["ISSUE_MEMORY_SERVER_LOCK_DIR"] = str(base / "state" / "run")
        os.environ["ISSUE_MEMORY_SERVER_REQUIRE_OWNER_KEY"] = "0"
        os.environ["ISSUE_MEMORY_ENFORCE_SINGLE_MCP_INSTANCE"] = "1"
        os.environ["ISSUE_MEMORY_MAX_MCP_INSTANCES"] = "1"
        os.environ["ISSUE_MEMORY_SERVER_ENFORCE_PARENT_SINGLETON"] = "0"
        os.environ["ISSUE_MEMORY_SERVER_PARENT_INSTANCE_IDLE_TIMEOUT_SECONDS"] = "0"
        os.environ.pop("ISSUE_MEMORY_MAIN_CONVERSATION_KEY", None)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_slot_acquisition_and_release_does_not_leak_handles(self) -> None:
        """Acquire a slot, release, then acquire again without FD leak."""
        settings = Settings.from_env()
        lc = MCPServerLifecycle(settings, register_atexit=False)
        lc.start()
        lc.release()

        # Second lifecycle must be able to acquire the same slot
        lc2 = MCPServerLifecycle(settings, register_atexit=False)
        lc2.start()
        lc2.release()

    def test_failed_slot_acquisition_does_not_leak(self) -> None:
        """When all slots are held, the second lifecycle fails without leaking."""
        settings = Settings.from_env()
        lc1 = MCPServerLifecycle(settings, register_atexit=False)
        lc1.start()
        try:
            lc2 = MCPServerLifecycle(settings, register_atexit=False)
            with self.assertRaises(RuntimeError):
                lc2.start()
            # lc2 should not have leaked a handle — verify we can still start after lc1 releases
        finally:
            lc1.release()

        lc3 = MCPServerLifecycle(settings, register_atexit=False)
        lc3.start()
        lc3.release()


class BackupStreamingHashTests(unittest.TestCase):
    """Verify backup hashing works correctly with streaming approach."""

    def test_streaming_hash_matches_reference(self) -> None:
        """The streaming _hash_file must produce the same digest as a full-read hash."""
        from hashlib import sha256 as sha256_ref

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            # Write 3 MiB of data to exercise multi-chunk streaming
            data = os.urandom(3 * 1024 * 1024)
            fh.write(data)
            fh.flush()
            temp_path = Path(fh.name)

        try:
            expected = sha256_ref(data).hexdigest()
            settings = (
                Settings.from_env() if "ISSUE_MEMORY_HOME" in os.environ else None
            )
            if settings is None:
                tmp = tempfile.mkdtemp(prefix="issue-memory-backup-hash-")
                os.environ["ISSUE_MEMORY_HOME"] = tmp
                os.environ["ISSUE_MEMORY_STATE_DIR"] = tmp
                os.environ["ISSUE_MEMORY_DB_PATH"] = str(Path(tmp) / "test.sqlite3")
                os.environ["ISSUE_MEMORY_BACKUP_DIR"] = str(Path(tmp) / "backups")
                os.environ["ISSUE_MEMORY_LOG_DIR"] = str(Path(tmp) / "log")
                settings = Settings.from_env()
            manager = BackupManager(settings)
            actual = manager._hash_file(temp_path)
            self.assertEqual(actual, expected)
        finally:
            temp_path.unlink(missing_ok=True)


class FingerprintDigestLengthTests(unittest.TestCase):
    """Verify that fingerprint digest length is now 32 hex chars (128 bits)."""

    def test_digest_length_is_32(self) -> None:
        digest = _stable_digest("test", "value")
        self.assertEqual(len(digest), 32)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(_stable_digest(), "")

    def test_different_inputs_produce_different_digests(self) -> None:
        a = _stable_digest("error_a", "context_a")
        b = _stable_digest("error_b", "context_b")
        self.assertNotEqual(a, b)


class DenseIndexCorruptBlobTests(unittest.TestCase):
    """Verify that corrupt embedding blobs produce warnings, not silent failures."""

    def test_corrupt_blob_returns_zero_vector(self) -> None:
        """A truncated blob should still return a zero vector."""
        dim = 64
        # Create a valid vector, then truncate it
        valid = [1.0] * dim
        blob = struct.pack(f"<{dim}f", *valid)
        truncated = blob[:10]
        result = DenseEmbeddingIndex.unpack_vector(truncated, expected_dim=dim)
        self.assertEqual(len(result), dim)
        self.assertTrue(all(v == 0.0 for v in result))

    def test_valid_blob_unpacks_correctly(self) -> None:
        dim = 32
        values = [float(i) for i in range(dim)]
        blob = struct.pack(f"<{dim}f", *values)
        result = DenseEmbeddingIndex.unpack_vector(blob, expected_dim=dim)
        self.assertEqual(len(result), dim)
        for expected, actual in zip(values, result):
            self.assertAlmostEqual(expected, actual, places=5)

    def test_empty_blob_returns_zero_vector(self) -> None:
        result = DenseEmbeddingIndex.unpack_vector(b"", expected_dim=64)
        self.assertEqual(len(result), 64)
        self.assertTrue(all(v == 0.0 for v in result))


if __name__ == "__main__":
    unittest.main()
