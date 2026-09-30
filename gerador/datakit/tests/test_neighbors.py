"""Busca de vizinhos em metros em qualquer latitude (o grau de longitude encolhe com a latitude:
a 30° S mede ~96 km, não 111 km, e uma grade de células quadradas em graus deixava de achar pares
a leste-oeste perto do raio)."""
import math
import random
import unittest

from datakit.common import Camera, Limit
from datakit.common.geo import haversine_m
from datakit.common.lrs import MeasuredLine
from datakit.common.model import absorb_osm, collapse_osm, deactivate_near, merge_cross_agency, override_limits
from datakit.common.spatial import Grid, lat_span_deg, lng_span_deg
from datakit.sources import osm_pbf

LATS = (0.0, -23.0, -30.0, -33.0)


def _east(lat: float, lng: float, m: float) -> float:
    """Longitude m metros a leste (m < 0: a oeste) na mesma latitude."""
    return lng + math.degrees(m / (6_371_000.0 * math.cos(math.radians(lat))))


def _cell_end(lng: float, radius_m: float, k: int) -> float:
    """Longitude encostada no fim de uma célula da grade antiga (radius_m / 111.000 graus)."""
    cell = radius_m / 111_000.0
    return (math.floor(lng / cell) + k + 0.999) * cell


class GridIndex(unittest.TestCase):
    def test_same_answer_as_brute_force(self):
        rnd = random.Random(7)
        for lat0 in LATS:
            pts = [(lat0 + rnd.uniform(-0.003, 0.003), -49.0 + rnd.uniform(-0.003, 0.003)) for _ in range(400)]
            g = Grid(30.0, [(la, lo, i) for i, (la, lo) in enumerate(pts)])
            for q in pts[:100]:
                want = {i for i, p in enumerate(pts) if haversine_m(q, p) <= 30.0}
                self.assertEqual({i for _, i in g.within(*q)}, want, lat0)

    def test_spans_cover_the_radius(self):
        for lat in LATS:
            self.assertGreaterEqual(haversine_m((lat, 0.0), (lat + lat_span_deg(30.0), 0.0)), 30.0)
            self.assertGreaterEqual(haversine_m((lat, 0.0), (lat, lng_span_deg(30.0, lat))), 30.0)


class EastWestPairs(unittest.TestCase):
    """Pares a leste-oeste perto do raio, com o ponto no fim de uma célula da grade antiga."""

    def test_absorb_osm(self):
        for lat in LATS:
            for k in range(50):
                lng = _cell_end(-49.0, 30.0, k)
                off = Camera(lat, lng, source="DNIT")
                osm = Camera(lat, _east(lat, lng, 29.0), source="OSM")
                _, rest, n = absorb_osm([off], [osm])
                self.assertEqual(n, 1, (lat, k))

    def test_merge_cross_agency(self):
        for lat in LATS:
            for k in range(50):
                lng = _cell_end(-49.0, 30.0, k)
                cams = [Camera(lat, lng, source="DNIT"), Camera(lat, _east(lat, lng, 29.0), source="DER-SP")]
                self.assertEqual(merge_cross_agency(cams)[1], 1, (lat, k))

    def test_collapse_osm(self):
        for lat in LATS:
            for k in range(50):
                lng = _cell_end(-49.0, 8.0, k)
                cams = [Camera(lat, lng, source="OSM"), Camera(lat, _east(lat, lng, 7.8), source="OSM")]
                self.assertEqual(collapse_osm(cams)[1], 1, (lat, k))

    def test_override_limits(self):
        for lat in LATS:
            for k in range(50):
                lng = _cell_end(-49.0, 50.0, k)
                official = [Limit(lat, lng, 60, "ANTT")]
                osm = [Limit(lat, _east(lat, lng, 49.0), 80, "OSM")]
                self.assertEqual(len(override_limits(official, osm)), 1, (lat, k))

    def test_deactivate_near(self):
        for lat in LATS:
            for k in range(50):
                lng = _cell_end(-49.0, 20.0, k)
                cam = Camera(lat, _east(lat, lng, 19.5), source="OSM")
                _, n = deactivate_near([cam], [(lat, lng)], [])
                self.assertEqual(n, 1, (lat, k))
                # ...e um oficial ativo a 29 m a oeste do radar o mantém ativo
                alive = Camera(lat, _east(lat, cam.lng, -29.0), source="CET-SP")
                _, n = deactivate_near([cam], [(lat, lng)], [alive])
                self.assertEqual(n, 0, (lat, k))

    def test_osm_probe(self):
        """Radar-sonda a 39 m a leste do fim de um trecho de via (a caixa do trecho + a margem
        tem que cobrir os 40 m também em longitude)."""
        for lat in LATS:
            for k in range(50):
                lng = (math.floor(-49.0 / 0.002) + k + 1.0005) * 0.002   # início de célula da sonda
                end = (lat, _east(lat, lng, -39.0))
                cam = Camera(lat, lng, heading_hint="*")
                found = {}
                osm_pbf._probe_way(osm_pbf._probe_grid([cam]), [(lat - 0.001, end[1] - 0.001), end], True, found)
                self.assertEqual(len(found), 1, (lat, k))

    def test_measured_line_projection(self):
        for lat in LATS:
            line = MeasuredLine([(lat - 0.01, -49.0), (lat + 0.01, -49.0)], [0.0, 2.2])
            p = (lat, _east(lat, -49.0, 39.0))
            self.assertTrue(line.near_bbox(p, 40.0), lat)
            self.assertLess(line.project(p, 40.0)[1], 40.0, lat)


if __name__ == "__main__":
    unittest.main()
