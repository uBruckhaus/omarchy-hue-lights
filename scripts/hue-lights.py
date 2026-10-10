#!/usr/bin/env python3
"""Philips Hue bridge helper for the Hue Lights Omarchy plugin.

Long-running process: reads one JSON command per line on stdin and prints one
JSON snapshot per line on stdout. Talks to the bridge's local CLIP v2 API over
HTTPS only. The bridge certificate is self-signed by Philips, so it is pinned
(SHA-256 fingerprint + CN == bridge id) when pairing and checked on every
connection afterwards. The application key lives in
~/.config/omarchy/hue-lights/config.json (mode 0600) and never leaves this
process.
"""

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy", "hue-lights")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

POLL_OPEN = 2.0       # seconds between refreshes while the panel is open
POLL_CLOSED = 20.0    # seconds between refreshes while it is closed
PAIR_WINDOW = 35.0    # how long to keep asking for a key after "pair"
LIGHT_INTERVAL = 0.12 # Hue guidance: ~10 light commands per second
GROUP_INTERVAL = 0.6  # ... and ~1 group command per second
HTTP_TIMEOUT = 4.0

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BRIDGE_ID_RE = re.compile(r"^[0-9A-F]{16}$")


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)

def valid_uuid(value):
    return isinstance(value, str) and UUID_RE.match(value) is not None


def valid_bridge_ip(value):
    """Only private / link-local IPv4 literals: the bridge is always on the LAN."""
    try:
        ip = ipaddress.IPv4Address(str(value))
    except (ipaddress.AddressValueError, ValueError):
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast


def clamp(value, low, high):
    return max(low, min(high, value))


def clean(text, limit=60):
    text = "".join(ch for ch in str(text or "") if ch.isprintable())
    return text.strip()[:limit]


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def _der_items(buf, pos, end):
    """Yield (tag, content_start, content_end) for the DER items in buf[pos:end]."""
    while pos < end:
        tag = buf[pos]
        length = buf[pos + 1]
        pos += 2
        if length & 0x80:
            count = length & 0x7F
            length = int.from_bytes(buf[pos:pos + count], "big")
            pos += count
        yield tag, pos, pos + length
        pos += length


def cert_common_name(der):
    """Subject CN of a DER certificate (Certificate > TBSCertificate > subject)."""
    try:
        _, cert_start, cert_end = next(_der_items(der, 0, len(der)))
        _, tbs_start, tbs_end = next(_der_items(der, cert_start, cert_end))
        fields = list(_der_items(der, tbs_start, tbs_end))
        if fields and fields[0][0] == 0xA0:  # explicit version
            fields = fields[1:]
        _, subj_start, subj_end = fields[4]   # serial, sigAlg, issuer, validity, subject
        for _, set_start, set_end in _der_items(der, subj_start, subj_end):
            for _, atv_start, atv_end in _der_items(der, set_start, set_end):
                (_, oid_s, oid_e), (_, val_s, val_e) = list(_der_items(der, atv_start, atv_end))[:2]
                if der[oid_s:oid_e] == b"\x55\x04\x03":
                    return der[val_s:val_e].decode("utf-8", "replace")
    except (StopIteration, IndexError, ValueError):
        pass
    return ""


def _gamma(c):
    c = clamp(c, 0.0, 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def xy_to_hex(x, y):
    """CIE xy (Hue colour) to an sRGB hex string at full brightness, for a swatch."""
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return "#ffd27a"
    if y <= 0:
        return "#ffd27a"
    z = 1.0 - x - y
    Y = 1.0
    X = (Y / y) * x
    Z = (Y / y) * z
    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530
    peak = max(r, g, b)
    if peak > 1:
        r, g, b = r / peak, g / peak, b / peak
    return "#%02x%02x%02x" % tuple(int(round(_gamma(c) * 255)) for c in (r, g, b))


def mirek_to_hex(mirek):
    """Colour temperature (mirek) to an approximate sRGB hex (Tanner Helland fit)."""
    try:
        kelvin = 1000000.0 / float(mirek)
    except (TypeError, ValueError, ZeroDivisionError):
        return "#ffd27a"
    t = clamp(kelvin, 1000, 40000) / 100.0
    import math
    if t <= 66:
        r = 255
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255
    return "#%02x%02x%02x" % tuple(int(clamp(c, 0, 255)) for c in (r, g, b))


def light_color(light):
    ct = light.get("color_temperature") or {}
    if ct.get("mirek_valid") and ct.get("mirek"):
        return mirek_to_hex(ct["mirek"])
    xy = (light.get("color") or {}).get("xy")
    if xy:
        return xy_to_hex(xy.get("x"), xy.get("y"))
    if ct.get("mirek"):
        return mirek_to_hex(ct["mirek"])
    return "#ffd27a"


def build_model(resources):
    """Turn the flat CLIP v2 /resource list into rooms -> lights / scenes."""
    by_type = {}
    for res in resources or []:
        by_type.setdefault(res.get("type"), []).append(res)
    lights = {r["id"]: r for r in by_type.get("light", []) if valid_uuid(r.get("id"))}
    grouped = {r["id"]: r for r in by_type.get("grouped_light", []) if valid_uuid(r.get("id"))}
    devices = {r["id"]: r for r in by_type.get("device", []) if valid_uuid(r.get("id"))}
    connectivity = {}
    for zc in by_type.get("zigbee_connectivity", []):
        owner = (zc.get("owner") or {}).get("rid")
        if owner:
            connectivity[owner] = zc.get("status") == "connected"

    def light_entry(light):
        dimming = light.get("dimming") or {}
        owner = (light.get("owner") or {}).get("rid")
        return {
            "id": light["id"],
            "name": clean((light.get("metadata") or {}).get("name") or "Light"),
            "on": bool((light.get("on") or {}).get("on")),
            "bri": round(float(dimming.get("brightness", 100.0)), 1),
            "dimmable": "dimming" in light,
            "color": light_color(light),
            "reachable": connectivity.get(owner, True),
        }

    def lights_of(group):
        ids = []
        for child in group.get("children") or []:
            rid, rtype = child.get("rid"), child.get("rtype")
            if rtype == "light" and rid in lights:
                ids.append(rid)
            elif rtype == "device" and rid in devices:
                for svc in devices[rid].get("services") or []:
                    if svc.get("rtype") == "light" and svc.get("rid") in lights:
                        ids.append(svc["rid"])
        return ids

    scenes_by_group = {}
    for scene in by_type.get("scene", []):
        gid = (scene.get("group") or {}).get("rid")
        if not valid_uuid(scene.get("id")) or not gid:
            continue
        scenes_by_group.setdefault(gid, []).append({
            "id": scene["id"],
            "name": clean((scene.get("metadata") or {}).get("name") or "Scene", 30),
            "active": (scene.get("status") or {}).get("active", "inactive") != "inactive",
        })

    rooms = []
    assigned = set()
    for kind in ("room", "zone"):
        for group in by_type.get(kind, []):
            gl_id = None
            for svc in group.get("services") or []:
                if svc.get("rtype") == "grouped_light" and svc.get("rid") in grouped:
                    gl_id = svc["rid"]
            ids = lights_of(group)
            if not ids:
                continue
            if kind == "room":
                assigned.update(ids)
            members = sorted((light_entry(lights[i]) for i in ids), key=lambda l: l["name"].lower())
            on_members = [l for l in members if l["on"]]
            gl = grouped.get(gl_id) or {}
            bri = (gl.get("dimming") or {}).get("brightness")
            if bri is None:
                bri = sum(l["bri"] for l in on_members) / len(on_members) if on_members else 0
            rooms.append({
                "id": group["id"],
                "kind": kind,
                "name": clean((group.get("metadata") or {}).get("name") or kind.title()),
                "grouped_light": gl_id or "",
                "on": any(l["on"] for l in members),
                "bri": round(float(bri), 1),
                "on_count": len(on_members),
                "lights": members,
                "scenes": sorted(scenes_by_group.get(group["id"], []), key=lambda s: s["name"].lower()),
            })
    loose = [light_entry(l) for lid, l in lights.items() if lid not in assigned]
    if loose:
        loose.sort(key=lambda l: l["name"].lower())
        on_members = [l for l in loose if l["on"]]
        rooms.append({
            "id": "", "kind": "other", "name": "Other lights", "grouped_light": "",
            "on": bool(on_members),
            "bri": round(sum(l["bri"] for l in on_members) / len(on_members), 1) if on_members else 0,
            "on_count": len(on_members), "lights": loose, "scenes": [],
        })
    rooms.sort(key=lambda r: (r["kind"] != "room", r["name"].lower()))
    return rooms, len(lights), sum(1 for l in lights.values() if (l.get("on") or {}).get("on"))


def parse_avahi_hue(lines):
    """Parse `avahi-browse -rtpk _hue._tcp` output into [{ip, bridge_id, name}]."""
    found = {}
    for line in lines:
        parts = line.split(";")
        if len(parts) < 10 or parts[0] != "=" or parts[2] != "IPv4":
            continue
        ip = parts[7]
        if not valid_bridge_ip(ip):
            continue
        bridge_id = ""
        m = re.search(r'"bridgeid=([0-9a-fA-F]{16})"', ";".join(parts[9:]))
        if m:
            bridge_id = m.group(1).upper()
        name = parts[3].replace("\\032", " ")
        name = re.sub(r"\\(\d{3})", lambda mm: chr(int(mm.group(1))), name)
        found[ip] = {"ip": ip, "bridge_id": bridge_id, "name": clean(name)}
    return sorted(found.values(), key=lambda b: b["ip"])


def parse_command(line):
    try:
        cmd = json.loads(line)
    except (ValueError, TypeError):
        return None
    return cmd if isinstance(cmd, dict) and isinstance(cmd.get("cmd"), str) else None


# ---------------------------------------------------------------------------
# Config

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if not (valid_bridge_ip(data.get("ip")) and isinstance(data.get("app_key"), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(data.get("fingerprint", "")))):
        return {}
    return data


def save_config(data):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------------------
# HTTPS with certificate pinning

class PinError(Exception):
    pass


def https_request(ip, method, path, body=None, headers=None, pin=None, timeout=HTTP_TIMEOUT):
    """One HTTPS request to the bridge. Returns (status, parsed_json, (fingerprint, cn)).

    With `pin`, the peer certificate must match pin["fingerprint"] and have
    CN == pin["bridge_id"], otherwise PinError is raised before anything is sent.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed Philips cert; pinned below instead
    conn = http.client.HTTPSConnection(ip, 443, timeout=timeout, context=ctx)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True) or b""
        peer = (fingerprint(der), cert_common_name(der).upper())
        if pin is not None:
            if peer[0] != pin.get("fingerprint") or (pin.get("bridge_id") and peer[1] != pin["bridge_id"]):
                raise PinError("bridge certificate changed")
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read(4 * 1024 * 1024)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = None
        return resp.status, parsed, peer
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main state machine

class Hue:
    def __init__(self):
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.config = load_config()
        self.panel_open = False
        self.rooms = []
        self.light_count = 0
        self.on_count = 0
        self.phase = "connecting" if self.config else "unpaired"
        self.error = ""
        self.candidates = []
        self.discovering = False
        self.pair_ip = ""
        self.pair_until = 0.0
        self.pending = {}          # (kind, id) -> body, coalesced
        self.last_sent = {"light": 0.0, "grouped_light": 0.0, "scene": 0.0}
        self.last_refresh = 0.0
        self.refresh_hold_until = 0.0

    # -- output
    def snapshot(self):
        with self.lock:
            bridge = {}
            if self.config:
                bridge = {"ip": self.config.get("ip", ""), "name": self.config.get("name", "Hue Bridge"),
                          "model": self.config.get("model", "")}
            return {
                "phase": self.phase, "error": self.error, "bridge": bridge,
                "rooms": self.rooms, "light_count": self.light_count, "on_count": self.on_count,
                "candidates": self.candidates, "discovering": self.discovering,
                "pair_ip": self.pair_ip,
                "pair_remaining": max(0, int(self.pair_until - time.time())) if self.pair_ip else 0,
            }

    def emit(self):
        line = json.dumps(self.snapshot(), separators=(",", ":"))
        with self.lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # -- bridge access
    def pin(self):
        return {"fingerprint": self.config.get("fingerprint"), "bridge_id": self.config.get("bridge_id", "")}

    def api(self, method, path, body=None):
        status, data, _ = https_request(self.config["ip"], method, path, body,
                                        {"hue-application-key": self.config["app_key"]}, pin=self.pin())
        if status in (401, 403):
            raise PermissionError("bridge rejected the app key")
        if status >= 400:
            raise OSError("bridge returned HTTP %d" % status)
        return data

    def refresh(self):
        if not self.config:
            return
        try:
            data = self.api("GET", "/clip/v2/resource")
            rooms, total, on = build_model((data or {}).get("data"))
            with self.lock:
                if time.time() >= self.refresh_hold_until:
                    self.rooms, self.light_count, self.on_count = rooms, total, on
                self.phase, self.error = "ok", ""
        except PinError:
            with self.lock:
                self.phase, self.error = "error", "The bridge certificate changed. Unpair and pair again if you replaced the bridge."
        except PermissionError:
            with self.lock:
                self.phase, self.error = "error", "The bridge no longer accepts this app. Unpair and pair again."
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            with self.lock:
                self.phase, self.error = "error", "Bridge not reachable (%s)" % clean(exc, 80)
            self.relocate()
        self.last_refresh = time.time()

    def relocate(self):
        """The bridge got a new DHCP address: find it again by bridge id via mDNS."""
        bridge_id = self.config.get("bridge_id")
        if not bridge_id:
            return
        for cand in self.discover_now():
            if cand["bridge_id"] == bridge_id and cand["ip"] != self.config.get("ip"):
                with self.lock:
                    self.config["ip"] = cand["ip"]
                    save_config(self.config)
                self.wake.set()
                return

    def discover_now(self):
        try:
            out = subprocess.run(["avahi-browse", "-rtpk", "_hue._tcp"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=6).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return parse_avahi_hue(out.splitlines())

    def discover(self):
        with self.lock:
            self.discovering = True
        self.emit()
        found = self.discover_now()
        with self.lock:
            self.candidates, self.discovering = found, False
        self.emit()

    # -- pairing
    def try_pair(self):
        ip = self.pair_ip
        try:
            _, cfg, peer = https_request(ip, "GET", "/api/0/config")
            bridge_id = str((cfg or {}).get("bridgeid", "")).upper()
            if not BRIDGE_ID_RE.match(bridge_id) or peer[1] != bridge_id:
                with self.lock:
                    self.error = "%s does not look like a Hue bridge" % ip
                    self.pair_ip = ""
                    self.phase = "unpaired"
                return
            host = clean(socket.gethostname(), 19) or "pc"
            _, resp, peer2 = https_request(ip, "POST", "/api",
                                           {"devicetype": "omarchy_hue_lights#" + host, "generateclientkey": True})
            if peer2 != peer:
                raise PinError("certificate changed during pairing")
            entry = resp[0] if isinstance(resp, list) and resp else {}
            if "success" in entry and isinstance(entry["success"].get("username"), str):
                config = {
                    "ip": ip, "bridge_id": bridge_id, "fingerprint": peer[0],
                    "app_key": entry["success"]["username"],
                    "name": clean(cfg.get("name") or "Hue Bridge"), "model": clean(cfg.get("modelid") or ""),
                }
                save_config(config)
                with self.lock:
                    self.config, self.pair_ip, self.phase, self.error = config, "", "connecting", ""
                self.wake.set()
            elif (entry.get("error") or {}).get("type") == 101:
                with self.lock:
                    self.error = ""  # link button not pressed yet: keep asking
            else:
                with self.lock:
                    self.error = clean((entry.get("error") or {}).get("description") or "pairing failed", 80)
        except (OSError, socket.timeout, http.client.HTTPException, PinError, ValueError) as exc:
            with self.lock:
                self.error = "Cannot reach %s (%s)" % (ip, clean(exc, 60))

    # -- control commands
    def queue(self, kind, rid, body):
        with self.lock:
            key = (kind, rid)
            merged = dict(self.pending.get(key, {}))
            merged.update(body)
            self.pending[key] = merged
            self.apply_optimistic(kind, rid, body)
            self.refresh_hold_until = time.time() + 1.0
        self.emit()
        self.wake.set()

    def apply_optimistic(self, kind, rid, body):
        on = body.get("on", {}).get("on") if "on" in body else None
        bri = body.get("dimming", {}).get("brightness") if "dimming" in body else None
        for room in self.rooms:
            targets = []
            if kind == "grouped_light" and room["grouped_light"] == rid:
                targets = room["lights"]
                if on is not None:
                    room["on"] = on
                if bri is not None:
                    room["bri"], room["on"] = bri, True
            elif kind == "light":
                targets = [l for l in room["lights"] if l["id"] == rid]
            for light in targets:
                if on is not None:
                    light["on"] = on
                if bri is not None:
                    light["bri"], light["on"] = bri, True
            if kind == "light" and targets:
                lit = [l for l in room["lights"] if l["on"]]
                room["on"], room["on_count"] = bool(lit), len(lit)
                if lit and room["grouped_light"] == "":
                    room["bri"] = round(sum(l["bri"] for l in lit) / len(lit), 1)
            elif kind == "grouped_light" and targets:
                room["on_count"] = sum(1 for l in room["lights"] if l["on"])
        self.on_count = sum(1 for r in self.rooms if r["kind"] != "zone" for l in r["lights"] if l["on"])

    def send_pending(self):
        sent = False
        now = time.time()
        with self.lock:
            items = list(self.pending.items())
        for (kind, rid), body in items:
            interval = GROUP_INTERVAL if kind == "grouped_light" else LIGHT_INTERVAL
            if now - self.last_sent.get(kind, 0) < interval:
                continue
            with self.lock:
                if self.pending.get((kind, rid)) is not body:
                    continue
                del self.pending[(kind, rid)]
            try:
                self.api("PUT", "/clip/v2/resource/%s/%s" % (kind, rid), body)
            except (OSError, PinError, PermissionError, socket.timeout, http.client.HTTPException) as exc:
                with self.lock:
                    self.error = "Command failed (%s)" % clean(exc, 60)
            self.last_sent[kind] = time.time()
            sent = True
        return sent

    def handle(self, cmd):
        name = cmd.get("cmd")
        if name == "open":
            self.panel_open = True
            self.wake.set()
        elif name == "closed":
            self.panel_open = False
        elif name == "refresh":
            self.last_refresh = 0
            self.wake.set()
        elif name == "discover":
            threading.Thread(target=self.discover, daemon=True).start()
        elif name == "pair":
            ip = str(cmd.get("ip", "")).strip()
            if not valid_bridge_ip(ip):
                with self.lock:
                    self.error = "Enter the bridge's local IPv4 address"
                self.emit()
                return
            with self.lock:
                self.pair_ip, self.pair_until, self.phase, self.error = ip, time.time() + PAIR_WINDOW, "pairing", ""
            self.emit()
            self.wake.set()
        elif name == "cancel_pair":
            with self.lock:
                self.pair_ip, self.phase = "", ("connecting" if self.config else "unpaired")
            self.emit()
        elif name == "unpair":
            try:
                os.remove(CONFIG_PATH)
            except OSError:
                pass
            with self.lock:
                self.config, self.rooms, self.light_count, self.on_count = {}, [], 0, 0
                self.phase, self.error, self.pending = "unpaired", "", {}
            self.emit()
            threading.Thread(target=self.discover, daemon=True).start()
        elif name in ("light", "group") and self.config:
            kind = "light" if name == "light" else "grouped_light"
            rid = cmd.get("id")
            if not valid_uuid(rid):
                return
            body = {}
            if isinstance(cmd.get("on"), bool):
                body["on"] = {"on": cmd["on"]}
            if isinstance(cmd.get("bri"), (int, float)):
                body["dimming"] = {"brightness": round(clamp(float(cmd["bri"]), 1.0, 100.0), 1)}
                body["on"] = {"on": True}
            if body:
                self.queue(kind, rid, body)
        elif name == "all_off" and self.config:
            for room in self.snapshot()["rooms"]:
                if room["kind"] == "room" and room["on"] and valid_uuid(room["grouped_light"]):
                    self.queue("grouped_light", room["grouped_light"], {"on": {"on": False}})
                elif room["kind"] == "other":
                    for light in room["lights"]:
                        if light["on"]:
                            self.queue("light", light["id"], {"on": {"on": False}})
        elif name == "scene" and self.config:
            rid = cmd.get("id")
            if valid_uuid(rid):
                with self.lock:
                    self.pending[("scene", rid)] = {"recall": {"action": "active"}}
                    self.refresh_hold_until = 0
                self.wake.set()

    # -- loops
    def worker(self):
        while True:
            if self.pair_ip:
                if time.time() > self.pair_until:
                    with self.lock:
                        self.pair_ip = ""
                        self.phase = "unpaired"
                        self.error = "No button press seen. Press the round button on the bridge, then try again."
                else:
                    self.try_pair()
                self.emit()
                if self.pair_ip:
                    self.wake.wait(2.0)
                    self.wake.clear()
                continue
            if self.send_pending():
                with self.lock:
                    has_more = bool(self.pending)
                if not has_more:
                    # Let the bridge settle, then show the real state.
                    self.last_refresh = time.time() - (POLL_OPEN if self.panel_open else POLL_CLOSED) + 1.2
                continue
            with self.lock:
                has_more = bool(self.pending)
            if has_more:
                time.sleep(0.05)
                continue
            interval = POLL_OPEN if self.panel_open else POLL_CLOSED
            if self.config and time.time() - self.last_refresh >= interval:
                self.refresh()
                self.emit()
            elif not self.config and not self.candidates and not self.discovering and self.panel_open:
                self.discover()
            wait = max(0.05, interval - (time.time() - self.last_refresh)) if self.config else 5.0
            self.wake.wait(wait)
            self.wake.clear()

    def run(self):
        self.emit()
        threading.Thread(target=self.worker, daemon=True).start()
        for line in sys.stdin:
            cmd = parse_command(line)
            if cmd:
                self.handle(cmd)


def main():
    if "--once" in sys.argv:
        hue = Hue()
        hue.refresh()
        print(json.dumps(hue.snapshot(), indent=1))
        return
    try:
        Hue().run()
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
