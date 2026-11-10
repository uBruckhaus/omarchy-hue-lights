#!/usr/bin/env python3
"""F12 screenshots of a plugin's shell panel (PanelShot.qml), saved like AI Playground's.

    shot_save.py prepare <plugin id>             the private folder the panel writes its PNG into
    shot_save.py cleanup <plugin id>             remove its left-over PNGs (when the panel loads)
    shot_save.py save <tmp.png> "<Folder name>"  move that PNG to ~/Pictures/<Folder name>/
    shot_save.py note <picture> "<Folder name>"  the note from stdin, next to the picture as .txt

Every call prints one JSON line, {"ok": true, ...} or {"ok": false, "error": "..."}, and exits 0 or 1.

Rules (as screenshot.py in AI Playground): the picture folder is ~/Pictures/<Folder name>
(XDG_PICTURES_DIR from user-dirs.dirs when set), made 0700 when missing, and used only when it is a
real folder (not a link) owned by this user and not writable by group or others. Files are created
in it relative to that folder handle, never through a link and never over an existing file (0600,
written completely, fsync'd, removed again when writing fails). A note is at most 4000 characters;
no text, no file.

The panel's PNG must be a regular file of this user in $XDG_RUNTIME_DIR/<plugin id>-shot/ (a 0700
folder of this user, not a link, made inside $XDG_RUNTIME_DIR, which must itself be a real folder of
this user that no one else can write to, and every folder above it a real folder of root or this
user that no one else can change: Qt writes the PNG by path), at most 32 MiB, with a PNG signature; it is removed
afterwards. PNGs left there (a save that never ran) go after STALE_SECONDS, on the next prepare
and when the panel loads (cleanup).
Nothing is sent anywhere and no other program is started. Python standard library only."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time

NOTE_LIMIT = 4000                 # characters of a note: a few sentences, not a document
NOTE_READ_LIMIT = 64 * 1024       # bytes read from stdin at most
MAX_PICTURE = 32 * 1024 * 1024    # bytes of one PNG at most
STALE_SECONDS = 120               # left-over panel PNGs older than this go (prepare, cleanup);
                                  # a PNG lives for the second between the panel's write and save
USER_DIRS_LIMIT = 64 * 1024       # bytes of user-dirs.dirs read at most
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHOT_DIR = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}-shot$")
TMP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.png$")
PICTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.png$")


class ShotError(Exception):
    pass


# ------------------------------------------------------------------ folders

def home() -> str:
    return os.path.expanduser("~")


def pictures_dir() -> str:
    """XDG_PICTURES_DIR from user-dirs.dirs (only "$HOME/…" or an absolute path), else ~/Pictures."""
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
    text = ""
    try:
        # A regular file only (not a link, FIFO or device), read bounded; else ~/Pictures.
        fd = os.open(os.path.join(config, "user-dirs.dirs"),
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        fd = None
    if fd is not None:
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                data = os.read(fd, USER_DIRS_LIMIT + 1)
                if len(data) <= USER_DIRS_LIMIT:
                    text = data.decode("utf-8", "replace")
        except OSError:
            text = ""
        finally:
            os.close(fd)
    found = None
    for line in text.splitlines():
        match = re.match(r'^\s*XDG_PICTURES_DIR\s*=\s*"([^"]*)"\s*$', line)
        if not match:
            continue
        value = match.group(1)
        if value.startswith("$HOME/"):
            value = os.path.join(home(), value[len("$HOME/"):])
        elif value == "$HOME":
            value = home()
        if os.path.isabs(value) and value.rstrip("/"):
            found = os.path.normpath(value)
    return found or os.path.join(home(), "Pictures")


def check_folder_name(name: str) -> str:
    if (not name or len(name) > 64 or name in (".", "..") or name.startswith(".")
            or "/" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ShotError("not a usable folder name")
    return name


def folder(name: str) -> str:
    return os.path.join(pictures_dir(), check_folder_name(name))


def private_folder(where: str) -> int:
    """A handle on the screenshot folder: made 0700 when missing, and only a real folder (not a
    link) owned by this user and not writable by others is used. The caller closes the handle."""
    os.makedirs(os.path.dirname(where), exist_ok=True)
    try:
        os.mkdir(where, 0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(where, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        raise ShotError(f"{shown(where)} is not a private folder of yours") from error
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(fd)
        raise ShotError(f"{shown(where)} is not a private folder of yours")
    return fd


def closed_folder(path: str, info) -> bool:
    """A real directory no other account can change: owned by root or this user and not writable
    by group or others, or owned by root with the sticky bit (as /tmp: names in it stay theirs)."""
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
        return False
    return not info.st_mode & 0o022 or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX))


def runtime_root() -> str:
    """$XDG_RUNTIME_DIR: a real folder of this user that group and others cannot write, under
    folders (from / down) that no other account can change. Qt writes the panel's PNG there by
    path, so no part of that path may be replaceable by someone else."""
    root = os.environ.get("XDG_RUNTIME_DIR") or ""
    if not os.path.isabs(root):
        raise ShotError("XDG_RUNTIME_DIR is not set")
    root = os.path.normpath(root)
    try:
        info = os.lstat(root)
    except OSError as error:
        raise ShotError("XDG_RUNTIME_DIR is missing") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ShotError("XDG_RUNTIME_DIR is not a private folder of yours")
    above = os.path.dirname(root)
    while True:
        try:
            if not closed_folder(above, os.lstat(above)):
                raise ShotError(f"{above} (above XDG_RUNTIME_DIR) is open to other accounts")
        except OSError as error:
            raise ShotError("XDG_RUNTIME_DIR is missing") from error
        if above == "/":
            break
        above = os.path.dirname(above)
    return root


def open_runtime_folder(path: str, create: bool) -> int:
    """$XDG_RUNTIME_DIR/<plugin id>-shot: a 0700 folder of this user, not a link, made and opened
    relative to a checked handle on $XDG_RUNTIME_DIR."""
    parent, name = os.path.split(path)
    if parent != runtime_root() or not SHOT_DIR.match(name):
        raise ShotError("the panel's picture folder is not a private folder of yours")
    root = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(root)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ShotError("XDG_RUNTIME_DIR is not a private folder of yours")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=root)
            except FileExistsError:
                pass
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
        except OSError as error:
            raise ShotError("the panel's picture folder is not a private folder of yours") from error
    finally:
        os.close(root)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ShotError("the panel's picture folder is not a private folder of yours")
    return fd


def shown(path: str) -> str:
    """The path with ~ for the home folder, as the panel shows it."""
    base = home().rstrip("/")
    return "~" + path[len(base):] if base and (path == base or path.startswith(base + "/")) else path


# ------------------------------------------------------------------ files

def _taken(fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def slug(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", folder_name.lower()).strip("-")[:40] or "screenshot"


def free_name(fd: int, prefix: str, stamp: str) -> str:
    """<prefix>-<stamp>.png, or -2, -3 … when it or its note already exists in the folder."""
    name, n = f"{prefix}-{stamp}.png", 2
    while _taken(fd, name) or _taken(fd, name[:-4] + ".txt"):
        name = f"{prefix}-{stamp}-{n}.png"
        n += 1
    return name


def write_new(fd: int, name: str, data: bytes):
    """A new file of our own in the folder (never through a link, never over an existing file);
    written completely or not left behind at all."""
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(out, view):]
        os.fsync(out)
    except BaseException:
        os.close(out)
        os.unlink(name, dir_fd=fd)
        raise
    os.close(out)


def read_panel_png(dfd: int, name: str) -> bytes:
    """The panel's PNG: a regular file of this user, at most MAX_PICTURE bytes, PNG signature."""
    try:
        info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ShotError("the panel's picture is missing") from error
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ShotError("the panel's picture is not a file of yours")
    if info.st_size > MAX_PICTURE:
        raise ShotError("the panel's picture is too large")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dfd)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise ShotError("the panel's picture changed while it was read")
        chunks, size = [], 0
        while size <= MAX_PICTURE:
            chunk = os.read(fd, min(1024 * 1024, MAX_PICTURE + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_PICTURE:
        raise ShotError("the panel's picture is too large")
    if not data.startswith(PNG_SIGNATURE):
        raise ShotError("the panel's picture is not a PNG")
    return data


# ------------------------------------------------------------------ commands

def remove_stale(fd: int):
    now = time.time()
    for name in os.listdir(fd):
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if name.endswith(".png") and now - info.st_mtime > STALE_SECONDS:
                os.unlink(name, dir_fd=fd)
        except OSError:
            pass


def prepare(plugin_id: str) -> dict:
    if not PLUGIN_ID.match(plugin_id or ""):
        raise ShotError("not a plugin id")
    path = os.path.join(runtime_root(), plugin_id + "-shot")
    fd = open_runtime_folder(path, create=True)
    try:
        remove_stale(fd)
    finally:
        os.close(fd)
    return {"ok": True, "dir": path}


def cleanup(plugin_id: str) -> dict:
    """Left-over PNGs of an earlier session; nothing is made when the folder is missing."""
    if not PLUGIN_ID.match(plugin_id or ""):
        raise ShotError("not a plugin id")
    path = os.path.join(runtime_root(), plugin_id + "-shot")
    try:
        os.lstat(path)
    except FileNotFoundError:
        return {"ok": True, "dir": None}
    fd = open_runtime_folder(path, create=False)
    try:
        remove_stale(fd)
    finally:
        os.close(fd)
    return {"ok": True, "dir": path}


def save(tmp: str, folder_name: str) -> dict:
    where = folder(folder_name)
    tmp = os.path.normpath(os.path.abspath(tmp))
    parent, name = os.path.split(tmp)
    above, shot_dir = os.path.split(parent)
    if above != runtime_root() or not SHOT_DIR.match(shot_dir) or not TMP_NAME.match(name):
        raise ShotError("the picture is not in the panel's private folder")
    dfd = open_runtime_folder(parent, create=False)
    try:
        data = read_panel_png(dfd, name)
        fd = private_folder(where)
        try:
            picture = free_name(fd, slug(folder_name), time.strftime("%Y%m%d-%H%M%S"))
            write_new(fd, picture, data)
        finally:
            os.close(fd)
    finally:
        try:
            os.unlink(name, dir_fd=dfd)          # only ever inside the verified private folder
        except OSError:
            pass
        os.close(dfd)
    path = os.path.join(where, picture)
    return {"ok": True, "path": path, "shown": shown(path)}


def note(picture: str, folder_name: str, text: str) -> dict:
    where = folder(folder_name)
    picture = os.path.normpath(os.path.abspath(picture))
    parent, name = os.path.split(picture)
    if parent != where or not PICTURE_NAME.match(name):
        raise ShotError("the picture is not in the screenshot folder")
    text = text.strip()[:NOTE_LIMIT]
    if not text:
        return {"ok": True, "path": None}
    fd = private_folder(where)
    try:
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ShotError("the picture is gone") from error
        if not stat.S_ISREG(info.st_mode):
            raise ShotError("the picture is not a file")
        write_new(fd, name[:-4] + ".txt", (text + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    path = os.path.join(where, name[:-4] + ".txt")
    return {"ok": True, "path": path, "shown": shown(path)}


def read_note(stream) -> str:
    return stream.read(NOTE_READ_LIMIT).decode("utf-8", "replace")


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "prepare":
            result = prepare(argv[1])
        elif len(argv) == 2 and argv[0] == "cleanup":
            result = cleanup(argv[1])
        elif len(argv) == 3 and argv[0] == "save":
            result = save(argv[1], argv[2])
        elif len(argv) == 3 and argv[0] == "note":
            result = note(argv[1], argv[2], read_note(sys.stdin.buffer))
        else:
            raise ShotError("usage: shot_save.py prepare <id> | cleanup <id> | save <tmp.png> <folder> | note <picture> <folder>")
    except ShotError as error:
        result = {"ok": False, "error": str(error)}
    except OSError as error:
        result = {"ok": False, "error": error.strerror or str(error)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
