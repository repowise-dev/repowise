from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from repowise.core.fsutils import atomic_write_text


class TestAtomicWriteText(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_atomic_write_creates_new_file_with_umask_permissions(self) -> None:
        """A newly created file should be subject to the process umask, not hardcoded to 0600."""
        target = self.tmp_path / "new_file.txt"
        atomic_write_text(target, "hello world\n")

        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "hello world\n")

        mode = stat.S_IMODE(target.stat().st_mode)
        cur_umask = os.umask(0)
        os.umask(cur_umask)
        expected_mode = 0o666 & ~cur_umask
        self.assertEqual(mode, expected_mode)

    def test_atomic_write_preserves_existing_file_permissions(self) -> None:
        """Rewriting an existing file must keep its existing file permissions intact."""
        target = self.tmp_path / "existing_file.txt"
        target.write_text("initial\n", encoding="utf-8")
        target.chmod(0o664)

        atomic_write_text(target, "updated content\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "updated content\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)

    def test_atomic_write_follows_symlinks(self) -> None:
        """Atomic write to a symlink should write to the underlying target without breaking the symlink."""
        real_target = self.tmp_path / "real_file.txt"
        real_target.write_text("initial\n", encoding="utf-8")
        symlink_path = self.tmp_path / "symlink_file.txt"
        symlink_path.symlink_to(real_target)

        atomic_write_text(symlink_path, "updated through symlink\n")

        self.assertTrue(symlink_path.is_symlink())
        self.assertEqual(symlink_path.read_text(encoding="utf-8"), "updated through symlink\n")
        self.assertEqual(real_target.read_text(encoding="utf-8"), "updated through symlink\n")


if __name__ == "__main__":
    unittest.main()
