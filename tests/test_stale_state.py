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

    def discover_now(self):
        return []


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
        h.relocate = lambda *a: relocated.append(1)

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


class StaleDispatch(Base):
    def test_new_pair_stops_sending_queued_commands(self):
        h = Quiet(PIN)
        other = "33333333-3333-3333-3333-333333333333"
        h.pending[("light", LIGHT)] = {"on": {"on": False}}
        h.pending[("light", other)] = {"on": {"on": False}}
        hue.LIGHT_INTERVAL, real = 0.0, hue.LIGHT_INTERVAL
        sent = []

        def request(ip, method, path, body=None, headers=None, **kw):
            sent.append(path)
            if len(sent) == 1:
                h.handle({"cmd": "pair", "ip": "192.168.1.60"})   # a new Pair while the batch runs
            return 200, {"data": []}, None
        hue.https_request = request
        try:
            h.send_pending()
        finally:
            hue.LIGHT_INTERVAL = real
        self.assertEqual(len(sent), 1)
        self.assertEqual(h.pending, {})


class NonFinite(unittest.TestCase):
    real_request = hue.https_request

    def test_infinite_brightness_never_reaches_the_snapshot(self):
        import json
        data = [
            {"type": "light", "id": LIGHT, "on": {"on": True}, "dimming": {"brightness": float("inf")},
             "color": {"xy": {"x": float("nan"), "y": 0.3}}, "color_temperature": {"mirek": float("inf")}},
            {"type": "grouped_light", "id": "44444444-4444-4444-4444-444444444444",
             "dimming": {"brightness": float("-inf")}},
            {"type": "room", "id": ROOM, "metadata": {"name": "Living"}, "children": [{"rid": LIGHT, "rtype": "light"}],
             "services": [{"rtype": "grouped_light", "rid": "44444444-4444-4444-4444-444444444444"}]},
        ]
        reply = json.loads(json.dumps(data))           # the bridge sent 1e309 / Infinity / NaN
        with self.assertRaises(ValueError):
            hue.build_model(reply)
        reply[0]["dimming"]["brightness"] = 250          # finite: clamped, the colours fall back
        with self.assertRaises(ValueError):
            hue.build_model(reply)                       # the grouped light is still -Infinity
        reply[1]["dimming"]["brightness"] = 40
        rooms, _, _ = hue.build_model(reply)
        self.assertNotIn("Infinity", json.dumps(rooms, allow_nan=False))
        self.assertEqual(rooms[0]["lights"][0]["bri"], 100)
        self.assertEqual(rooms[0]["bri"], 40)
        h = Quiet(PIN)
        hue.https_request = lambda *a, **k: (200, {"data": json.loads(json.dumps(data))}, None)
        try:
            h.refresh()
        finally:
            hue.https_request = self.real_request
        self.assertEqual((h.phase, h.error_code), ("error", "errReply"))

    def test_brightness_strings_and_nan_from_the_panel(self):
        self.assertEqual(hue.finite("inf", 50.0, 0, 100), 50.0)
        self.assertEqual(hue.finite(float("nan"), 50.0, 0, 100), 50.0)
        self.assertEqual(hue.finite(250, 50.0, 0, 100), 100)
        h = Quiet(PIN)
        h.handle({"cmd": "light", "id": LIGHT, "bri": float("nan")})
        self.assertEqual(h.pending, {})

    def test_emit_never_writes_infinity(self):
        import io
        h = hue.Hue.__new__(hue.Hue)
        hue.load_config = lambda: dict(PIN)
        hue.Hue.__init__(h)
        h.rooms = [{"bri": float("inf")}]
        out, real = io.StringIO(), hue.sys.stdout
        hue.sys.stdout = out
        try:
            h.emit()
        finally:
            hue.sys.stdout = real
        self.assertNotIn("Infinity", out.getvalue())
        self.assertEqual(json_loads_strict(out.getvalue())["phase"], "error")


class CommandInput(unittest.TestCase):
    def read(self, data):
        import io
        return list(hue.read_commands(io.BytesIO(data)))

    def test_oversized_line_is_discarded_through_its_newline(self):
        big = b'{"cmd":"refresh","x":"' + b"a" * (hue.MAX_COMMAND * 3) + b'"}\n'
        self.assertEqual(self.read(big + b'{"cmd":"open"}\n'), [{"cmd": "open"}])

    def test_nested_command_is_no_crash(self):
        nested = b'{"cmd":"x","a":' + b"[" * 5000 + b"]" * 5000 + b"}\n"
        self.assertEqual(self.read(nested + b'{"cmd":"open"}\n{"cmd":"closed"}'),
                         [{"cmd": "open"}, {"cmd": "closed"}])

    def test_failing_command_does_not_end_the_loop(self):
        import io
        h = Quiet(PIN)
        seen = []

        def handle(cmd):
            seen.append(cmd["cmd"])
            if cmd["cmd"] == "boom":
                raise RuntimeError("boom")
        h.handle = handle
        h.worker = lambda: None
        real = hue.sys.stdin
        hue.sys.stdin = io.TextIOWrapper(io.BytesIO(b'{"cmd":"boom"}\n{"cmd":"open"}\n'))
        try:
            h.run()
        finally:
            hue.sys.stdin = real
        self.assertEqual(seen, ["boom", "open"])


class StaleWorkerFallback(Base):
    def test_unexpected_exception_after_unpair_shows_no_error(self):
        h = Quiet(PIN)
        rounds = []

        def work_once():
            rounds.append(1)
            if len(rounds) == 1:
                h.handle({"cmd": "unpair"})
                raise RuntimeError("late failure of the old connection")
            raise SystemExit
        h.work_once = work_once
        hue.time.sleep, real_sleep = (lambda s: None), hue.time.sleep
        try:
            with self.assertRaises(SystemExit):
                h.worker()
        finally:
            hue.time.sleep = real_sleep
        self.assertEqual(h.error, "")
        self.assertEqual(h.phase, "unpaired")


class BoundedWork(Base):
    def test_commands_only_for_known_resources(self):
        h = Quiet(PIN)
        hue.https_request = lambda *a, **k: (200, RESOURCES, None)
        h.refresh()
        unknown = "99999999-9999-9999-9999-999999999999"
        h.handle({"cmd": "light", "id": unknown, "on": False})
        h.handle({"cmd": "scene", "id": unknown})
        h.handle({"cmd": "group", "id": unknown, "on": False})
        self.assertEqual(h.pending, {})
        h.handle({"cmd": "light", "id": LIGHT, "on": False})
        self.assertEqual(list(h.pending), [("light", LIGHT)])

    def test_pending_has_a_global_cap(self):
        h = Quiet(PIN)
        ids = ["%08x-0000-0000-0000-000000000000" % i for i in range(hue.MAX_PENDING + 50)]
        hue.https_request = lambda *a, **k: (200, {"data": [
            {"type": "light", "id": lid, "on": {"on": True}} for lid in ids]}, None)
        real = hue.MAX_GROUP_LIGHTS, hue.MAX_MEMBERSHIPS
        hue.MAX_GROUP_LIGHTS = hue.MAX_MEMBERSHIPS = 10 ** 6
        try:
            h.refresh()
        finally:
            hue.MAX_GROUP_LIGHTS, hue.MAX_MEMBERSHIPS = real
        for lid in ids:
            h.handle({"cmd": "light", "id": lid, "on": False})
        self.assertEqual(len(h.pending), hue.MAX_PENDING)

    def test_one_discovery_at_a_time(self):
        h = Quiet()
        started, release = [], threading.Event()

        def discover_now():
            started.append(1)
            release.wait(5)
            return []
        h.discover_now = discover_now
        for _ in range(20):
            h.handle({"cmd": "discover"})
        h.handle({"cmd": "unpair"})
        time.sleep(0.2)
        self.assertEqual(len(started), 1)
        self.assertTrue(h.discovering)
        release.set()
        for _ in range(100):
            if not h.discovering:
                break
            time.sleep(0.02)
        self.assertFalse(h.discovering)
        h.handle({"cmd": "discover"})
        time.sleep(0.2)
        self.assertEqual(len(started), 2)

    def test_refresh_captured_after_pair_started_changes_nothing(self):
        h = Quiet(PIN)
        h.handle({"cmd": "pair", "ip": "192.168.1.60"})   # Pair came first, refresh captures after
        calls = []
        hue.https_request = lambda *a, **k: calls.append(1) or (200, RESOURCES, None)
        h.refresh()
        self.assertEqual(calls, [])
        self.assertEqual(h.phase, "pairing")
        h.handle({"cmd": "cancel_pair"})
        h.refresh()
        self.assertEqual(h.phase, "ok")


class NoKeyRequestAfterCancel(Base):
    def run_with(self, during_get):
        h = Quiet()
        posts = []

        def request(ip, method, path, body=None, headers=None, **kw):
            peer = ("f" * 64, BRIDGE)
            if method == "GET":
                during_get(h)                      # Cancel / Unpair / new Pair while the GET runs
                return 200, {"bridgeid": BRIDGE, "name": "Hue"}, peer
            posts.append(ip)
            return 200, [{"success": {"username": "fakekey"}}], peer
        hue.https_request = request
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        return h, posts

    def test_cancel_during_first_request_sends_no_key_request(self):
        for during in (lambda h: h.handle({"cmd": "cancel_pair"}),
                       lambda h: h.handle({"cmd": "unpair"}),
                       lambda h: h.handle({"cmd": "pair", "ip": "192.168.1.60"})):
            h, posts = self.run_with(during)
            self.assertEqual(posts, [])
            self.assertEqual(h.config, {})


FAKE_KEY = "FakeAppKey0123456789abcdefABCDEF-_xyz0123"
KEYED = dict(PIN, app_key=FAKE_KEY)


class BridgeTextsStayOut(Base):
    def assertClean(self, h, *words):
        text = repr(h.snapshot())
        for word in words:
            self.assertNotIn(word, text)

    def test_bridge_error_description_never_reaches_the_snapshot(self):
        h = Quiet(KEYED)
        echo = "invalid key " + FAKE_KEY + " <b>busy</b>"
        hue.https_request = lambda *a, **k: (200, {"errors": [{"description": echo}], "data": []}, None)
        h.refresh()
        self.assertEqual((h.phase, h.error_code), ("error", "errReply"))
        self.assertClean(h, FAKE_KEY, "busy", "invalid key")
        hue.https_request = lambda *a, **k: (200, RESOURCES, None)
        h.refresh()
        hue.https_request = lambda *a, **k: (200, {"errors": [{"description": echo}]}, None)
        h.handle({"cmd": "light", "id": LIGHT, "on": False})
        h.send_pending()
        self.assertEqual(h.error_code, "errCommand")
        self.assertClean(h, FAKE_KEY, "busy", "invalid key")

    def test_pairing_error_description_never_reaches_the_snapshot(self):
        h = Quiet()
        peer = ("f" * 64, BRIDGE)
        hue.https_request = lambda ip, method, *a, **k: (
            (200, {"bridgeid": BRIDGE}, peer) if method == "GET"
            else (200, [{"error": {"type": 7, "description": "<i>secret detail</i>"}}], peer))
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        self.assertEqual(h.error_code, "errPairFailed")
        self.assertClean(h, "secret detail")

    def test_key_in_exception_text_is_redacted(self):
        h = Quiet(KEYED)
        relocated = []
        h.relocate = lambda *a: relocated.append(1)

        def request(*a, **k):
            raise hue.http.client.HTTPException("got line hue-application-key: " + FAKE_KEY)
        hue.https_request = request
        h.refresh()
        self.assertEqual(h.error_code, "errUnreachable")
        self.assertClean(h, FAKE_KEY, FAKE_KEY[:12])
        h.set_error("errInternal", "Something went wrong (%s)", "x " + FAKE_KEY)
        self.assertClean(h, FAKE_KEY)

    def test_client_key_from_pairing_is_redacted_too(self):
        saved = []
        hue.save_config = lambda data: saved.append(data)
        h = Quiet()
        peer = ("f" * 64, BRIDGE)
        client = "0123456789ABCDEF0123456789ABCDEF"
        hue.https_request = lambda ip, method, *a, **k: (
            (200, {"bridgeid": BRIDGE}, peer) if method == "GET"
            else (200, [{"success": {"username": "fakekey99", "clientkey": client}}], peer))
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        h.set_error("errInternal", "Something went wrong (%s)", client + " fakekey99")
        self.assertClean(h, client, "fakekey99")


class KeptKeys(Base):
    """Only well-formed keys from a pairing that is still current are kept for redaction,
    and never more than MAX_SECRETS of them."""
    def pairing(self, username, clientkey, during_post=None):
        h = Quiet()
        hue.save_config = lambda data: None
        peer = ("f" * 64, BRIDGE)

        def request(ip, method, *a, **k):
            if method == "GET":
                return 200, {"bridgeid": BRIDGE}, peer
            if during_post:
                during_post(h)
            return 200, [{"success": {"username": username, "clientkey": clientkey}}], peer
        hue.https_request = request
        h.handle({"cmd": "pair", "ip": "192.168.1.50"})
        h.try_pair()
        return h

    def test_malformed_keys_are_not_kept(self):
        h = self.pairing("x" * 5000, "not hex " * 1000)
        self.assertEqual(h.secrets, set())

    def test_reply_for_a_cancelled_pairing_keeps_no_keys(self):
        h = self.pairing("goodkey123", "ABCDEF0123", during_post=lambda h: h.handle({"cmd": "cancel_pair"}))
        self.assertEqual(h.secrets, set())
        self.assertEqual(h.config, {})

    def test_kept_keys_stay_bounded(self):
        h = Quiet()
        hue.save_config = lambda data: None
        peer = ("f" * 64, BRIDGE)
        for n in range(50):
            hue.https_request = lambda ip, method, *a, n=n, **k: (
                (200, {"bridgeid": BRIDGE}, peer) if method == "GET"
                else (200, [{"success": {"username": "key%04d" % n, "clientkey": "%032X" % n}}], peer))
            h.handle({"cmd": "pair", "ip": "192.168.1.50"})
            h.try_pair()
        self.assertLessEqual(len(h.secrets), hue.MAX_SECRETS)
        self.assertIn(h.config["app_key"], h.secrets)
        h.set_error("errInternal", "Something went wrong (%s)", "x " + h.config["app_key"])
        self.assertNotIn(h.config["app_key"], h.error)


class UnpairDeletion(Base):
    def test_unpair_without_a_saved_pairing_is_no_error(self):
        h = Quiet(PIN)
        h.handle({"cmd": "unpair"})
        self.assertEqual((h.phase, h.error), ("unpaired", ""))

    def test_unpair_reports_a_pairing_file_it_cannot_delete(self):
        if os.geteuid() == 0:
            self.skipTest("root can delete in a read-only folder")
        hue.save_config(PIN)
        os.chmod(hue.CONFIG_DIR, 0o500)
        try:
            h = Quiet(PIN)
            h.handle({"cmd": "unpair"})
            self.assertTrue(os.path.exists(hue.CONFIG_PATH))
        finally:
            os.chmod(hue.CONFIG_DIR, 0o700)   # before tearDown removes the folder
        self.assertEqual((h.phase, h.error_code), ("unpaired", "errInternal"))
        self.assertIn("could not be deleted", h.error)
        self.assertEqual(h.config, {})


def json_loads_strict(text):
    import json

    def refuse(name):
        raise ValueError(name)
    return json.loads(text, parse_constant=refuse)


if __name__ == "__main__":
    unittest.main()
