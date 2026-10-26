"""Fixes from a Codex review of 0.4.1: no Python preexec_fn in a threaded helper, children ended
with the helper, and no network result started before Unpair or a new Pair applied after it."""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hue_stale", os.path.join(HERE, "..", "scripts", "hue-lights.py"))
hue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hue)

BRIDGE = "001788FFFE4A2B3C"
OTHER = "001788FFFE000001"
PIN = {"ip": "192.168.1.50", "bridge_id": BRIDGE, "fingerprint": "a" * 64,
       "app_key": "abcDEF-123_x", "name": "Hue", "model": "BSB003"}
ROOM = "22222222-2222-2222-2222-222222222222"
LIGHT = "11111111-1111-1111-1111-111111111111"
RESOURCES = {"data": [
    {"type": "light", "id": LIGHT, "on": {"on": True}, "metadata": {"name": "Lamp"}},
    {"type": "room", "id": ROOM, "metadata": {"name": "Living"}, "children": [{"rid": LIGHT, "rtype": "light"}]},
]}


class Quiet(hue.Hue):
    """A Hue without stdout, without the user's config and without discovery threads."""
    def __init__(self, config=None):
        self.emitted = []
        hue.load_config = lambda: dict(config or {})
        super().__init__()

    def emit(self):
        self.emitted.append(self.snapshot())

    def discover(self):
        pass


def pair_reply(bridge_id, key="key123"):
    peer = ("f" * 64, bridge_id)

    def request(ip, method, path, body=None, headers=None, **kw):
        if method == "GET":
            return 200, {"bridgeid": bridge_id, "name": "Hue"}, peer
        return 200, [{"success": {"username": key}}], peer
    return request


class Base(unittest.TestCase):
    def setUp(self):
        self.real = (hue.https_request, hue.load_config, hue.save_config, hue.remove_config,
                     hue.CONFIG_DIR, hue.CONFIG_PATH)
        self.dir = tempfile.mkdtemp()
        hue.CONFIG_DIR = os.path.join(self.dir, "hue-lights")
        hue.CONFIG_PATH = os.path.join(hue.CONFIG_DIR, "config.json")

    def tearDown(self):
        (hue.https_request, hue.load_config, hue.save_config, hue.remove_config,
         hue.CONFIG_DIR, hue.CONFIG_PATH) = self.real
        shutil.rmtree(self.dir)


class Subprocesses(unittest.TestCase):
    def setUp(self):
        self.real = (hue.subprocess.Popen, hue.shutil.which if hasattr(hue, "shutil") else None)

    def tearDown(self):
        hue.subprocess.Popen = self.real[0]
        if self.real[1] is not None:
            hue.shutil.which = self.real[1]
        hue._SETPRIV = None

    def capture(self, which):
        calls = []

        def popen(argv, **kw):
            calls.append((list(argv), kw))
            raise OSError("not started in the test")
        hue.subprocess.Popen = popen
        hue.shutil.which = which
        hue._SETPRIV = None
        hue.run_bounded(["avahi-browse", "-rtpk", "_hue._tcp"])
        return calls

    def test_no_python_preexec_fn(self):
        for which in (lambda name: "/usr/bin/" + name, lambda name: None):
            (argv, kw), = self.capture(which)
            self.assertIsNone(kw.get("preexec_fn"))
            self.assertNotIn("shell", kw)

    def test_setpriv_prefix_when_available(self):
        (argv, _), = self.capture(lambda name: "/usr/bin/setpriv" if name == "setpriv" else None)
        self.assertEqual(argv, ["/usr/bin/setpriv", "--pdeathsig", "KILL", "--",
                                "avahi-browse", "-rtpk", "_hue._tcp"])

    def test_plain_argv_without_setpriv(self):
        (argv, _), = self.capture(lambda name: None)
        self.assertEqual(argv, ["avahi-browse", "-rtpk", "_hue._tcp"])

    def test_running_children_are_killed_on_exit(self):
        out = []
        thread = threading.Thread(target=lambda: out.append(hue.run_bounded(["sleep", "30"], timeout=30)))
        start = time.monotonic()
        thread.start()
        for _ in range(100):
            with hue._CHILDREN_LOCK:
                children = list(hue._CHILDREN)
            if children:
                break
            time.sleep(0.02)
        self.assertEqual(len(children), 1)
        hue.kill_children()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 5)
        self.assertIsNotNone(children[0].poll())
        with hue._CHILDREN_LOCK:
            self.assertEqual(hue._CHILDREN, set())

    def test_real_run_still_reads_output(self):
        self.assertEqual(hue.run_bounded(["echo", "hi"]).strip(), "hi")


class UnpairVersusPair(Base):
    def test_pair_saving_during_unpair_deletion_does_not_survive(self):
        """Unpair deletes the file while a pairing reply is being saved: the new key must not stay."""
        hue.save_config(PIN)
        h = Quiet(PIN)
        h.handle({"cmd": "pair", "ip": "192.168.1.60"})
        hue.https_request = pair_reply(OTHER)
        real_remove = hue.remove_config
        pairing = []

        def remove_then_race():
            real_remove()
            thread = threading.Thread(target=h.try_pair)   # its reply arrives right now
            pairing.append(thread)
            thread.start()
            thread.join(0.5)                               # blocks on the lock once Unpair holds it
        hue.remove_config = remove_then_race
        h.handle({"cmd": "unpair"})
        pairing[0].join(5)
        self.assertFalse(os.path.exists(hue.CONFIG_PATH))
        self.assertEqual(h.config, {})
        self.assertEqual(h.phase, "unpaired")

    def test_unpair_during_pairing_request_saves_nothing(self):
        saved = []
        hue.save_config = lambda data: saved.append(data)
        h = Quiet()
        reply = pair_reply(BRIDGE)

        def request(ip, method, path, body=None, headers=None, **kw):
            if method == "POST":
                h.handle({"cmd": "unpair"})
            return reply(ip, method, path, body, headers, **kw)
        hue.https_request = request
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        self.assertEqual(saved, [])
        self.assertEqual(h.config, {})
        self.assertEqual(h.phase, "unpaired")

    def test_replacement_pair_ignores_the_old_attempt(self):
        saved = []
        hue.save_config = lambda data: saved.append(data)
        h = Quiet()
        reply = pair_reply(BRIDGE, "oldkey")

        def request(ip, method, path, body=None, headers=None, **kw):
            if method == "POST":
                h.handle({"cmd": "pair", "ip": "192.168.1.60"})   # a new Pair while the old POST runs
            return reply(ip, method, path, body, headers, **kw)
        hue.https_request = request
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        self.assertEqual(saved, [])
        self.assertEqual(h.pair_ip, "192.168.1.60")
        self.assertEqual(h.phase, "pairing")
        hue.https_request = pair_reply(OTHER, "newkey")
        h.try_pair()
        self.assertEqual([c["app_key"] for c in saved], ["newkey"])
        self.assertEqual(h.config["ip"], "192.168.1.60")


class StaleRefresh(Base):
    def test_refresh_finishing_after_unpair_is_dropped(self):
        h = Quiet(PIN)

        def request(ip, method, path, body=None, headers=None, **kw):
            h.handle({"cmd": "unpair"})
            return 200, RESOURCES, None
        hue.https_request = request
        h.refresh()
        self.assertEqual(h.phase, "unpaired")
        self.assertEqual(h.rooms, [])
        self.assertEqual(h.config, {})

    def test_refresh_error_after_unpair_is_dropped(self):
        h = Quiet(PIN)
        relocated = []
        h.discover_now = lambda: relocated.append(1) or []

        def request(ip, method, path, body=None, headers=None, **kw):
            h.handle({"cmd": "unpair"})
            raise OSError("timed out")
        hue.https_request = request
        h.refresh()
        self.assertEqual(h.phase, "unpaired")
        self.assertEqual(h.error, "")
        self.assertEqual(relocated, [])

    def test_refresh_finishing_after_a_new_pair_started_is_dropped(self):
        h = Quiet(PIN)

        def request(ip, method, path, body=None, headers=None, **kw):
            h.handle({"cmd": "pair", "ip": "192.168.1.60"})
            return 200, RESOURCES, None
        hue.https_request = request
        h.refresh()
        self.assertEqual(h.phase, "pairing")

    def test_refresh_still_applies(self):
        h = Quiet(PIN)
        hue.https_request = lambda *a, **k: (200, RESOURCES, None)
        h.refresh()
        self.assertEqual(h.phase, "ok")
        self.assertEqual([r["name"] for r in h.rooms], ["Living"])

    def test_refresh_uses_one_config_snapshot(self):
        h = Quiet(PIN)
        seen = []

        def request(ip, method, path, body=None, headers=None, pin=None, **kw):
            seen.append((ip, headers, pin))
            return 200, RESOURCES, None
        hue.https_request = request
        h.refresh()
        self.assertEqual(seen, [(PIN["ip"], {"hue-application-key": PIN["app_key"]},
                                 {"fingerprint": PIN["fingerprint"], "bridge_id": BRIDGE})])


class StaleRelocation(Base):
    def unreachable(self):
        def request(*a, **k):
            raise OSError("timed out")
        hue.https_request = request

    def test_relocation_after_unpair_saves_nothing(self):
        saved = []
        hue.save_config = lambda data: saved.append(dict(data))
        h = Quiet(PIN)
        self.unreachable()

        def discover():
            h.handle({"cmd": "unpair"})
            return [{"ip": "192.168.1.77", "bridge_id": BRIDGE, "name": "Hue"}]
        h.discover_now = discover
        h.refresh()
        self.assertEqual(saved, [])
        self.assertEqual(h.config, {})

    def test_relocation_after_replacement_pair_keeps_the_new_bridge(self):
        saved = []
        hue.save_config = lambda data: saved.append(dict(data))
        h = Quiet(PIN)
        self.unreachable()

        def discover():
            hue.https_request = pair_reply(OTHER, "newkey")
            h.handle({"cmd": "pair", "ip": "192.168.1.60"})
            h.try_pair()                                  # a new bridge is paired meanwhile
            return [{"ip": "192.168.1.77", "bridge_id": BRIDGE, "name": "Hue"}]
        h.discover_now = discover
        h.refresh()
        self.assertEqual([(c["ip"], c["app_key"]) for c in saved], [("192.168.1.60", "newkey")])
        self.assertEqual(h.config["ip"], "192.168.1.60")
        self.assertEqual(h.config["bridge_id"], OTHER)

    def test_relocation_still_works(self):
        saved = []
        hue.save_config = lambda data: saved.append(dict(data))
        h = Quiet(PIN)
        self.unreachable()
        h.discover_now = lambda: [{"ip": "192.168.1.77", "bridge_id": BRIDGE, "name": "Hue"}]
        h.refresh()
        self.assertEqual([c["ip"] for c in saved], ["192.168.1.77"])
        self.assertEqual(h.config["ip"], "192.168.1.77")
        self.assertEqual(h.config["app_key"], PIN["app_key"])


class StaleCommand(Base):
    def test_command_failing_after_unpair_shows_no_error(self):
        h = Quiet(PIN)
        h.pending[("light", LIGHT)] = {"on": {"on": False}}

        def request(*a, **k):
            h.handle({"cmd": "unpair"})
            raise OSError("timed out")
        hue.https_request = request
        h.send_pending()
        self.assertEqual(h.error, "")
        self.assertEqual(h.phase, "unpaired")


if __name__ == "__main__":
    unittest.main()
