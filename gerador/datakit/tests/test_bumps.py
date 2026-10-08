"""Lombadas e quebra-molas do OSM (traffic_calming) nos pacotes por estado: lombadas.csv, schema 3 do pacote.

Do OSM entram bump (lombada), hump (lombada longa), table (faixa elevada), cushion (almofada), rumble_strip
(sonorizador) e yes (sem tipo); chicane, choker, island e o resto não fazem a moto pular e ficam de fora. Nó vira o
ponto; via (a faixa elevada desenhada como linha) vira o ponto do meio.
"""
import csv
import json
import os
import tempfile
import unittest

try:
    import osmium
    from osmium.osm.mutable import Node, Way
except ImportError:  # pragma: no cover
    osmium = None  # type: ignore[assignment]

from datakit.common import Bump

BBOX = (-17.0, -50.0, -15.9, -48.9)


def _write_pbf(path):
    with osmium.SimpleWriter(path) as w:
        w.add_node(Node(id=1, location=(-49.0, -16.0), tags={"traffic_calming": "bump"}))
        w.add_node(Node(id=2, location=(-49.1, -16.1), tags={"traffic_calming": "hump"}))
        w.add_node(Node(id=3, location=(-49.2, -16.2), tags={"traffic_calming": "rumble_strip"}))
        w.add_node(Node(id=4, location=(-49.3, -16.3), tags={"traffic_calming": "yes"}))
        w.add_node(Node(id=5, location=(-49.4, -16.4), tags={"traffic_calming": "chicane"}))
        w.add_node(Node(id=6, location=(-49.5, -16.5), tags={"highway": "crossing"}))
        w.add_node(Node(id=7, location=(-48.0, -15.0), tags={"traffic_calming": "cushion"}))
        w.add_node(Node(id=10, location=(-49.6000, -16.6)))
        w.add_node(Node(id=11, location=(-49.6002, -16.6)))
        w.add_node(Node(id=12, location=(-49.6004, -16.6)))
        w.add_way(Way(id=100, nodes=[10, 11, 12], tags={"traffic_calming": "table", "highway": "residential"}))


@unittest.skipIf(osmium is None, "pyosmium não instalado")
class LoadBumps(unittest.TestCase):
    def test_the_kinds_that_make_a_bike_jump_come_in_with_their_kind_and_the_rest_stay_out(self):
        from datakit.sources import osm_pbf
        with tempfile.TemporaryDirectory() as tmp:
            pbf = os.path.join(tmp, "x.osm.pbf")
            _write_pbf(pbf)
            got = osm_pbf.load_bumps(pbf, bbox=BBOX)
        self.assertEqual(sorted((round(b.lat, 4), round(b.lng, 4), b.kind) for b in got), [
            (-16.6, -49.6002, "TABLE"),        # a via: o ponto do meio
            (-16.3, -49.3, "UNSPECIFIED"),
            (-16.2, -49.2, "RUMBLE_STRIP"),
            (-16.1, -49.1, "HUMP"),
            (-16.0, -49.0, "BUMP"),
        ])

    def test_without_a_bbox_every_one_comes_in(self):
        from datakit.sources import osm_pbf
        with tempfile.TemporaryDirectory() as tmp:
            pbf = os.path.join(tmp, "x.osm.pbf")
            _write_pbf(pbf)
            self.assertEqual(len(osm_pbf.load_bumps(pbf)), 6)


class PackAndCatalog(unittest.TestCase):
    def test_the_pack_writes_the_bumps_once_each_and_counts_them(self):
        from datakit.build_pack import build as build_pack
        bumps = [Bump(-16.0, -49.0, "BUMP"), Bump(-16.0, -49.0, "BUMP"), Bump(-16.1, -49.1, "TABLE")]
        with tempfile.TemporaryDirectory() as d:
            m = build_pack("GO", d, [], [], [], sources=[], bumps=bumps)
            with open(os.path.join(d, "GO", "lombadas.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows, [{"lat": "-16.100000", "lng": "-49.100000", "kind": "TABLE"},
                                {"lat": "-16.000000", "lng": "-49.000000", "kind": "BUMP"}])
        self.assertEqual(m["counts"]["bumps"], 2)
        self.assertIn("lombadas.csv", m["files"])
        self.assertEqual(m["schema"], 5)  # 5 desde o radar portátil (test_portable_radar)

    def test_the_catalog_lists_the_bumps_of_a_state_that_has_them(self):
        from datakit import build_catalog
        from datakit.build_pack import build as build_pack
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            build_pack("GO", packs, [], [], [], sources=[], bumps=[Bump(-16.0, -49.0, "BUMP")])
            build_pack("AC", packs, [], [], [], sources=[])
            cat = build_catalog.build(packs, dist)
            published = os.path.exists(os.path.join(dist, "estados", "GO", "lombadas.csv"))
        self.assertEqual((cat["ufs"]["GO"]["bumps"]["file"], cat["ufs"]["GO"]["bumps"]["count"]), ("estados/GO/lombadas.csv", 1))
        self.assertTrue(published)
        self.assertNotIn("bumps", cat["ufs"]["AC"], "a state with none lists none")
        self.assertEqual(json.loads(json.dumps(cat))["schema"], 2, "the catalog only grows a key: old readers ignore it")
