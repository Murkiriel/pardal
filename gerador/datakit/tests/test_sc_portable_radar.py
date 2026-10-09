"""SC: locais do radar portátil nas rodovias estaduais (PDF da SIE), postos na via pelos marcos quilométricos do OSM.

SC não tem radar fixo estadual; a SIE publica onde a polícia rodoviária opera o radar portátil: rodovia SC, km, sentido,
município e limite, sem coordenada. Sem malha estadual com km em aberto, o km cai entre os dois marcos do OSM
(`highway=milestone` com `ref` e `distance`) que o cercam, cada um a até MAX_GAP_KM; o trecho entre os dois, pelo caminho
das vias `ref=SC-N`, entra no radar_portatil.csv como os trechos aptos da PRF (road "SC-N").
"""
import os
import tempfile
import unittest
from unittest import mock

try:
    import osmium
    from osmium.osm.mutable import Node, Way
except ImportError:  # pragma: no cover
    osmium = None  # type: ignore[assignment]

TEXT = ("SECRETARIA DE ESTADO DA INFRAESTRUTURA E MOBILIDADE\n"
        "P-01 001001 ROD. SC401 KM 5.550 DECRESCENTE FLORIANÓPOLIS 8105 80 88P-01 001008 ROD. SC 406 KM 14.600 "
        "CRESCENTE FLORIANÓPOLIS 8105 80 88P-01 001010 ROD. SC406 KM 12.550 DESCRESCENTE FLORIANÓPOLIS 8105 80 88\n"
        "P-02 002001 ROD. SC108 KM 105.150 CRESCENTE GASPAR 8117 80 88")


class Parse(unittest.TestCase):
    def test_each_point_with_its_road_km_and_direction(self):
        from datakit.sources import sc_portable_radar as sc
        P = sc.Point
        self.assertEqual([P(401, 5.55, False), P(406, 14.6, True), P(406, 12.55, False), P(108, 105.15, True)],
                         sc.parse(TEXT))

    def test_the_list_says_from_when_it_is_valid(self):
        from datakit.sources import sc_portable_radar as sc
        # the PDF's own heading, with no "de"
        self.assertEqual("2023-05-16", sc.valid_from("RADARES PORTÁTEIS EM RODOVIAS ESTADUAIS DE SC(COM VALIDADE A PARTIR 16/05/2023)"))
        self.assertEqual("2023-05-16", sc.valid_from("válida a partir de 16-05-23"))
        self.assertEqual("", sc.valid_from(TEXT))


def _write(path):
    # SC-401 para leste, km 0 em lng -48.50, um nó a cada ~1 km (0.0093°), com uma volta para o norte nos km 7 a 9;
    # marcos nos km 5 e 10 (um com a outra via no ref); um atalho reto de outra rodovia (SC-999) entre o km 5 e o 10,
    # mais curto, que o caminho da SC-401 não pode usar
    with osmium.SimpleWriter(path) as w:
        for i in range(13):
            w.add_node(Node(id=i + 1, location=(-48.50 + 0.0093 * i, -27.49 if 7 <= i <= 9 else -27.50)))
        w.add_node(Node(id=50, location=(-48.50 + 0.0093 * 7.5, -27.50)))
        w.add_way(Way(id=100, nodes=list(range(1, 8)), tags={"highway": "primary", "ref": "SC-401"}))
        w.add_way(Way(id=101, nodes=list(range(7, 14)), tags={"highway": "primary", "ref": "SC-401;SC-405"}))
        w.add_way(Way(id=102, nodes=[6, 50, 11], tags={"highway": "secondary", "ref": "SC-999"}))
        w.add_node(Node(id=201, location=(-48.50 + 0.0093 * 5, -27.5002), tags={"highway": "milestone", "ref": "SC-401", "distance": "5"}))
        w.add_node(Node(id=202, location=(-48.50 + 0.0093 * 10, -27.5002), tags={"highway": "milestone", "ref": "SC-401", "distance": "10,0"}))
        w.add_node(Node(id=203, location=(-48.50 + 0.0093 * 2, -27.5002), tags={"highway": "milestone", "ref": "SC-401"}))
        w.add_node(Node(id=204, location=(-48.0, -26.0), tags={"highway": "milestone", "ref": "BR-101", "distance": "3"}))


@unittest.skipIf(osmium is None, "pyosmium não instalado")
class LoadOsm(unittest.TestCase):
    def test_the_marks_and_the_ways_of_each_sc_road(self):
        from datakit.sources import sc_portable_radar as sc
        with tempfile.TemporaryDirectory() as tmp:
            pbf = os.path.join(tmp, "x.osm.pbf")
            _write(pbf)
            marks, ways = sc.load_osm(pbf)
        self.assertIn(401, marks)
        self.assertEqual([5.0, 10.0], [k for k, _, _ in marks[401]])   # the one with no distance and the BR one stay out
        self.assertEqual({401, 405, 999}, set(ways))
        self.assertEqual(2, len(ways[401]))


class Place(unittest.TestCase):
    def setUp(self):
        from datakit.sources import sc_portable_radar as sc
        self.sc = sc
        if osmium is None:
            self.skipTest("pyosmium não instalado")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pbf = os.path.join(self.tmp.name, "x.osm.pbf")
        _write(pbf)
        self.marks, self.ways = sc.load_osm(pbf)

    def test_a_point_takes_the_road_between_the_two_marks_around_it_along_the_way(self):
        # 7,2 and 9,9 between the marks of km 5 and 10; 5,0 right on the first one: the same stretch, once
        zones, unplaced = self.sc.place([self.sc.Point(401, 7.2, True), self.sc.Point(401, 9.9, False), self.sc.Point(401, 5.0, True)], self.marks,
                                        self.ways, "2023-05-16")
        self.assertEqual(0, unplaced)
        self.assertEqual([("SC-401", 5.0, 10.0, "2023-05-16")], [(z.road, z.km_from, z.km_to, z.valid_from) for z in zones])
        lngs = [round(p[1], 4) for p in zones[0].points]
        self.assertEqual(round(-48.50 + 0.0093 * 5, 4), lngs[0])
        self.assertEqual(round(-48.50 + 0.0093 * 10, 4), lngs[-1])
        self.assertGreaterEqual(sum(1 for p in zones[0].points if abs(p[0] + 27.49) < 1e-6), 2, "along SC-401's bend")
        self.assertNotIn(round(-48.50 + 0.0093 * 7.5, 4), lngs, "not the SC-999 shortcut")

    def test_a_point_with_no_mark_on_one_side_or_too_far_is_left_out(self):
        P = self.sc.Point
        zones, unplaced = self.sc.place([P(401, 3.0, True), P(401, 11.0, True), P(161, 5.0, True)], self.marks,
                                        self.ways, "x")
        self.assertEqual(([], 3), (zones, unplaced))
        self.assertEqual(5.0, self.sc.MAX_GAP_KM)

    def test_the_path_between_two_points_follows_the_road(self):
        self.assertIn(401, self.ways)
        path = self.sc.road_path(self.ways[401], (-27.50, -48.50 + 0.0093 * 2), (-27.50, -48.50 + 0.0093 * 4))
        self.assertEqual(3, len(path))


class Context(unittest.TestCase):
    def test_the_sc_points_are_read_once_and_a_failure_is_nothing_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import sc_portable_radar as sc
        calls = []

        def down(pbf):
            calls.append(pbf)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(sc, "zones", down):
                ctx = BuildContext("raw")
                got = (ctx.sc_portable_radar("x.pbf"), ctx.sc_portable_radar("x.pbf"))
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual(((([], 0), ([], 0)), 1), (got, len(calls)))
        self.assertEqual(["SIE-SC (radar portátil): ConnectionError: fora do ar"], new)


if __name__ == "__main__":
    unittest.main()
