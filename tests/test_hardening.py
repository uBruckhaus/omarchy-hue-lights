"""Hostile-input tests: bridge replies, config file tricks and markup in names."""
import importlib.util
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hue_hard", os.path.join(HERE, "..", "scripts", "hue-lights.py"))
hue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hue)


def make_cert(directory):
    cert, key = os.path.join(directory, "c.pem"), os.path.join(directory, "k.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=C42996FFFEC9B46D", "-keyout", key, "-out", cert],
                   check=True, capture_output=True)
    return cert, key


class FakeBridge:
    """TLS server on 127.0.0.1 that answers every connection with `behaviour(conn)`."""

    def __init__(self, cert, key, behaviour, tls=True):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.behaviour, self.tls, self.stop = behaviour, tls, threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        self.sock.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            if self.tls:
                conn = self.ctx.wrap_socket(conn, server_side=True)
                conn.recv(65536)
            self.behaviour(conn, self.stop)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.sock.close()


def silent(conn, stop):
    stop.wait(10)


def drip_headers(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\n")
    while not stop.is_set():
        conn.sendall(b"X: y\r\n")
        time.sleep(0.05)


def endless_chunked(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
    block = b"a" * 65536
    while not stop.is_set():
        conn.sendall(b"10000\r\n" + block + b"\r\n")


def huge_length(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 999999999999\r\n\r\n{")
    stop.wait(10)


def normal(conn, stop):
    body = b'{"bridgeid":"C42996FFFEC9B46D"}'
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.cert, cls.key = make_cert(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def request(self, behaviour, tls=True, **kw):
        server = FakeBridge(self.cert, self.key, behaviour, tls)
        try:
            start = time.monotonic()
            try:
                result = hue.https_request("127.0.0.1", "GET", "/api/0/config", timeout=1.0, port=server.port, **kw)
            except Exception as exc:  # noqa: BLE001 - the test inspects what was raised
                result = exc
            return result, time.monotonic() - start
        finally:
            server.close()

    def assertFailsFast(self, behaviour, tls=True):
        result, elapsed = self.request(behaviour, tls)
        self.assertIsInstance(result, (OSError, hue.http.client.HTTPException))
        self.assertLess(elapsed, 2.5)

    def test_silent_server(self):
        self.assertFailsFast(silent, tls=False)

    def test_silent_after_handshake(self):
        self.assertFailsFast(silent)

    def test_drip_headers(self):
        self.assertFailsFast(drip_headers)

    def test_endless_body(self):
        self.assertFailsFast(endless_chunked)

    def test_huge_content_length(self):
        self.assertFailsFast(huge_length)

    def test_normal_reply_and_pin(self):
        (status, data, peer), _ = self.request(normal)
        self.assertEqual((status, data["bridgeid"], peer[1]), (200, "C42996FFFEC9B46D", "C42996FFFEC9B46D"))
        result, _ = self.request(normal, pin={"fingerprint": "0" * 64, "bridge_id": "C42996FFFEC9B46D"})
        self.assertIsInstance(result, hue.PinError)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = (hue.CONFIG_DIR, hue.CONFIG_PATH)
        hue.CONFIG_DIR = os.path.join(self.dir, "hue-lights")
        hue.CONFIG_PATH = os.path.join(hue.CONFIG_DIR, "config.json")
        self.good = {"ip": "192.168.178.106", "bridge_id": "C42996FFFEC9B46D", "fingerprint": "a" * 64,
                     "app_key": "abcDEF-123_x", "name": "Hue", "model": "BSB003"}

    def tearDown(self):
        hue.CONFIG_DIR, hue.CONFIG_PATH = self.old
        shutil.rmtree(self.dir)

    def test_round_trip_private(self):
        hue.save_config(self.good)
        self.assertEqual(hue.load_config(), self.good)
        self.assertEqual(os.stat(hue.CONFIG_PATH).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(hue.CONFIG_DIR).st_mode & 0o777, 0o700)
        self.assertEqual([n for n in os.listdir(hue.CONFIG_DIR) if n.endswith(".tmp")], [])

    def test_permissive_existing_file_ends_private(self):
        os.makedirs(hue.CONFIG_DIR)
        with open(hue.CONFIG_PATH, "w") as fh:
            fh.write("{}")
        os.chmod(hue.CONFIG_PATH, 0o644)
        os.chmod(hue.CONFIG_DIR, 0o755)
        hue.save_config(self.good)
        self.assertEqual(os.stat(hue.CONFIG_PATH).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(hue.CONFIG_DIR).st_mode & 0o777, 0o700)

    def test_predictable_tmp_symlink_not_followed(self):
        os.makedirs(hue.CONFIG_DIR, mode=0o700)
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as fh:
            fh.write("keep")
        os.symlink(victim, hue.CONFIG_PATH + ".tmp")
        hue.save_config(self.good)
        with open(victim) as fh:
            self.assertEqual(fh.read(), "keep")

    def test_symlinked_config_rejected(self):
        os.makedirs(hue.CONFIG_DIR, mode=0o700)
        elsewhere = os.path.join(self.dir, "other.json")
        with open(elsewhere, "w") as fh:
            import json
            json.dump(self.good, fh)
        os.symlink(elsewhere, hue.CONFIG_PATH)
        self.assertEqual(hue.load_config(), {})
        hue.remove_config()
        self.assertTrue(os.path.exists(elsewhere))

    def test_symlinked_config_dir_rejected(self):
        real = os.path.join(self.dir, "real")
        os.makedirs(real)
        os.symlink(real, hue.CONFIG_DIR)
        with self.assertRaises(OSError):
            hue.save_config(self.good)
        self.assertEqual(os.listdir(real), [])

    def test_bad_values_rejected(self):
        for field, value in (("ip", "8.8.8.8"), ("app_key", "a b;$(x)"), ("fingerprint", "zz"), ("bridge_id", "x")):
            data = dict(self.good, **{field: value})
            hue.save_config(data)
            self.assertEqual(hue.load_config(), {}, field)

    def test_oversized_config_rejected(self):
        os.makedirs(hue.CONFIG_DIR, mode=0o700)
        with open(hue.CONFIG_PATH, "w") as fh:
            fh.write(" " * (hue.MAX_CONFIG + 1))
        self.assertEqual(hue.load_config(), {})


class TextTests(unittest.TestCase):
    def test_markup_and_controls_removed(self):
        text = hue.clean('Lamp <img src="http://x/y.png"> &amp;\r\n\x1b[31m' + "x" * 100)
        for bad in "<>&\r\n\x1b":
            self.assertNotIn(bad, text)
        self.assertLessEqual(len(text), 60)

    def test_markup_room_names_from_bridge(self):
        resources = [
            {"id": "11111111-1111-1111-1111-111111111111", "type": "light", "metadata": {"name": "<b>x</b>"},
             "on": {"on": True}},
            {"id": "not-a-uuid", "type": "light", "metadata": {"name": "y"}, "on": {"on": True}},
        ]
        rooms, total, _ = hue.build_model(resources)
        self.assertEqual(total, 1)
        self.assertNotIn("<", rooms[0]["lights"][0]["name"])

    def test_repeated_room_members_stay_bounded(self):
        """A bridge listing one device in many rooms, with a long repeated light-service list."""
        uid = lambda n: "%08x-1111-1111-1111-111111111111" % n
        light, device = uid(1), uid(2)
        services = [{"rtype": "light", "rid": light}] * 20000
        resources = [{"id": light, "type": "light", "metadata": {"name": "L"}, "on": {"on": True}},
                     {"id": device, "type": "device", "services": services}]
        resources += [{"id": uid(100 + n), "type": "room", "metadata": {"name": "R%d" % n},
                       "children": [{"rtype": "device", "rid": device}] * 50 + [{"rtype": "light", "rid": light}] * 50}
                      for n in range(2000)]
        start = time.monotonic()
        rooms, total, _ = hue.build_model(resources)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(total, 1)
        self.assertTrue(all(len(room["lights"]) == 1 for room in rooms))
        self.assertLessEqual(sum(len(room["lights"]) for room in rooms), hue.MAX_MEMBERSHIPS)

    def test_many_lights_per_room_capped(self):
        uid = lambda n: "%08x-1111-1111-1111-111111111111" % n
        lights = [{"id": uid(n), "type": "light", "metadata": {"name": "L"}, "on": {"on": False}} for n in range(1, 400)]
        rooms = [{"id": uid(10000 + n), "type": "zone", "children": [{"rtype": "light", "rid": l["id"]} for l in lights]}
                 for n in range(20)] + [{"id": uid(20000), "type": "room", "children": ["x", None, {"rtype": "device", "rid": 5}]}]
        built, _, _ = hue.build_model(lights + rooms)
        zones = [room for room in built if room["kind"] == "zone"]
        self.assertTrue(all(len(room["lights"]) <= hue.MAX_GROUP_LIGHTS for room in zones))
        self.assertLessEqual(sum(len(room["lights"]) for room in zones), hue.MAX_MEMBERSHIPS)

    def test_bounded_command_output(self):
        out = hue.run_bounded(["yes"], timeout=2, max_bytes=1000)
        self.assertLessEqual(len(out), 1000)
        start = time.monotonic()
        hue.run_bounded(["sleep", "5"], timeout=0.5)
        self.assertLess(time.monotonic() - start, 2)


if __name__ == "__main__":
    unittest.main()
