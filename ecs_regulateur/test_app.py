import unittest
from unittest.mock import patch
import app

class NetatmoTest(unittest.TestCase):
    def setUp(self):
        app.last_uncertain = False

    @patch("app.api")
    @patch("app.call")
    @patch("app.get")
    def test_unknown_no_cloud(self, get, call, api):
        get.return_value = "unavailable"
        self.assertFalse(app.cut())
        self.assertFalse(app.cut())
        call.assert_not_called()
        self.assertEqual(api.call_count, 1)

    @patch("app.api")
    @patch("app.call")
    @patch("app.get")
    def test_off_no_cloud(self, get, call, api):
        get.return_value = "off"
        get.side_effect = lambda e: "idle" if e == app.TIMER else "off"
        self.assertTrue(app.cut())
        call.assert_not_called()

    @patch("app.api")
    @patch("app.call")
    @patch("app.get")
    def test_shared_limiter(self, get, call, api):
        values = {app.SW: "on", app.CUT_SCRIPT: "off", app.CUT_PAUSE: "idle"}
        get.side_effect = values.__getitem__
        self.assertFalse(app.cut())
        call.assert_called_once_with("script/turn_on", app.CUT_SCRIPT)
        values[app.CUT_PAUSE] = "active"
        self.assertFalse(app.cut())
        self.assertEqual(call.call_count, 1)

    @patch("app.get")
    def test_confirm_start_only_reads(self, get):
        states = {app.TIMER: "active", app.ENABLE: "on", app.GUARD: "on",
                  app.PUMP: "off", app.BOOST: "off", app.FAULT: "off",
                  app.CUT_SCRIPT: "off", app.CUT_PAUSE: "idle", app.TEMP: "51",
                  app.COLOR: "blanc", app.PERIOD: "hp", app.SW: "on"}
        get.side_effect = states.__getitem__
        self.assertTrue(app.confirm_start())
        states[app.PUMP] = "on"
        self.assertFalse(app.confirm_start())
