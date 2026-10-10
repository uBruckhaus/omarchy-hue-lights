import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hue", os.path.join(HERE, "..", "scripts", "hue-lights.py"))
hue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hue)

L1 = "11111111-1111-1111-1111-111111111111"
L2 = "22222222-2222-2222-2222-222222222222"
L3 = "33333333-3333-3333-3333-333333333333"
D1 = "aaaaaaaa-1111-1111-1111-111111111111"
D2 = "aaaaaaaa-2222-2222-2222-222222222222"
D3 = "aaaaaaaa-3333-3333-3333-333333333333"
GL = "bbbbbbbb-1111-1111-1111-111111111111"
ROOM = "cccccccc-1111-1111-1111-111111111111"
SCENE = "dddddddd-1111-1111-1111-111111111111"

RESOURCES = [
    {"id": L1, "type": "light", "owner": {"rid": D1}, "metadata": {"name": "Ceiling"},
     "on": {"on": True}, "dimming": {"brightness": 60.0},
     "color_temperature": {"mirek": 366, "mirek_valid": True}, "color": {"xy": {"x": 0.3, "y": 0.3}}},
    {"id": L2, "type": "light", "owner": {"rid": D2}, "metadata": {"name": "Arc"},
     "on": {"on": False}, "dimming": {"brightness": 20.0}},
    {"id": L3, "type": "light", "owner": {"rid": D3}, "metadata": {"name": "Plug"}, "on": {"on": True}},
    {"id": D1, "type": "device", "services": [{"rid": L1, "rtype": "light"}]},
    {"id": D2, "type": "device", "services": [{"rid": L2, "rtype": "light"}]},
    {"id": D3, "type": "device", "services": [{"rid": L3, "rtype": "light"}]},
    {"id": "z", "type": "zigbee_connectivity", "owner": {"rid": D2}, "status": "connectivity_issue"},
    {"id": GL, "type": "grouped_light", "on": {"on": True}, "dimming": {"brightness": 55.0}},
    {"id": ROOM, "type": "room", "metadata": {"name": "Living room"},
     "children": [{"rid": D1, "rtype": "device"}, {"rid": D2, "rtype": "device"}],
     "services": [{"rid": GL, "rtype": "grouped_light"}]},
    {"id": SCENE, "type": "scene", "metadata": {"name": "Relax"}, "group": {"rid": ROOM, "rtype": "room"},
     "status": {"active": "static"}},
]


class ModelTests(unittest.TestCase):
    def test_rooms_lights_scenes(self):
        rooms, total, on = hue.build_model(RESOURCES)
        self.assertEqual((total, on), (3, 2))
        living = rooms[0]
        self.assertEqual(living["name"], "Living room")
        self.assertEqual(living["grouped_light"], GL)
        self.assertEqual([l["name"] for l in living["lights"]], ["Arc", "Ceiling"])
        self.assertEqual(living["bri"], 55.0)
        self.assertEqual(living["on_count"], 1)
        self.assertFalse(living["lights"][0]["reachable"])
        self.assertEqual(living["scenes"], [{"id": SCENE, "name": "Relax", "active": True}])

    def test_unassigned_lights_go_to_other(self):
        rooms, _, _ = hue.build_model(RESOURCES)
        other = rooms[-1]
        self.assertEqual(other["kind"], "other")
        self.assertEqual([l["name"] for l in other["lights"]], ["Plug"])
        self.assertFalse(other["lights"][0]["dimmable"])

    def test_colors(self):
        self.assertEqual(hue.xy_to_hex(0.7, 0.29)[:3], "#ff")
        self.assertTrue(hue.mirek_to_hex(500).startswith("#ff"))
        self.assertEqual(hue.xy_to_hex("bad", None), "#ffd27a")


class ValidationTests(unittest.TestCase):
    def test_bridge_ip(self):
        self.assertTrue(hue.valid_bridge_ip("192.168.178.106"))
        for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "bridge.local", "192.168.1.1/24", ""):
            self.assertFalse(hue.valid_bridge_ip(bad), bad)

    def test_uuid(self):
        self.assertTrue(hue.valid_uuid(L1))
        self.assertFalse(hue.valid_uuid("../config"))
        self.assertFalse(hue.valid_uuid(GL.upper()))

    def test_parse_command(self):
        self.assertEqual(hue.parse_command('{"cmd":"open"}'), {"cmd": "open"})
        self.assertIsNone(hue.parse_command("[1]"))
        self.assertIsNone(hue.parse_command("nope"))

    def test_parse_avahi(self):
        lines = [
            '+;wlp15s0;IPv4;Hue\\032Bridge\\032-\\032C9B46D;_hue._tcp;local',
            '=;wlp15s0;IPv6;Hue\\032Bridge;_hue._tcp;local;x.local;fe80::1;443;"bridgeid=c42996fffec9b46d"',
            '=;wlp15s0;IPv4;Hue\\032Bridge\\032-\\032C9B46D;_hue._tcp;local;C42996C9B46D.local;192.168.178.106;443;"bridgeid=c42996fffec9b46d" "modelid=BSB003"',
            '=;wlp15s0;IPv4;Evil;_hue._tcp;local;e.local;1.2.3.4;443;"bridgeid=c42996fffec9b46d"',
        ]
        self.assertEqual(hue.parse_avahi_hue(lines),
                         [{"ip": "192.168.178.106", "bridge_id": "C42996FFFEC9B46D", "name": "Hue Bridge - C9B46D"}])


class OptimisticTests(unittest.TestCase):
    def test_group_brightness_turns_room_on(self):
        h = hue.Hue.__new__(hue.Hue)
        h.rooms, _, _ = hue.build_model(RESOURCES)
        h.apply_optimistic("grouped_light", GL, {"dimming": {"brightness": 80.0}, "on": {"on": True}})
        living = h.rooms[0]
        self.assertTrue(all(l["on"] for l in living["lights"]))
        self.assertEqual(living["bri"], 80.0)
        self.assertEqual(h.on_count, 3)

    def test_light_off(self):
        h = hue.Hue.__new__(hue.Hue)
        h.rooms, _, _ = hue.build_model(RESOURCES)
        h.apply_optimistic("light", L1, {"on": {"on": False}})
        self.assertFalse(h.rooms[0]["on"])
        self.assertEqual(h.on_count, 1)


if __name__ == "__main__":
    unittest.main()
