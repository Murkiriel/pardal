"""Cada ponto em exatamente uma UF: a que contém; senão a mais perto a até 5 km (pontes,
litoral, ilhas — a malha simplificada do IBGE corta a linha d'água); senão nenhuma."""
import os
import unittest

from datakit.common.ufassign import NEAR_M, UfAssigner

try:
    from shapely.geometry import box
except ImportError:  # pragma: no cover
    box = None

_MESH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw", "ibge_malha_uf.json")

# Os 12 radares da ANTT na Ponte Rio-Niterói (BR-101): nenhum cai dentro do polígono do RJ.
RIO_NITEROI_BRIDGE = [
    (-22.873872, -43.125076), (-22.873872, -43.125076), (-22.871861, -43.147095),
    (-22.870416, -43.163312), (-22.868429, -43.185028), (-22.868429, -43.185028),
    (-22.873761, -43.125056), (-22.873891, -43.125023), (-22.871847, -43.14727),
    (-22.868433, -43.185125), (-22.868433, -43.185125), (-22.870419, -43.163498),
]


@unittest.skipIf(box is None, "shapely não instalado")
class Synthetic(unittest.TestCase):
    def setUp(self):
        # A e B separados por 0,02° de "água" (~2,2 km); C encosta em A (divisa seca).
        self.a = UfAssigner({"AA": box(0.0, 0.0, 1.0, 1.0), "BB": box(1.02, 0.0, 2.0, 1.0),
                             "CC": box(0.0, 1.0, 1.0, 2.0)})

    def test_inside(self):
        self.assertEqual(self.a.assign([0.5, 0.5, 1.5], [0.5, 1.5, 0.5]), ["AA", "BB", "CC"])

    def test_water_between_two_states_goes_to_the_nearest(self):
        self.assertEqual(self.a.assign([0.5, 0.5], [1.005, 1.015]), ["AA", "BB"])

    def test_far_at_sea_is_nobody(self):
        self.assertEqual(self.a.assign([0.5, 0.5], [-0.09, -0.04]), [None, "AA"])  # ~10 km, ~4,4 km
        self.assertEqual(NEAR_M, 5000.0)

    def test_on_the_border_is_exactly_one(self):
        (uf,) = self.a.assign([1.0], [0.5])
        self.assertIn(uf, ("AA", "CC"))

    def test_scalar_matches_batch(self):
        pts = [(0.5, 1.005), (0.5, -0.09), (1.5, 0.5)]
        self.assertEqual([self.a.uf_of(la, lo) for la, lo in pts],
                         self.a.assign([p[0] for p in pts], [p[1] for p in pts]))


@unittest.skipIf(box is None, "shapely não instalado")
class BuildSelection(unittest.TestCase):
    """O build e o pacote usam a mesma decisão: o radar da ponte fica no RJ e em mais nenhuma."""

    def setUp(self):
        # "RJ" termina a oeste da baía; "ES" longe. A ponte fica ~3 km a leste do "RJ".
        self.assigner = UfAssigner({"RJ": box(-43.30, -23.00, -43.18, -22.80),
                                    "ES": box(-41.00, -21.00, -40.00, -20.00)})
        from datakit.common import Camera, Struct
        self.bridge = Camera(-22.87, -43.15, source="ANTT")
        self.inland = Camera(-22.90, -43.25, source="OSM")
        self.sea = Camera(-22.87, -43.00, source="OSM")          # ~18 km da costa
        self.struct = Struct(-22.87, -43.20, -22.87, -43.10, "BRIDGE")

    def test_split_points_and_structs(self):
        from datakit.build import _Split
        s = _Split(self.assigner)
        cams = [self.bridge, self.inland, self.sea]
        self.assertEqual(s.points("RJ", "c", cams), [self.bridge, self.inland])
        self.assertEqual(s.points("ES", "c", cams), [])
        self.assertEqual(s.structs("RJ", "s", [self.struct]), [self.struct])

    def test_pack_keeps_the_bridge(self):
        import tempfile
        from datakit.build import _Split
        from datakit.build_pack import build as build_pack
        s = _Split(self.assigner)
        with tempfile.TemporaryDirectory() as d:
            m = build_pack("RJ", d, s.points("RJ", "c", [self.bridge, self.inland, self.sea]), [],
                           s.structs("RJ", "s", [self.struct]), sources=[])
        self.assertEqual((m["counts"]["cameras"], m["counts"]["structs"]), (2, 1))


@unittest.skipIf(box is None, "shapely não instalado")
class FarFromHome(unittest.TestCase):
    """Radar de órgão estadual ou municipal noutra UF: colado na UF do órgão fica; longe dela é
    coordenada errada na fonte (01/10/2026: DER-SP a 10, 16 e 380 km de SP) e sai."""

    def setUp(self):
        # "SP" e "MG" encostados (divisa em lng 1.0); "SC" longe, ao sul.
        self.assigner = UfAssigner({"SP": box(0.0, 0.0, 1.0, 1.0), "MG": box(1.0, 0.0, 2.0, 1.0),
                                    "SC": box(0.0, -5.0, 1.0, -4.0)})

    def test_distance_to_a_state(self):
        self.assertEqual(self.assigner.distance_m("SP", 0.5, 0.5), 0.0)
        self.assertAlmostEqual(self.assigner.distance_m("SP", 0.5, 1.02), 2224, delta=30)   # ~2,2 km a leste

    def test_keeps_what_is_next_to_the_agency_state_and_drops_the_rest(self):
        from datakit.build import _drop_far_from_home
        from datakit.common import Camera
        near = Camera(0.5, 1.02, source="DER-SP")        # em "MG", a ~2,2 km de "SP"
        far = Camera(0.5, 1.10, source="DER-SP")         # em "MG", a ~11 km de "SP"
        other = [Camera(0.5, 1.5, source="DNIT"), Camera(0.5, 1.5, source="OSM"),
                 Camera(0.5, 1.5, source="BHTRANS")]     # federal, OSM e o órgão da própria UF
        self.assertEqual(_drop_far_from_home("MG", [near, far, *other], self.assigner), [near, *other])
        self.assertEqual(_drop_far_from_home("SC", [Camera(-4.5, 0.5, source="DER-SP")], self.assigner), [])

    def test_without_polygons_nothing_is_dropped(self):
        from datakit.build import _drop_far_from_home
        from datakit.common import Camera
        cams = [Camera(0.5, 1.10, source="DER-SP")]
        self.assertEqual(_drop_far_from_home("MG", cams, None), cams)

    def test_agency_whose_state_has_no_polygon_is_left_alone(self):
        from datakit.build import _drop_far_from_home
        from datakit.common import Camera
        cams = [Camera(0.5, 1.5, source="DER-GO")]       # não há "GO" nesta malha
        self.assertEqual(_drop_far_from_home("MG", cams, self.assigner), cams)


@unittest.skipIf(box is None, "shapely não instalado")
class IbgeMesh(unittest.TestCase):
    """A malha vem da API de malhas do IBGE (v3), que identifica a UF pelo código (codarea)."""

    def test_reads_the_official_mesh_by_ibge_code(self):
        import json
        import tempfile
        from unittest import mock
        from datakit import failures
        from datakit.common import ufpoly

        def square(x0, y0):
            return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + 1, y0], [x0 + 1, y0 + 1], [x0, y0 + 1], [x0, y0]]]}

        gj = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"codarea": "33"}, "geometry": square(-44, -23)},
            {"type": "Feature", "properties": {"codarea": "35"}, "geometry": square(-48, -24)}]}
        before = list(failures._FAILURES)
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch("datakit.sources._http.get_bytes", side_effect=ConnectionError("sem rede no teste")):
            with open(os.path.join(raw, ufpoly.MESH_FILE), "w", encoding="utf-8") as f:
                json.dump(gj, f)
            try:
                polys = ufpoly.polygons(raw)
            finally:
                failures._FAILURES[:] = before
        self.assertEqual(sorted(polys), ["RJ", "SP"])
        self.assertTrue(polys["RJ"].contains(__import__("shapely.geometry").geometry.Point(-43.5, -22.5)))
        self.assertIn("servicodados.ibge.gov.br/api/v3/malhas", ufpoly.MESH_URL)


@unittest.skipUnless(box is not None and os.path.exists(_MESH), "sem a malha do IBGE em data/raw")
class RioNiteroiBridge(unittest.TestCase):
    def test_all_twelve_are_rj(self):
        from datakit.common.ufpoly import polygons
        a = UfAssigner(polygons(os.path.dirname(_MESH)))
        self.assertEqual(a.assign([p[0] for p in RIO_NITEROI_BRIDGE], [p[1] for p in RIO_NITEROI_BRIDGE]),
                         ["RJ"] * 12)


if __name__ == "__main__":
    unittest.main()
