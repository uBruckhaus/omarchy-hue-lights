#!/usr/bin/env python3
"""python3 plugin_catalog.py <plugin id> — the author's plugins in the Omarchy Plugin Marketplace,
for the Information page's cards (MorePlugins.qml runs it when the page opens).

Generated from ~/Projects/omarchy-plugin-i18n/qml/plugin_catalog.py by install_more.py; edit it
there, not in a plugin.

Prints one JSON line, {"plugins": [{"id", "name", "summary"}], "checked": <time or 0>}, and exits.
The catalog (the file the marketplace site and My Plugins use) is read at most once an hour,
failed attempts included, and only if it changed (its ETag). Only the author's own entries are
kept, in ~/.cache/<plugin id>/plugin-catalog.json. Without the network the cached list is printed,
else an empty one (the page then shows its built-in list). Python standard library plus curl.
"""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import secrets
import time
from pathlib import Path

CATALOG_URL = "https://raw.githubusercontent.com/omacom/omarchy-plugin-marketplace/main/site/catalog.json"
OWNER = "ubruckhaus"                       # the repositories' owner on GitHub (the author's plugins)
MAX_AGE = 3600
MAX_BYTES = 40 * 1024 * 1024
MAX_CACHE_BYTES = 1024 * 1024
MAX_PLUGINS = 60
TIMEOUT = 40
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/[A-Za-z0-9._-]{1,100}/?$")
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
ETAG = re.compile(r'^(?:W/)?"[\x21\x23-\x7e]{1,200}"$')


def cache_path(plugin_id: str) -> Path:
    """~/.cache/<plugin id>/plugin-catalog.json (always under the home folder)."""
    if not ID.fullmatch(plugin_id) or ".." in plugin_id:
        raise ValueError("not a plugin id")
    return Path.home() / ".cache" / plugin_id / "plugin-catalog.json"


def _open_dir(folder: Path, create: bool) -> int:
    """A descriptor of the cache folder: ours, not a link, no access for others (made 0700 if missing).
    Everything else in it is opened relative to this descriptor, so the path cannot be swapped."""
    if create:
        try:
            folder.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise OSError("cache folder is not private")
    return fd


def owner_of(repo) -> str:
    match = REPO.match(repo or "") if isinstance(repo, str) else None
    return match.group(1).lower() if match else ""


def _text(value, limit: int) -> str:
    """Plain text QML and the cache can take: JSON's escaped lone surrogates become "?"."""
    return str(value or "").encode("utf-8", "replace").decode("utf-8")[:limit]


def _entry(plugin_id, name, description="", version="", verified=False):
    """One plugin as kept and shown, or None: the same limits for the network and the cache."""
    if not isinstance(plugin_id, str) or not ID.fullmatch(plugin_id) or not isinstance(name, str):
        return None
    name = _text(name.strip(), 60)
    if not name.strip():
        return None
    return {"id": plugin_id, "name": name, "description": _text(description, 600),
            "version": _text(version, 30), "verified": verified is True}


def parse(data) -> list:
    """The author's entries of a catalog: [{id, name, description, version, verified}]."""
    items = data.get("plugins") if isinstance(data, dict) else None
    found = []
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or owner_of(entry.get("repo")) != OWNER:
            continue
        kept = _entry(entry.get("id"), entry.get("name"), entry.get("description"), entry.get("version"),
                      entry.get("verificationStatus") == "verified")
        if kept:
            found.append(kept)
            if len(found) >= MAX_PLUGINS:
                break
    return found


def _time(value) -> float:
    """A stored time, or 0 (stale) for anything that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        number = float(value)          # a huge int (10**400) does not fit
    except OverflowError:
        return 0.0
    return number if math.isfinite(number) else 0.0


def _read_cache(path: Path) -> bytes:
    """The cache file's bytes: a regular file of ours in our private folder, not a link, FIFO or
    device, read once and bounded."""
    folder = _open_dir(path.parent, create=False)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=folder)
    finally:
        os.close(folder)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("not a regular file of this user")
        chunks, size = [], 0
        while size <= MAX_CACHE_BYTES:
            chunk = os.read(fd, MAX_CACHE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_CACHE_BYTES:
            raise OSError("cache too large")
        return b"".join(chunks)
    finally:
        os.close(fd)


def load(path: Path) -> dict:
    """The cache, checked like an answer from the network (anyone of this account can edit it)."""
    try:
        data = json.loads(_read_cache(path).decode("utf-8"))
    except (OSError, ValueError, RecursionError):      # deeply nested JSON too
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        return {}
    plugins = []
    for p in data["plugins"]:
        kept = _entry(p.get("id"), p.get("name"), p.get("description"), p.get("version"),
                      p.get("verified")) if isinstance(p, dict) else None
        if kept:
            plugins.append(kept)
            if len(plugins) >= MAX_PLUGINS:
                break
    etag = data.get("etag") if isinstance(data.get("etag"), str) and ETAG.fullmatch(data["etag"]) else ""
    return {"plugins": plugins, "etag": etag, "checked": _time(data.get("checked")),
            "tried": _time(data.get("tried"))}


def _save(data: dict, path: Path):
    """Atomically, through an exclusive 0600 temporary file in the private folder."""
    text = json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8")
    folder = _open_dir(path.parent, create=True)
    try:
        tmp = f".plugin-catalog-{secrets.token_hex(8)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text)
            os.replace(tmp, path.name, src_dir_fd=folder, dst_dir_fd=folder)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=folder)
            except OSError:
                pass
            raise
    finally:
        os.close(folder)


def split_response(raw: bytes) -> tuple[int, dict, bytes]:
    """curl --include output: (status, headers of the last response, body)."""
    status, headers, rest = 0, {}, raw
    while rest.startswith(b"HTTP/"):
        head, sep, body = rest.partition(b"\r\n\r\n")
        if not sep:
            break
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        headers = {k.strip().lower(): v.strip() for k, _s, v in (line.partition(":") for line in lines[1:]) if k}
        rest = body
    return status, headers, rest


def curl_command(etag: str = "") -> list:
    """Fixed https address, no redirects, bounded in size and time; the ETag only when it looks like one."""
    argv = ["curl", "--silent", "--include", "--proto", "=https", "--max-redirs", "0",
            "--max-time", str(TIMEOUT), "--max-filesize", str(MAX_BYTES), "--user-agent", "omarchy-plugin"]
    if etag and ETAG.fullmatch(etag):
        argv += ["--header", f"If-None-Match: {etag}"]
    return argv + ["--", CATALOG_URL]


def read_bounded(argv: list, max_bytes: int, timeout: float) -> bytes:
    """argv's output, at most max_bytes within timeout seconds (no shell); raises OSError otherwise."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise OSError("no answer in time")
                    chunk = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(output)))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > max_bytes:
                        raise OSError("answer too large")
            if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                raise OSError("curl failed")
            return bytes(output)
        except subprocess.TimeoutExpired as error:
            raise OSError("no answer in time") from error
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def fetch(etag: str = "") -> bytes:
    if not shutil.which("curl"):
        raise OSError("curl is not installed")
    return read_bounded(curl_command(etag), MAX_BYTES + 64 * 1024, TIMEOUT + 5)


# Cache path -> last request, should the cache not be writable. This script runs once per page
# visit, so MorePlugins.qml keeps its own hourly guard as well.
_ATTEMPTS = {}


def refresh(path: Path, fetch=fetch, now: float | None = None) -> dict:
    """The cache record after reading the catalog again when the last attempt is older than MAX_AGE.
    A failed attempt counts too; errors and malformed answers keep the cached list."""
    now = time.time() if now is None else now
    cached = load(path)
    plugins = cached.get("plugins", [])
    last = max(cached.get("checked", 0), cached.get("tried", 0), _ATTEMPTS.get(str(path), 0))
    if last and 0 <= now - last < MAX_AGE:
        return cached
    _ATTEMPTS[str(path)] = now
    try:
        status, answer, body = split_response(fetch(cached.get("etag", "")))
        if status == 304 and cached:
            pass
        elif status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
                raise ValueError("not a catalog")      # keeps the list read before
            plugins = parse(data)
        else:
            raise OSError(f"HTTP {status}")
        etag = answer.get("etag", "")
        record = {"checked": now, "etag": etag if ETAG.fullmatch(etag) else "", "plugins": plugins}
    except Exception:          # offline, too large, refused, not JSON: the cached list (or none)
        record = {**cached, "plugins": plugins, "tried": now}
    try:
        _save(record, path)
    except OSError:
        pass
    return record


def first_sentence(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    cut = re.split(r"(?<=[.!?:])\s", text, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[:limit - 1].rsplit(" ", 1)[0] + "…"


def output(record: dict) -> dict:
    """What MorePlugins.qml reads: id, name and a one-line summary per plugin."""
    return {"plugins": [{"id": p["id"], "name": p["name"], "summary": first_sentence(p["description"])}
                        for p in record.get("plugins", [])],
            "checked": record.get("checked", 0)}


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    try:
        path = cache_path(argv[1])
    except ValueError:
        return 2
    print(json.dumps(output(refresh(path)), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
