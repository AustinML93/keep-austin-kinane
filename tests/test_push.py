"""
Push plumbing. The VAPID key format is the whole test.
"""

import os
import tempfile
import unittest
from pathlib import Path

try:
    import py_vapid  # noqa: F401
    from cryptography.hazmat.primitives import serialization  # noqa: F401
    HAVE_CRYPTO = True
except Exception:
    HAVE_CRYPTO = False


@unittest.skipUnless(HAVE_CRYPTO, "needs cryptography + py_vapid")
class TestVapidKeyFormat(unittest.TestCase):
    """
    pywebpush hands the key string to py_vapid.Vapid01.from_string, which does
    NOT understand PEM — it strips newlines, base64url-decodes the whole thing
    including the header line, and hands the garbage to the DER parser. Every
    send then dies with an ASN.1 error before touching the network.

    We shipped a PEM. Every push failed. The app reported "failed=2".
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        from api import push as _push
        importlib.reload(_push)
        self.db, self.push = _db, _push
        self.con = _db.connect()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_generated_key_is_raw_not_pem(self):
        priv, pub = self.push.ensure_vapid(self.con)
        self.assertNotIn("-----BEGIN", priv)
        self.assertEqual(len(pub), 87, "public key should be an 87-char b64url P-256 point")

    def test_generated_key_is_accepted_by_py_vapid(self):
        """The actual contract: the library must be able to load what we store."""
        from py_vapid import Vapid01
        priv, _ = self.push.ensure_vapid(self.con)
        self.assertIsNotNone(Vapid01.from_string(priv))

    def test_an_existing_pem_is_migrated_in_place(self):
        """
        Converted, NOT regenerated. A new key would invalidate every push
        subscription already registered on a phone, meaning both users would
        have to re-enable notifications.
        """
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        self.db.set_setting(self.con, self.push.VAPID_PRIVATE, pem)
        self.db.set_setting(self.con, self.push.VAPID_PUBLIC, "placeholder")

        priv, _ = self.push.ensure_vapid(self.con)
        self.assertNotIn("-----BEGIN", priv)

        # Same key, so existing subscriptions keep working.
        from py_vapid import Vapid01
        loaded = Vapid01.from_string(priv)
        self.assertEqual(
            loaded.private_key.private_numbers().private_value,
            key.private_numbers().private_value)

    def test_migration_is_persisted(self):
        """Not just converted on the fly — written back, so it happens once."""
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        self.db.set_setting(self.con, self.push.VAPID_PRIVATE, pem)
        self.db.set_setting(self.con, self.push.VAPID_PUBLIC, "placeholder")
        self.push.ensure_vapid(self.con)
        stored = self.db.get_setting(self.con, self.push.VAPID_PRIVATE)
        self.assertNotIn("-----BEGIN", stored)


class TestSubscriptionErrorRecording(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["KAK_DB"] = str(Path(self.tmp.name) / "t.sqlite3")
        import importlib

        from api import db as _db
        importlib.reload(_db)
        self.db = _db
        self.con = _db.connect()
        self.db.add_user(self.con, "mike", "Mike")
        self.db.save_subscription(self.con, "mike", {
            "endpoint": "https://fcm.example/abc",
            "keys": {"p256dh": "p", "auth": "a"}}, "test-agent")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_failures_keep_the_reason(self):
        """A count alone can't distinguish a dead phone from an unparseable key."""
        self.db.mark_subscription_failed(self.con, "https://fcm.example/abc", "ValueError: nope")
        s = self.db.subscriptions_for(self.con, "mike")[0]
        self.assertEqual(s["failures"], 1)
        self.assertIn("ValueError", s["last_error"])

    def test_success_clears_the_error(self):
        self.db.mark_subscription_failed(self.con, "https://fcm.example/abc", "boom")
        self.db.mark_subscription_ok(self.con, "https://fcm.example/abc")
        s = self.db.subscriptions_for(self.con, "mike")[0]
        self.assertEqual(s["failures"], 0)
        self.assertIsNone(s["last_error"])
        self.assertIsNotNone(s["last_ok_at"])


if __name__ == "__main__":
    unittest.main()
