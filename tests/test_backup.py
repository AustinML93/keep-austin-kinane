"""
Backups.

Losing the database costs more than it looks: the VAPID keypair (regenerating it
invalidates every push subscription on both phones), both magic-link tokens,
every bit rating and the Holds up list built from them, the ticket decisions, and
the whole nag history. None of it is recoverable from the sources.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class TestBackup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "kak.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike", True)
        self.db.set_setting(self.con, "vapid_private_pem", "a-secret-key")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_snapshot_contains_the_unrecoverable_things(self):
        dest = self.db.backup(self.con)
        self.assertTrue(dest.exists())

        copy = sqlite3.connect(dest)
        copy.row_factory = sqlite3.Row
        try:
            key = copy.execute(
                "SELECT value FROM settings WHERE key='vapid_private_pem'").fetchone()
            self.assertEqual(key["value"], "a-secret-key")
            user = copy.execute("SELECT * FROM users WHERE id='mike'").fetchone()
            self.assertTrue(user["token"])
        finally:
            copy.close()

    def test_snapshot_captures_writes_still_in_the_wal(self):
        """
        The real reason to use sqlite3's backup API rather than copying the file:
        in WAL mode recent commits live in the -wal, and a plain cp that catches
        one without the other is a backup that only fails when you need it.
        """
        self.db.add_user(self.con, "rob", "Rob")
        dest = self.db.backup(self.con)
        copy = sqlite3.connect(dest)
        try:
            n = copy.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(n, 2)
        finally:
            copy.close()

    def test_old_snapshots_are_pruned(self):
        """A backup directory that grows forever becomes the thing that fills the disk."""
        for _ in range(5):
            self.db.backup(self.con, keep=3)
        kept = list((Path(self.tmp.name) / "backups").glob("kak-*.sqlite3"))
        self.assertLessEqual(len(kept), 3)

    def test_the_backup_is_a_usable_database_not_just_bytes(self):
        dest = self.db.backup(self.con)
        copy = sqlite3.connect(dest)
        try:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            copy.close()


if __name__ == "__main__":
    unittest.main()
