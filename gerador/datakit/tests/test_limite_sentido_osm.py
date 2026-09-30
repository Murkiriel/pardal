"""Limite por sentido no OSM (maxspeed:forward / maxspeed:backward): pontos com direction_deg pelo
rumo do way — forward = sentido do desenho, backward = o contrário."""
import collections
import os
import tempfile
import unittest

try:
    import osmium
    from osmium.osm.mutable import Node, Way
except ImportError:  # pragma: no cover
    osmium = None  # type: ignore[assignment]


def _load(ways):
    """ways: [(tags, [(lat, lng), ...])] desenhados na ordem dada -> limites do osm_pbf.load."""
    from datakit.sources import osm_pbf
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.osm.pbf")
        nid = 1
        way_nodes = []
        with osmium.SimpleWriter(path) as w:
            for _, pts in ways:
                ids = []
                for lat, lng in pts:
                    w.add_node(Node(id=nid, location=(lng, lat)))
                    ids.append(nid)
                    nid += 1
                way_nodes.append(ids)
            for i, ((tags, _), ids) in enumerate(zip(ways, way_nodes)):
                w.add_way(Way(id=100 + i, nodes=ids, tags=tags))
        _, lims, _ = osm_pbf.load(path)
    return lims


def _by_dir(lims):
    return collections.Counter((x.limit_kmh, x.direction_deg, x.estimated) for x in lims)


EAST = [(-16.0, -49.0), (-16.0, -48.99)]           # desenhado de oeste para leste: rumo 90°


@unittest.skipIf(osmium is None, "pyosmium não instalado")
class LimitePorSentido(unittest.TestCase):
    def test_forward_and_backward_without_maxspeed(self):
        lims = _load([({"highway": "residential", "maxspeed:forward": "60", "maxspeed:backward": "40"}, EAST)])
        got = {(x.limit_kmh, x.direction_deg, x.estimated) for x in lims}
        self.assertEqual(got, {(60, 90, False), (40, 270, False)})
        self.assertTrue(all(x.source == "OSM" for x in lims))

    def test_maxspeed_fills_the_side_without_its_own_tag(self):
        lims = _load([({"highway": "residential", "maxspeed": "60", "maxspeed:backward": "40"}, EAST)])
        self.assertEqual({(x.limit_kmh, x.direction_deg) for x in lims}, {(60, 90), (40, 270)})

    def test_same_value_both_ways_is_a_plain_signed_limit(self):
        lims = _load([({"highway": "residential", "maxspeed:forward": "50", "maxspeed:backward": "50"}, EAST)])
        self.assertEqual({(x.limit_kmh, x.direction_deg, x.estimated) for x in lims}, {(50, None, False)})

    def test_one_side_only_keeps_the_estimate_for_the_other(self):
        lims = _load([({"highway": "tertiary", "maxspeed:forward": "60"}, EAST)])
        got = _by_dir(lims)
        self.assertTrue(any(k[:3] == (60, 90, False) for k in got))       # o sentido sinalizado
        self.assertTrue(any(k[1] is None and k[2] for k in got))          # a estimativa de antes, sem sentido

    def test_plain_maxspeed_is_unchanged(self):
        lims = _load([({"highway": "residential", "maxspeed": "40"}, EAST)])
        self.assertEqual({(x.limit_kmh, x.direction_deg, x.estimated) for x in lims}, {(40, None, False)})


if __name__ == "__main__":
    unittest.main()
