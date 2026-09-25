import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import db
from bot import license


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        self.enterContext(patch.object(db._local, "conn", conn, create=True))
        self.enterContext(patch.object(config, "UPLOADS_DIR", Path(self.tmp.name)))
        self.codes = self.enterContext(patch.object(config, "activation_codes", return_value=("trial-a", "permanent-a")))
        self.clock = self.enterContext(patch.object(license, "_now", return_value=100000))
        db.init_db()

    def test_first_use_and_repeat_do_not_extend(self):
        self.assertFalse(license.is_valid())
        self.assertTrue(license.apply_code("trial-a")[0])
        self.assertEqual(license.state()["expires"], 186400)
        self.clock.return_value = 110000
        self.assertTrue(license.apply_code("trial-a")[0])
        self.assertEqual(license.state()["expires"], 186400)
        self.assertEqual(license.active_member_cap(), 0)
        self.assertFalse(license.trial_full())

    def test_expiry_clear_reset_and_clock_rollback(self):
        license.apply_code("trial-a")
        self.clock.return_value = 186400
        self.assertFalse(license.is_valid())
        license.clear()
        self.assertFalse(license.apply_code("trial-a")[0])
        db.reset_all_data()
        self.clock.return_value = 100001
        self.assertFalse(license.apply_code("trial-a")[0])

    def test_new_trial_and_reinstating_old_code(self):
        license.apply_code("trial-a")
        self.clock.return_value = 200000
        self.codes.return_value = ("trial-b", "permanent-a")
        self.assertTrue(license.apply_code("trial-b")[0])
        self.assertEqual(license.state()["expires"], 286400)
        self.codes.return_value = ("trial-a", "permanent-a")
        self.assertFalse(license.apply_code("trial-a")[0])
        self.assertTrue(license.is_valid())

    def test_permanent_and_switching_back_to_used_trial(self):
        license.apply_code("trial-a")
        self.assertTrue(license.apply_code("permanent-a")[0])
        self.clock.return_value = 999999999
        self.assertTrue(license.is_valid())
        self.assertEqual(license.state()["expires"], 0)
        self.assertFalse(license.apply_code("trial-a")[0])
        self.assertTrue(license.is_valid())

    def test_missing_duplicate_and_invalid_codes_fail_closed(self):
        self.assertFalse(license.apply_code("bad")[0])
        self.codes.return_value = ("", "")
        self.assertFalse(license.apply_code("")[0])
        self.assertFalse(license.is_valid())
        self.codes.return_value = ("same", "same")
        self.assertFalse(license.apply_code("same")[0])

    def test_dashboard_gate_after_expiry(self):
        from dashboard.app import app
        client = app.test_client()
        self.assertEqual(client.get("/activate").status_code, 200)
        license.apply_code("trial-a")
        self.clock.return_value = 186400
        response = client.get("/pending")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/activate", response.location)


if __name__ == "__main__":
    unittest.main()
