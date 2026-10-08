"""Testes puros do datakit — stdlib unittest (esta máquina não tem pytest).

    python -m unittest discover -s datakit/tests    # da raiz do repo
"""
import unittest

from datakit.common import Camera, CameraKind, Limit, merge_cameras, merge_limits
from datakit.common.geo import haversine_m, parse_maxspeed, rdp, sample_polyline
from datakit.build_pack import tile_of, tile_bbox
from datakit.sources.osm_pbf import (
    _apply_enforcement_relations, _kind, _limit_points, _zone_limit, _struct_kind,
)


class Geo(unittest.TestCase):
    def test_parse_maxspeed(self):
        self.assertEqual(parse_maxspeed("60"), 60)
        self.assertEqual(parse_maxspeed("50 mph"), 80)
        self.assertIsNone(parse_maxspeed("none"))
        self.assertEqual(parse_maxspeed("80;60 @ (22:00-06:00)"), 80)
        self.assertIsNone(parse_maxspeed("BR:urban"))

    def test_rdp_drops_collinear(self):
        line = [(-16.0, -49.0), (-16.0, -48.99), (-16.0, -48.98), (-16.0, -48.97)]
        self.assertEqual(rdp(line, 5.0), [line[0], line[-1]])

    def test_rdp_keeps_corner(self):
        bent = [(-16.0, -49.0), (-16.0, -48.9), (-15.9, -48.9)]
        self.assertEqual(len(rdp(bent, 5.0)), 3)

    def test_sample_polyline_spacing(self):
        pts = sample_polyline([(-16.0, -49.0), (-16.0, -48.9)], 250.0)
        self.assertGreaterEqual(len(pts), 2)
        for a, b in zip(pts, pts[1:]):
            self.assertLess(haversine_m(a, b), 400.0)

    def test_limit_points_keeps_vertices_fills_only_long_gaps(self):
        # dois vértices ~1 km: nenhum ponto extra
        near = _limit_points([(-16.0, -49.0), (-16.0, -48.99)], 1500.0)
        self.assertEqual(len(near), 2)
        # dois vértices ~11 km: enche até <= 1.5 km
        far = _limit_points([(-16.0, -49.0), (-16.0, -48.9)], 1500.0)
        self.assertGreater(len(far), 5)
        for a, b in zip(far, far[1:]):
            self.assertLessEqual(haversine_m(a, b), 1600.0)


class Merge(unittest.TestCase):
    def test_official_wins_tie_when_first(self):
        osm = Camera(-16.0, -49.0, CameraKind.FIXED, None, "OSM", True)
        der = Camera(-16.0, -49.0, CameraKind.FIXED, 60, "DER-GO", True)
        self.assertEqual(merge_cameras([der], [osm])[0].source, "DER-GO")

    def test_section_outranks_fixed(self):
        a = Camera(-16.0, -49.0, CameraKind.FIXED, 80, "OSM", True)
        b = Camera(-16.0, -49.0, CameraKind.SECTION, None, "OSM", True)
        self.assertEqual(merge_cameras([a], [b])[0].kind, CameraKind.SECTION)

    def test_merge_limits_last_group_wins(self):
        a = Limit(-16.0, -49.0, 60, "OSM")
        b = Limit(-16.0, -49.0, 40, "CET-SP")
        self.assertEqual(merge_limits([a], [b])[0].limit_kmh, 40)


class Tiles(unittest.TestCase):
    def test_tile_roundtrip(self):
        t = tile_of(-16.68, -49.25)
        mnla, mnlo, mxla, mxlo = tile_bbox(*t)
        self.assertTrue(mnla <= -16.68 <= mxla and mnlo <= -49.25 <= mxlo)


class Pack(unittest.TestCase):
    """O pacote sai direto das listas da UF: nada gravado fora de packs/<UF>/, mesmo formato."""

    def test_pack_from_memory(self):
        import csv
        import json
        import os
        import tempfile
        from datakit.build_pack import build as build_pack
        from datakit.common import Struct

        cams = [Camera(-16.0, -49.0, CameraKind.FIXED, 60, "DER-GO", True, direction_deg=90),
                Camera(-16.0, -49.0, CameraKind.FIXED, None, "OSM", True),       # mesmo ponto: um só
                Camera(-16.1, -49.1, CameraKind.SECTION, 80, "OSM", True, end_lat=-16.2, end_lng=-49.1)]
        lims = [Limit(-16.0, -49.0, 60, "OSM"), Limit(-16.3, -49.3, 40, "OSM", True, 30)]
        structs = [Struct(-16.0, -49.0, -16.001, -49.0, "BRIDGE")] * 2
        with tempfile.TemporaryDirectory() as d:
            m = build_pack("GO", d, cams, lims, structs, sources=[{"name": "teste"}],
                           built_at="2026-09-30T00:00:00Z")
            self.assertEqual(os.listdir(d), ["GO"])
            self.assertEqual(sorted(os.listdir(os.path.join(d, "GO"))),
                             ["estruturas.csv", "limites.csv", "lombadas.csv", "manifesto.json", "pedagios.csv", "radares.csv"])  # + lombadas (3), pedágios (4)
            with open(os.path.join(d, "GO", "radares.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with open(os.path.join(d, "GO", "manifesto.json"), encoding="utf-8") as f:
                on_disk = json.load(f)
        self.assertEqual(on_disk, m)
        self.assertEqual([(r["source"], r["direction_deg"]) for r in rows], [("OSM", ""), ("DER-GO", "90")])
        self.assertEqual(set(m), {"schema", "uf", "bbox", "built_at", "artifact_built_at", "sources",
                                  "tiles", "counts", "files"})
        self.assertEqual(m["counts"], {"cameras": 2, "cameras_with_limit": 2, "cameras_inactive": 0,
                                       "sections": 1, "red_lights": 0, "limits": 2, "structs": 1, "bumps": 0, "tolls": 0})
        self.assertEqual((m["schema"], m["artifact_built_at"], m["sources"]), (4, "2026-09-30T00:00:00Z",
                                                                             [{"name": "teste"}]))
        want = {tile_of(-16.0, -49.0), tile_of(-16.1, -49.1), tile_of(-16.3, -49.3)}
        self.assertEqual(m["tiles"], [list(t) for t in sorted(want)])


class Relations(unittest.TestCase):
    def test_kind_from_enforcement(self):
        self.assertEqual(_kind({"enforcement": "average_speed"}), CameraKind.SECTION)
        self.assertEqual(_kind({"enforcement": "traffic_signals"}), CameraKind.RED_LIGHT)
        self.assertEqual(_kind({"highway": "speed_camera"}), CameraKind.FIXED)

    def test_zone_limit(self):
        # (típico, lado baixo)
        self.assertEqual(_zone_limit({"zone:maxspeed": "BR:urban"}), (60, 50))
        self.assertEqual(_zone_limit({"maxspeed:type": "BR:rural"}), (100, 80))
        self.assertEqual(_zone_limit({"source:maxspeed": "BR:motorway"}), (110, 110))
        self.assertIsNone(_zone_limit({"zone:maxspeed": "sign"}))
        self.assertIsNone(_zone_limit({}))

    def test_struct_kind(self):
        self.assertEqual(_struct_kind({"bridge": "yes"}), "BRIDGE")
        self.assertEqual(_struct_kind({"bridge": "viaduct"}), "BRIDGE")
        self.assertEqual(_struct_kind({"tunnel": "yes"}), "TUNNEL")
        self.assertIsNone(_struct_kind({"bridge": "no"}))
        self.assertIsNone(_struct_kind({}))

    def test_maxspeed_relation_promotes_from_node_to_section(self):
        cams = [Camera(-16.0, -49.0, CameraKind.FIXED, 80, "OSM", True)]
        node_pt = {1: (-16.0, -49.0), 2: (-16.05, -49.0)}
        _apply_enforcement_relations([("maxspeed", 1, 2, None, 80)], node_pt, {1: 0}, cams, None)
        self.assertEqual(cams[0].kind, CameraKind.SECTION)
        self.assertAlmostEqual(cams[0].end_lat, -16.05)
        self.assertEqual(len(cams), 1)  # promovido, não duplicado

    def test_relation_keeps_direction_and_hint(self):
        cams = [Camera(-16.0, -49.0, CameraKind.FIXED, 80, "OSM", True, direction_deg=180, heading_hint="S")]
        node_pt = {1: (-16.0, -49.0), 2: (-16.05, -49.0)}
        _apply_enforcement_relations([("maxspeed", 1, 2, None, 80)], node_pt, {1: 0}, cams, None)
        self.assertEqual(cams[0].kind, CameraKind.SECTION)
        self.assertEqual((cams[0].direction_deg, cams[0].heading_hint), (180, "S"))

    def test_section_appends_when_from_not_emitted(self):
        cams = []
        node_pt = {10: (-15.0, -48.0), 11: (-15.02, -48.0)}
        _apply_enforcement_relations([("average_speed", 10, 11, None, 60)], node_pt, {}, cams, None)
        self.assertEqual(len(cams), 1)
        self.assertEqual(cams[0].kind, CameraKind.SECTION)
        self.assertEqual(cams[0].limit_kmh, 60)

    def test_traffic_signals_relation_is_red_light(self):
        cams = []
        node_pt = {5: (-20.0, -44.0)}
        _apply_enforcement_relations([("traffic_signals", 5, None, None, None)], node_pt, {}, cams, None)
        self.assertEqual(len(cams), 1)
        self.assertEqual(cams[0].kind, CameraKind.RED_LIGHT)
        self.assertIsNone(cams[0].end_lat)

    def test_from_only_relation_fills_limit_not_kind(self):
        cams = [Camera(-16.0, -49.0, CameraKind.FIXED, None, "OSM", True)]
        _apply_enforcement_relations([("maxspeed", 1, None, None, 60)], {1: (-16.0, -49.0)}, {1: 0}, cams, None)
        self.assertEqual(cams[0].kind, CameraKind.FIXED)  # sem 'to' -> tipo não muda
        self.assertEqual(cams[0].limit_kmh, 60)           # mas herda o limite da relação


if __name__ == "__main__":
    unittest.main()
