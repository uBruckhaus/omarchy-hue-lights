"""F12 screenshots of a panel: shot_save.py's folder, file and note rules (as AI Playground's)."""

import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
# In a plugin: tests/test_shot_save.py next to ../shot_save.py; in the i18n tool: qml/ holds both.
SCRIPT = next(p for p in (HERE.parent / "shot_save.py", HERE / "shot_save.py") if p.is_file())
spec = importlib.util.spec_from_file_location("shot_save", SCRIPT)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

PNG = ss.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
FOLDER = "LLM Monitor"
PLUGIN = "ubruckhaus.llm-usage"


class ShotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.runtime = base / "run"
        self.home.mkdir()
        self.runtime.mkdir(mode=0o700)
        env = {"HOME": str(self.home), "XDG_RUNTIME_DIR": str(self.runtime),
               "XDG_CONFIG_HOME": str(self.home / ".config")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.pictures = self.home / "Pictures" / FOLDER

    def run_cli(self, *argv, stdin=b""):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(ss.sys, "stdin", mock.Mock(buffer=io.BytesIO(stdin))):
            code = ss.main(list(argv))
        line = out.getvalue()
        self.assertEqual(line.count("\n"), 1, line)
        return code, json.loads(line)

    def panel_png(self, data=PNG, name="a1b2c3.png"):
        code, result = self.run_cli("prepare", PLUGIN)
        self.assertEqual(code, 0, result)
        path = Path(result["dir"]) / name
        path.write_bytes(data)
        return path


class PrepareTest(ShotCase):
    def test_makes_private_runtime_folder(self):
        code, result = self.run_cli("prepare", PLUGIN)
        self.assertEqual(code, 0)
        self.assertEqual(result["dir"], str(self.runtime / (PLUGIN + "-shot")))
        self.assertEqual(stat.S_IMODE(os.stat(result["dir"]).st_mode), 0o700)

    def test_rejects_bad_plugin_id(self):
        for bad in ("../x", "", "A B", "x/y"):
            code, result = self.run_cli("prepare", bad)
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])

    def test_refuses_linked_or_open_runtime_folder(self):
        target = self.runtime / "elsewhere"
        target.mkdir(mode=0o700)
        (self.runtime / (PLUGIN + "-shot")).symlink_to(target)
        self.assertEqual(self.run_cli("prepare", PLUGIN)[0], 1)
        (self.runtime / (PLUGIN + "-shot")).unlink()
        (self.runtime / (PLUGIN + "-shot")).mkdir(mode=0o755)
        os.chmod(self.runtime / (PLUGIN + "-shot"), 0o755)
        self.assertEqual(self.run_cli("prepare", PLUGIN)[0], 1)

    def test_refuses_unsafe_runtime_root(self):
        real = Path(self.tmp.name) / "real-run"
        real.mkdir(mode=0o700)
        link = Path(self.tmp.name) / "link-run"
        link.symlink_to(real)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(link)}):
            code, result = self.run_cli("prepare", PLUGIN)
        self.assertEqual(code, 1)
        self.assertIn("XDG_RUNTIME_DIR", result["error"])
        self.assertEqual(list(real.iterdir()), [])
        for mode in (0o777, 0o770, 0o1777, 0o702):
            os.chmod(self.runtime, mode)
            code, result = self.run_cli("prepare", PLUGIN)
            self.assertEqual(code, 1, oct(mode))
            self.assertIn("XDG_RUNTIME_DIR", result["error"])
            self.assertFalse((self.runtime / (PLUGIN + "-shot")).exists())
        os.chmod(self.runtime, 0o700)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime / "missing")}):
            self.assertEqual(self.run_cli("prepare", PLUGIN)[0], 1)

    def test_refuses_runtime_root_under_open_or_linked_folder(self):
        base = Path(self.tmp.name)
        open_dir = base / "open"
        open_dir.mkdir()
        os.chmod(open_dir, 0o777)                                # not sticky, not root's
        run = open_dir / "run"
        run.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(run)}):
            code, result = self.run_cli("prepare", PLUGIN)
        self.assertEqual(code, 1)
        self.assertIn("open to other accounts", result["error"])
        self.assertFalse((run / (PLUGIN + "-shot")).exists())
        os.chmod(open_dir, 0o1777)                               # sticky, but ours: still open
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(run)}):
            self.assertEqual(self.run_cli("prepare", PLUGIN)[0], 1)
        os.chmod(open_dir, 0o700)
        real = base / "real"
        real.mkdir(mode=0o700)
        (real / "run").mkdir(mode=0o700)
        (base / "via").symlink_to(real)                          # a link above the runtime folder
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base / "via" / "run")}):
            code, result = self.run_cli("prepare", PLUGIN)
        self.assertEqual(code, 1)
        self.assertFalse((real / "run" / (PLUGIN + "-shot")).exists())
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(run)}):
            self.assertEqual(self.run_cli("prepare", PLUGIN)[0], 0)  # closed again

    def test_root_owned_sticky_and_closed_folders_count_as_closed(self):
        info = lambda mode, uid: mock.Mock(st_mode=stat.S_IFDIR | mode, st_uid=uid)
        self.assertTrue(ss.closed_folder("/tmp", info(0o1777, 0)))
        self.assertTrue(ss.closed_folder("/run", info(0o755, 0)))
        self.assertTrue(ss.closed_folder("/home/u", info(0o755, os.getuid())))
        self.assertFalse(ss.closed_folder("/x", info(0o777, 0)))
        self.assertFalse(ss.closed_folder("/x", info(0o1777, os.getuid() or 4242)))
        self.assertFalse(ss.closed_folder("/x", info(0o755, os.getuid() + 1)))
        self.assertFalse(ss.closed_folder("/x", mock.Mock(st_mode=stat.S_IFLNK | 0o777, st_uid=0)))

    def test_save_refused_when_runtime_root_turned_unsafe(self):
        tmp = self.panel_png()
        os.chmod(self.runtime, 0o777)
        code, result = self.run_cli("save", str(tmp), FOLDER)
        os.chmod(self.runtime, 0o700)
        self.assertEqual(code, 1)
        self.assertFalse(self.pictures.exists())

    def test_cleanup_removes_stale_and_makes_nothing(self):
        code, result = self.run_cli("cleanup", PLUGIN)
        self.assertEqual((code, result["dir"]), (0, None))
        self.assertFalse((self.runtime / (PLUGIN + "-shot")).exists())
        old = self.panel_png(name="old.png")
        os.utime(old, (1, 1))
        fresh = old.parent / "fresh.png"
        fresh.write_bytes(PNG)
        code, result = self.run_cli("cleanup", PLUGIN)
        self.assertEqual(code, 0)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        os.chmod(old.parent, 0o755)
        self.assertEqual(self.run_cli("cleanup", PLUGIN)[0], 1)
        self.assertEqual(self.run_cli("cleanup", "../x")[0], 1)

    def test_removes_stale_pictures_only(self):
        old = self.panel_png(name="old.png")
        os.utime(old, (1, 1))
        fresh = old.parent / "fresh.png"
        fresh.write_bytes(PNG)
        self.run_cli("prepare", PLUGIN)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


class SaveTest(ShotCase):
    def test_saves_private_copy_and_removes_tmp(self):
        tmp = self.panel_png()
        code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 0, result)
        path = Path(result["path"])
        self.assertEqual(path.parent, self.pictures)
        self.assertRegex(path.name, r"^llm-monitor-\d{8}-\d{6}\.png$")
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.pictures.stat().st_mode), 0o700)
        self.assertTrue(result["shown"].startswith("~/Pictures/LLM Monitor/"))
        self.assertFalse(tmp.exists())

    def test_never_overwrites(self):
        first = self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"]
        with mock.patch.object(ss.time, "strftime", return_value=Path(first).stem.split("monitor-")[1]):
            second = self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"]
            Path(second[:-4] + ".txt").unlink(missing_ok=True)
            third = self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"]
        self.assertTrue(second.endswith("-2.png"), second)
        self.assertTrue(third.endswith("-3.png"), third)
        self.assertEqual(Path(first).read_bytes(), PNG)

    def test_name_taken_by_a_note_is_skipped(self):
        self.pictures.mkdir(parents=True, mode=0o700)
        with mock.patch.object(ss.time, "strftime", return_value="20261110-120000"):
            (self.pictures / "llm-monitor-20261110-120000.txt").write_text("x")
            path = self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"]
        self.assertTrue(path.endswith("llm-monitor-20261110-120000-2.png"))

    def test_xdg_pictures_dir(self):
        config = self.home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
        path = Path(self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"])
        self.assertEqual(path.parent, self.home / "Bilder" / FOLDER)

    def test_user_dirs_must_be_a_small_regular_file(self):
        config = self.home / ".config"
        config.mkdir()
        real = self.home / "dirs"
        real.write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
        (config / "user-dirs.dirs").symlink_to(real)            # a link: not followed
        self.assertEqual(ss.pictures_dir(), str(self.home / "Pictures"))
        (config / "user-dirs.dirs").unlink()
        os.mkfifo(config / "user-dirs.dirs")                     # a FIFO must not block
        self.assertEqual(ss.pictures_dir(), str(self.home / "Pictures"))
        (config / "user-dirs.dirs").unlink()
        (config / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n' + "#" * ss.USER_DIRS_LIMIT)
        self.assertEqual(ss.pictures_dir(), str(self.home / "Pictures"))
        (config / "user-dirs.dirs").write_text('# x\nXDG_PICTURES_DIR="$HOME/Bilder"\n')
        self.assertEqual(ss.pictures_dir(), str(self.home / "Bilder"))

    def test_refuses_symlinked_folder(self):
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        self.pictures.parent.mkdir(parents=True)
        self.pictures.symlink_to(elsewhere)
        tmp = self.panel_png()
        code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 1)
        self.assertIn("private folder", result["error"])
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertFalse(tmp.exists())

    def test_refuses_folder_writable_by_others(self):
        for mode in (0o777, 0o770, 0o722):
            self.pictures.mkdir(parents=True, exist_ok=True)
            os.chmod(self.pictures, mode)
            code, result = self.run_cli("save", str(self.panel_png()), FOLDER)
            self.assertEqual(code, 1, oct(mode))
            self.assertEqual(list(self.pictures.iterdir()), [])

    def test_tmp_outside_runtime_folder_is_refused_and_left_alone(self):
        outside = self.home / "x.png"
        outside.write_bytes(PNG)
        other = self.runtime / "x.png"
        other.write_bytes(PNG)
        nested = self.runtime / "not-a-shot-folder"
        nested.mkdir(mode=0o700)
        (nested / "x.png").write_bytes(PNG)
        for path in (outside, other, nested / "x.png", self.runtime / (PLUGIN + "-shot") / ".." / "x.png"):
            code, result = self.run_cli("save", str(path), FOLDER)
            self.assertEqual(code, 1, path)
            self.assertIn("private folder", result["error"])
        self.assertTrue(outside.exists() and other.exists() and (nested / "x.png").exists())
        self.assertFalse(self.pictures.exists())

    def test_tmp_symlink_is_refused(self):
        real = self.home / "real.png"
        real.write_bytes(PNG)
        tmp = self.panel_png()
        tmp.unlink()
        tmp.symlink_to(real)
        code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 1)
        self.assertTrue(real.exists())
        self.assertFalse(os.path.lexists(tmp))

    def test_oversized_is_refused(self):
        tmp = self.panel_png()
        with open(tmp, "r+b") as handle:
            handle.truncate(ss.MAX_PICTURE + 1)
        code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 1)
        self.assertIn("too large", result["error"])
        self.assertFalse(tmp.exists())
        self.assertEqual(list(self.pictures.iterdir()) if self.pictures.exists() else [], [])

    def test_not_a_png_is_refused(self):
        tmp = self.panel_png(b"GIF89a" + b"\x00" * 32)
        code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 1)
        self.assertIn("not a PNG", result["error"])
        self.assertFalse(tmp.exists())

    def test_bad_folder_names(self):
        for bad in ("", ".", "..", "a/b", ".hidden", "x\ny", "x" * 65):
            code, result = self.run_cli("save", str(self.panel_png()), bad)
            self.assertEqual(code, 1, repr(bad))

    def test_failed_write_leaves_nothing(self):
        tmp = self.panel_png()
        with mock.patch.object(ss.os, "fsync", side_effect=OSError(28, "No space left on device")):
            code, result = self.run_cli("save", str(tmp), FOLDER)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "No space left on device")
        self.assertEqual(list(self.pictures.iterdir()), [])
        self.assertFalse(tmp.exists())

    def test_short_writes_are_completed(self):
        real_write = os.write
        with mock.patch.object(ss.os, "write", side_effect=lambda fd, data: real_write(fd, bytes(data[:7]))):
            code, result = self.run_cli("save", str(self.panel_png()), FOLDER)
        self.assertEqual(code, 0, result)
        self.assertEqual(Path(result["path"]).read_bytes(), PNG)


    def test_write_returning_zero_fails_instead_of_spinning(self):
        with mock.patch.object(ss.os, "write", return_value=0):
            code, result = self.run_cli("save", str(self.panel_png()), FOLDER)
        self.assertEqual(code, 1, result)
        self.assertEqual(list(self.pictures.iterdir()), [])

class NoteTest(ShotCase):
    def saved(self):
        return Path(self.run_cli("save", str(self.panel_png()), FOLDER)[1]["path"])

    def test_note_next_to_picture(self):
        picture = self.saved()
        code, result = self.run_cli("note", str(picture), FOLDER, stdin="  The meter is cut off.  \n".encode())
        self.assertEqual(code, 0, result)
        note = picture.with_suffix(".txt")
        self.assertEqual(result["path"], str(note))
        self.assertEqual(note.read_text(), "The meter is cut off.\n")
        self.assertEqual(stat.S_IMODE(note.stat().st_mode), 0o600)

    def test_no_text_no_file(self):
        picture = self.saved()
        code, result = self.run_cli("note", str(picture), FOLDER, stdin=b" \n\t ")
        self.assertEqual((code, result["path"]), (0, None))
        self.assertFalse(picture.with_suffix(".txt").exists())

    def test_note_is_cut_and_decoded_with_replacement(self):
        picture = self.saved()
        self.run_cli("note", str(picture), FOLDER, stdin=b"\xff" + "ä".encode() * 5000)
        text = picture.with_suffix(".txt").read_text()
        self.assertEqual(len(text), ss.NOTE_LIMIT + 1)
        self.assertTrue(text.startswith("�ä"))

    def test_stdin_read_is_bounded(self):
        stream = io.BytesIO(b"x" * (ss.NOTE_READ_LIMIT * 4))
        self.assertEqual(len(ss.read_note(stream)), ss.NOTE_READ_LIMIT)

    def test_no_second_note(self):
        picture = self.saved()
        self.run_cli("note", str(picture), FOLDER, stdin=b"one")
        code, result = self.run_cli("note", str(picture), FOLDER, stdin=b"two")
        self.assertEqual(code, 1)
        self.assertEqual(picture.with_suffix(".txt").read_text(), "one\n")

    def test_picture_outside_folder_is_refused(self):
        picture = self.saved()
        elsewhere = self.home / picture.name
        elsewhere.write_bytes(PNG)
        for path in (elsewhere, self.pictures / ".." / picture.name, self.pictures / "Other.PNG"):
            code, result = self.run_cli("note", str(path), FOLDER, stdin=b"x")
            self.assertEqual(code, 1, path)
        self.assertFalse(elsewhere.with_suffix(".txt").exists())

    def test_missing_picture_is_refused(self):
        self.pictures.mkdir(parents=True, mode=0o700)
        code, result = self.run_cli("note", str(self.pictures / "llm-monitor-20261110-120000.png"), FOLDER, stdin=b"x")
        self.assertEqual(code, 1)
        self.assertEqual(list(self.pictures.iterdir()), [])

    def test_note_refused_when_folder_turned_unsafe(self):
        picture = self.saved()
        os.chmod(self.pictures, 0o777)
        code, result = self.run_cli("note", str(picture), FOLDER, stdin=b"x")
        self.assertEqual(code, 1)
        self.assertFalse(picture.with_suffix(".txt").exists())

    def test_note_through_symlink_is_refused(self):
        picture = self.saved()
        target = self.home / "target.txt"
        picture.with_suffix(".txt").symlink_to(target)
        code, result = self.run_cli("note", str(picture), FOLDER, stdin=b"x")
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())

    def test_failed_note_write_leaves_nothing(self):
        picture = self.saved()
        with mock.patch.object(ss.os, "fsync", side_effect=OSError(5, "Input/output error")):
            code, result = self.run_cli("note", str(picture), FOLDER, stdin=b"x")
        self.assertEqual(code, 1)
        self.assertFalse(picture.with_suffix(".txt").exists())


if __name__ == "__main__":
    unittest.main()
