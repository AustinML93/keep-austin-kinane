"""
The operational endpoints require a magic-link token.

They didn't always: /api/poll and /api/nags/run shipped open, which meant
anyone on the internet could make this box hammer its sources on demand —
sources this project is deliberately polite to. The endpoints are exercised as
plain functions (no TestClient) so nothing here touches the network or starts
the background threads, and the tests still fail if the auth check is removed.
"""

import os
import tempfile
import unittest
from pathlib import Path

try:
    from api import main as _probe  # noqa: F401 — import fails without fastapi/pywebpush
    HAVE_API_DEPS = True
except Exception:
    HAVE_API_DEPS = False


@unittest.skipUnless(HAVE_API_DEPS, "fastapi/pywebpush not installed")
class TestOpsEndpointsRequireAuth(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        from api import main
        self.main = main
        from fastapi import HTTPException
        self.HTTPException = HTTPException
        con = _db.connect()
        self.token = _db.add_user(con, "mike", "Mike")
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def assert401(self, fn, **kw):
        with self.assertRaises(self.HTTPException) as caught:
            fn(**kw)
        self.assertEqual(caught.exception.status_code, 401)

    def test_force_poll_rejects_strangers(self):
        self.assert401(self.main.force_poll, payload={}, authorization=None)

    def test_force_nags_rejects_strangers(self):
        self.assert401(self.main.force_nags, payload={}, authorization=None)

    def test_backup_download_rejects_strangers(self):
        self.assert401(self.main.backup_latest, authorization=None, token=None)

    def test_a_real_token_gets_past_the_gate(self):
        """404 (no snapshots in a fresh database), NOT 401 — auth held."""
        with self.assertRaises(self.HTTPException) as caught:
            self.main.backup_latest(authorization=None, token=self.token)
        self.assertEqual(caught.exception.status_code, 404)

    def test_nags_dry_run_works_when_authed(self):
        out = self.main.force_nags(dry_run=True, payload={"token": self.token},
                                   authorization=None)
        self.assertIn("nags", out)


if __name__ == "__main__":
    unittest.main()
