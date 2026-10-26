"""Fixes from a Codex review of the GNOME port: replies of an unexpected shape, CLIP errors,
a pairing cancelled while its request runs, a worker round that throws, a FIFO config."""
import importlib.util
import os
import shutil
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hue_fix", os.path.join(HERE, "..", "scripts", "hue-lights.py"))
hue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hue)

PIN = {"ip": "192.168.1.50", "bridge_id": "001788FFFE4A2B3C", "fingerprint": "a" * 64,
       "app_key": "abcDEF-123_x", "name": "Hue", "model": "BSB003"}


class Quiet(hue.Hue):
    """A Hue without stdout and without the user's config."""
    def __init__(self, config=None):
        self.emitted = []
        hue.load_config = lambda: dict(config or {})
        super().__init__()

    def emit(self):
        self.emitted.append(self.snapshot())


class Fixes(unittest.TestCase):
    def setUp(self):
        self.real = (hue.https_request, hue.load_config, hue.save_config)

    def tearDown(self):
        hue.https_request, hue.load_config, hue.save_config = self.real

    def test_resources_that_are_not_objects_are_skipped(self):
        self.assertEqual(hue.build_model([None, 3, "x", []]), ([], 0, 0))
        self.assertEqual(hue.build_model({"not": "a list"}), ([], 0, 0))

    def test_clip_errors_are_failures(self):
        hue.https_request = lambda *a, **k: (200, {"errors": [{"description": "<b>device busy</b>"}], "data": []}, None)
        h = Quiet(PIN)
        with self.assertRaises(hue.BridgeError) as raised:
            h.api("PUT", "/clip/v2/resource/light/x", {})
        self.assertNotIn("device busy", str(raised.exception))   # the bridge's text never travels on
        self.assertNotIn("<", str(raised.exception))

    def test_unexpected_reply_is_an_error_not_a_crash(self):
        for reply in ([1, 2], {"data": [None, {"type": "light", "id": "11111111-1111-1111-1111-111111111111",
                                                "dimming": {"brightness": "bright"}}]}, {"data": "x"}):
            hue.https_request = lambda *a, r=reply, **k: (200, r, None)
            h = Quiet(PIN)
            h.refresh()
            self.assertEqual(h.phase, "error", reply)
            self.assertEqual(h.error_code, "errReply", reply)

    def test_cancel_during_pairing_saves_nothing(self):
        saved = []
        hue.save_config = lambda data: saved.append(data)
        h = Quiet()

        def request(ip, method, path, body=None, **kw):
            peer = ("f" * 64, "001788FFFE4A2B3C")
            if method == "GET":
                return 200, {"bridgeid": "001788FFFE4A2B3C", "name": "Hue"}, peer
            h.handle({"cmd": "cancel_pair"})          # the user cancels while the POST runs
            return 200, [{"success": {"username": "key123"}}], peer
        hue.https_request = request
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        self.assertEqual(saved, [])
        self.assertEqual(h.config, {})
        self.assertEqual(h.phase, "unpaired")

    def test_pairing_still_works(self):
        saved = []
        hue.save_config = lambda data: saved.append(data)
        hue.https_request = lambda ip, method, path, body=None, **kw: (
            (200, {"bridgeid": "001788FFFE4A2B3C"}, ("f" * 64, "001788FFFE4A2B3C")) if method == "GET"
            else (200, [{"success": {"username": "key123"}}], ("f" * 64, "001788FFFE4A2B3C")))
        h = Quiet()
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        self.assertEqual(len(saved), 1)
        self.assertEqual(h.phase, "connecting")

    def test_worker_survives_a_failing_round(self):
        h = Quiet()
        rounds = []

        def work_once():
            rounds.append(1)
            if len(rounds) == 1:
                raise RuntimeError("boom")
            raise SystemExit   # end the test's worker thread on the second round
        h.work_once = work_once
        hue.time.sleep, real_sleep = (lambda s: None), hue.time.sleep
        try:
            thread = threading.Thread(target=lambda: self.assertRaises(SystemExit, h.worker))
            thread.start()
            thread.join(5)
        finally:
            hue.time.sleep = real_sleep
        self.assertEqual(len(rounds), 2)
        self.assertEqual(h.error_code, "errInternal")


class FifoConfig(unittest.TestCase):
    def test_fifo_config_does_not_block(self):
        directory = tempfile.mkdtemp()
        old = (hue.CONFIG_DIR, hue.CONFIG_PATH)
        hue.CONFIG_DIR = directory
        hue.CONFIG_PATH = os.path.join(directory, "config.json")
        os.mkfifo(hue.CONFIG_PATH)
        result = []
        try:
            thread = threading.Thread(target=lambda: result.append(hue.load_config()), daemon=True)
            thread.start()
            thread.join(3)
            self.assertEqual(result, [{}])
        finally:
            hue.CONFIG_DIR, hue.CONFIG_PATH = old
            shutil.rmtree(directory)


if __name__ == "__main__":
    unittest.main()
