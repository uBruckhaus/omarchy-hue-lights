"""The Information page's plugin list from the marketplace catalog (plugin_catalog.py)."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "plugin_catalog.py"
spec = importlib.util.spec_from_file_location("plugin_catalog", SCRIPT)
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

CATALOG = {"plugins": [
    {"id": "ubruckhaus.wlan-monitor", "name": "WLAN Monitor", "repo": "https://github.com/uBruckhaus/omarchy-wlan",
     "description": "Who is on your Wi-Fi.", "version": "0.7.1", "verificationStatus": "verified"},
    {"id": "ubruckhaus.new-thing", "name": "New Thing", "repo": "https://github.com/uBruckhaus/new-thing",
     "description": "Does a new thing for you. And more besides.", "verificationStatus": "unverified"},
    {"id": "someone.other", "name": "Other", "repo": "https://github.com/someone/other", "description": "x"},
    {"id": "../bad", "name": "Bad", "repo": "https://github.com/uBruckhaus/bad"},
]}


def answer(status, body=b"", etag='"abc"'):
    return f"HTTP/2 {status}\r\netag: {etag}\r\ncontent-type: text/plain\r\n\r\n".encode() + body


def ok(etag):
    return answer(200, json.dumps(CATALOG).encode())


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "plugin-catalog.json"
        pc._ATTEMPTS.clear()

    def ids(self, record):
        return [p["id"] for p in record["plugins"]]

    def test_only_the_authors_plugins(self):
        self.assertEqual([p["id"] for p in pc.parse(CATALOG)], ["ubruckhaus.wlan-monitor", "ubruckhaus.new-thing"])

    def test_read_then_not_changed_then_offline(self):
        asked = []

        def fetch(etag):
            asked.append(etag)
            return ok(etag)
        self.assertEqual(len(self.ids(pc.refresh(self.cache, fetch, now=1000))), 2)
        self.assertEqual(len(self.ids(pc.refresh(self.cache, fetch, now=1500))), 2)     # within the hour
        self.assertEqual(asked, [""])
        self.assertEqual(len(self.ids(pc.refresh(self.cache, lambda e: asked.append(e) or answer(304), now=9000))), 2)
        self.assertEqual(asked[-1], '"abc"')

        def offline(etag):
            raise OSError("no network")
        self.assertEqual(len(self.ids(pc.refresh(self.cache, offline, now=99000))), 2)

    def test_output_is_what_the_page_reads(self):
        out = pc.output(pc.refresh(self.cache, ok, now=1000))
        self.assertEqual(out["plugins"][1], {"id": "ubruckhaus.new-thing", "name": "New Thing",
                                             "summary": "Does a new thing for you."})
        self.assertEqual(out["checked"], 1000)

    def test_failures_are_tried_at_most_once_an_hour(self):
        asked = []

        def offline(etag):
            asked.append(etag)
            raise OSError("no network")
        pc.refresh(self.cache, offline, now=1000)
        pc.refresh(self.cache, offline, now=1500)
        self.assertEqual(len(asked), 1)
        blocked = Path(self.tmp.name) / "file-not-dir" / "plugin-catalog.json"
        blocked.parent.write_text("")                       # the cache folder cannot be made
        pc.refresh(blocked, offline, now=1000)
        pc.refresh(blocked, offline, now=1001)
        self.assertEqual(len(asked), 2)

    def test_error_or_malformed_answer_keeps_the_list(self):
        pc.refresh(self.cache, ok, now=1000)
        for n, body in enumerate((b"busy", b"{}", b"[]", b'{"plugins": "x"}')):
            status = 503 if body == b"busy" else 200
            now = 1000 + pc.MAX_AGE * (n + 2)
            self.assertEqual(len(self.ids(pc.refresh(self.cache, lambda e: answer(status, body), now=now))), 2)
        self.assertEqual(self.ids(pc.refresh(self.cache, lambda e: answer(200, b'{"plugins": []}'), now=10 ** 6)), [])

    def test_fifo_symlink_and_oversized_cache_are_not_read(self):
        os.mkfifo(self.cache)
        self.assertEqual(pc.load(self.cache), {})
        self.cache.unlink()
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps({"plugins": [{"id": "ubruckhaus.x", "name": "X"}]}))
        self.cache.symlink_to(target)
        self.assertEqual(pc.load(self.cache), {})
        self.cache.unlink()
        self.cache.write_text(json.dumps({"plugins": [], "pad": "x" * (pc.MAX_CACHE_BYTES + 1)}))
        self.assertEqual(pc.load(self.cache), {})

    def test_bad_times_huge_numbers_and_nesting(self):
        self.cache.write_text('{"plugins": [], "checked": 1' + "0" * 400 + ', "tried": "x"}')
        self.assertEqual((pc.load(self.cache)["checked"], pc.load(self.cache)["tried"]), (0.0, 0.0))
        self.cache.write_text('{"plugins": ' + "[" * 100000 + "]" * 100000 + "}")
        self.assertEqual(pc.load(self.cache), {})

    def test_text_is_limited_and_plain(self):
        self.cache.write_text(json.dumps({"checked": 1, "plugins": [
            {"id": "ubruckhaus.long", "name": "N" * 500, "description": "d" * 5000, "verified": "yes"},
            {"id": "ubruckhaus.blank", "name": "  "}]}).replace("NNN", "\\ud800NN", 1))
        plugins = pc.load(self.cache)["plugins"]
        self.assertEqual(len(plugins), 1)
        self.assertEqual((len(plugins[0]["name"]), len(plugins[0]["description"]), plugins[0]["verified"]),
                         (60, 600, False))
        self.assertIn("?", plugins[0]["name"])
        json.dumps(pc.output({"plugins": plugins}), ensure_ascii=False).encode("utf-8")

    def test_odd_etag_is_not_sent(self):
        self.assertNotIn("--header", pc.curl_command('"x"\r\nEvil: 1'))
        self.assertIn('If-None-Match: "abc"', pc.curl_command('"abc"'))

    def test_cache_path_takes_only_a_plugin_id(self):
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/shared"}):
            self.assertEqual(pc.cache_path("ubruckhaus.hue-lights"),
                             Path.home() / ".cache/ubruckhaus.hue-lights/plugin-catalog.json")
        for bad in ("../x", "a/b", "", "a..b", "A", "ubruckhaus.x\n"):
            with self.assertRaises(ValueError):
                pc.cache_path(bad)

    def test_cache_folder_is_made_private(self):
        cache = Path(self.tmp.name) / "new" / "plugin-catalog.json"
        pc.refresh(cache, ok, now=1000)
        self.assertEqual(cache.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(pc.load(cache)["plugins"]), 2)

    def test_open_or_linked_cache_folder_is_refused(self):
        open_dir = Path(self.tmp.name) / "open"
        open_dir.mkdir(mode=0o755)
        open_dir.chmod(0o755)
        cache = open_dir / "plugin-catalog.json"
        cache.write_text(json.dumps({"checked": 1, "plugins": [{"id": "ubruckhaus.x", "name": "X"}]}))
        self.assertEqual(pc.load(cache), {})
        with self.assertRaises(OSError):
            pc._save({"plugins": []}, cache)
        real = Path(self.tmp.name) / "real"
        real.mkdir(mode=0o700)
        linked = Path(self.tmp.name) / "linked"
        linked.symlink_to(real, target_is_directory=True)
        pc.refresh(linked / "plugin-catalog.json", ok, now=1000)
        self.assertEqual(list(real.iterdir()), [])                 # nothing written through the link

    def test_etag_must_match_exactly(self):
        self.assertNotIn("--header", pc.curl_command('"abc"\n'))

    def test_bounded_reader(self):
        self.assertEqual(pc.read_bounded(["printf", "abc"], 10, 5), b"abc")
        with self.assertRaises(OSError):
            pc.read_bounded(["head", "-c", "100", "/dev/zero"], 10, 5)
        with self.assertRaises(OSError):
            pc.read_bounded(["sleep", "5"], 10, 0.3)


if __name__ == "__main__":
    unittest.main()
