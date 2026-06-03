"""Tests for Sprint 3-B pre-flight safety gates in cmd_simulate_ops_run."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tw_day_trading_lab.cli import check_git_clean, check_ops_lock


class TestCheckGitClean(unittest.TestCase):
    """3-B-1: git clean check helper."""

    def _make_run_result(self, returncode: int, stdout: str) -> MagicMock:
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        return result

    def test_git_clean_returns_clean_status(self) -> None:
        """Empty git status stdout → clean=True, no uncommitted files."""
        # subprocess is imported inside check_git_clean; patch at the stdlib level
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._make_run_result(0, ""),   # git status --porcelain → clean
                self._make_run_result(0, ""),   # git diff --check → no issues
            ]
            result = check_git_clean("/tmp")

        self.assertTrue(result["clean"])
        self.assertEqual(result["uncommitted_files"], [])
        self.assertFalse(result["whitespace_issues"])

    def test_git_dirty_returns_uncommitted_files(self) -> None:
        """Non-empty git status stdout → clean=False with files listed."""
        porcelain_output = " M src/tw_day_trading_lab/cli.py\n?? new_file.py\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._make_run_result(0, porcelain_output),  # git status
                self._make_run_result(0, ""),                # git diff --check
            ]
            result = check_git_clean("/tmp")

        self.assertFalse(result["clean"])
        self.assertIn("src/tw_day_trading_lab/cli.py", result["uncommitted_files"])
        self.assertIn("new_file.py", result["uncommitted_files"])

    def test_git_dirty_adds_blocked_reason(self) -> None:
        """Dirty repo → blocked_reasons should include 'uncommitted_changes'."""
        porcelain_output = " M some_file.py\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._make_run_result(0, porcelain_output),
                self._make_run_result(0, ""),
            ]
            result = check_git_clean("/tmp")

        # Simulate what cmd_simulate_ops_run does
        preflight_blocked: list = []
        if not result["clean"]:
            preflight_blocked.append("uncommitted_changes")

        self.assertIn("uncommitted_changes", preflight_blocked)

    def test_git_whitespace_issues_detected(self) -> None:
        """git diff --check exits non-zero → whitespace_issues=True."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._make_run_result(0, ""),   # git status → clean
                self._make_run_result(1, ""),   # git diff --check → whitespace
            ]
            result = check_git_clean("/tmp")

        self.assertTrue(result["whitespace_issues"])
        # Still clean because no uncommitted_files from status
        self.assertTrue(result["clean"])

    def test_git_subprocess_exception_handled(self) -> None:
        """If subprocess raises, the function returns safe defaults (no crash)."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = check_git_clean("/tmp")

        self.assertTrue(result["clean"])
        self.assertEqual(result["uncommitted_files"], [])
        self.assertFalse(result["whitespace_issues"])


class TestCheckOpsLock(unittest.TestCase):
    """3-B-2: lock file detection helper."""

    def test_lock_file_not_present(self) -> None:
        """No lock file → locked=False."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".ops.lock"
            result = check_ops_lock(lock_path)

        self.assertFalse(result["locked"])
        self.assertEqual(result["lock_file"], str(lock_path))

    def test_lock_file_detection(self) -> None:
        """Existing .ops.lock file → locked=True."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".ops.lock"
            lock_path.write_text('{"run_id": "ops-test"}', encoding="utf-8")

            result = check_ops_lock(lock_path)

        self.assertTrue(result["locked"])
        self.assertEqual(result["lock_file"], str(lock_path))

    def test_lock_file_path_returned(self) -> None:
        """lock_file key always holds the string path regardless of existence."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".ops.lock"
            result = check_ops_lock(lock_path)
            self.assertEqual(result["lock_file"], str(lock_path))

    def test_lock_blocked_reason_added(self) -> None:
        """Locked file → simulate that blocked_reasons gets 'ops_lock_file_exists'."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".ops.lock"
            lock_path.write_text("locked", encoding="utf-8")

            lock_status = check_ops_lock(lock_path)

        preflight_blocked: list = []
        if lock_status["locked"]:
            preflight_blocked.append("ops_lock_file_exists")

        self.assertIn("ops_lock_file_exists", preflight_blocked)


if __name__ == "__main__":
    unittest.main()
