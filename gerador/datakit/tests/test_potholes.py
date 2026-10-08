"""DNIT, Condições do Pavimento (ICM): os km de rodovia federal com panela (buraco) ruim ou péssima, juntados em trechos,
postos na rodovia pelo SNV e no pacote do estado (buracos.csv, schema 6), com o mês da avaliação.
"""
import csv
import os
import tempfile
import unittest
from unittest import mock

from datakit.common.lrs import MeasuredLine, SnvRoutes

HEAD = ("id_malha;UF;Contrato;Ano;Mes;Panela;Remendo;Trincamento;Rocada;Drenagem;Sinalizacao_Vertical;"
        "Sinalizacao_Horizontal;Rodovia;km;Sentido;Km_Inicial;Km_Final;Num_Faixas;Superfície;Data Aval.;IP;IC;ICM;ICM_Unificado")


def _row(uf, br, km0, km1, sentido, panela):
    return f'1;{uf};"x";2026;8;{panela};Bom;Bom;Bom;Bom;Bom;Bom;BR-{br};{min(km0, km1)};{sentido};{km0};{km1};1;Pavimentada;2026-08-13;0;0;0;0'


CSV = "\n".join([HEAD,
                 _row("GO", "060", 10, 11, "C", "Ruim"),
                 _row("GO", "060", 11, 12, "C", "Péssimo"),
                 _row("GO", "060", 12, 13, "C", "Bom"),
                 _row("GO", "060", 14, 13, "D", "Pessimo"),
                 _row("GO", "153", 5, 6, "C", "Regular"),
                 "lixo;sem;campos"])


def _line(km0, km1, lng0, lng1, lat=-16.0, n=6):
    pts = [(lat, lng0 + (lng1 - lng0) * i / (n - 1)) for i in range(n)]
    return MeasuredLine(pts, [km0 + (km1 - km0) * i / (n - 1) for i in range(n)])


class Parse(unittest.TestCase):
    def test_only_the_km_with_bad_or_very_bad_potholes_with_the_month_and_the_direction(self):
        from datakit.common import RoughKm
        from datakit.sources import dnit_icm
        month, rows = dnit_icm.parse(CSV)
        self.assertEqual("2026-08", month)
        self.assertEqual([RoughKm("GO", 60, 10.0, 11.0, False, "BAD"), RoughKm("GO", 60, 11.0, 12.0, False, "VERY_BAD"),
                          RoughKm("GO", 60, 13.0, 14.0, True, "VERY_BAD")], rows)


class Merge(unittest.TestCase):
    def test_km_in_a_row_on_the_same_road_and_direction_are_one_stretch_with_the_worst_level(self):
        from datakit import potholes
        from datakit.common import RoughKm
        rows = [RoughKm("GO", 60, 11.0, 12.0, False, "VERY_BAD"), RoughKm("GO", 60, 10.0, 11.0, False, "BAD"),
                RoughKm("GO", 60, 14.0, 15.0, False, "BAD"),          # a 2 km gap: another stretch
                RoughKm("GO", 60, 10.0, 11.0, True, "BAD")]           # the other direction: another one
        got = potholes.merge(rows)
        self.assertEqual([("GO", 60, 10.0, 12.0, False, "VERY_BAD"), ("GO", 60, 14.0, 15.0, False, "BAD"),
                          ("GO", 60, 10.0, 11.0, True, "BAD")],
                         [(r.uf, r.br, r.km_from, r.km_to, r.decreasing, r.level) for r in got])


class Place(unittest.TestCase):
    def test_each_stretch_on_its_road_drawn_the_way_traffic_goes_and_the_ones_with_no_road_counted(self):
        from datakit import potholes
        from datakit.common import RoughKm
        routes = SnvRoutes({(60, "GO"): [_line(0.0, 50.0, -49.5, -49.0)]})
        rows = [RoughKm("GO", 60, 10.0, 20.0, False, "BAD"), RoughKm("GO", 60, 10.0, 20.0, True, "VERY_BAD"),
                RoughKm("GO", 153, 0.0, 1.0, False, "BAD"), RoughKm("DF", 60, 0.0, 1.0, False, "BAD")]
        zones, unplaced = potholes.place("GO", "2026-08", rows, routes)
        self.assertEqual(1, unplaced)
        self.assertEqual([("BR-060", 10.0, 20.0, "INCREASING", "BAD", "2026-08"), ("BR-060", 10.0, 20.0, "DECREASING", "VERY_BAD", "2026-08")],
                         [(z.road, z.km_from, z.km_to, z.direction, z.level, z.month) for z in zones])
        self.assertAlmostEqual(-49.4, zones[0].points[0][1], places=6)
        self.assertAlmostEqual(-49.3, zones[1].points[0][1], places=6)   # decreasing: starts at km 20
        self.assertEqual(([], 1), potholes.place("GO", "2026-08", rows[:1], None))

    def test_the_row_for_the_pack(self):
        from datakit.common import PotholeZone
        z = PotholeZone("BR-060", 10.0, 12.0, "DECREASING", "VERY_BAD", "2026-08", ((-16.0, -49.3), (-16.0, -49.4)))
        self.assertEqual(("road", "km_from", "km_to", "direction", "level", "month", "wkt"), PotholeZone.HEADER)
        self.assertEqual(["BR-060", "10.0", "12.0", "DECREASING", "VERY_BAD", "2026-08",
                          "LINESTRING(-49.30000 -16.00000, -49.40000 -16.00000)"], z.row())


class PackAndCatalog(unittest.TestCase):
    def test_the_pack_writes_the_stretches_counts_them_and_the_catalog_lists_them(self):
        from datakit import build_catalog
        from datakit.build_pack import build as build_pack
        from datakit.common import PotholeZone
        zones = [PotholeZone("BR-060", 10.0, 12.0, "INCREASING", "BAD", "2026-08", ((-16.0, -49.4), (-16.0, -49.3)))]
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            m = build_pack("GO", packs, [], [], [], sources=[], potholes=zones)
            build_pack("AC", packs, [], [], [], sources=[])
            with open(os.path.join(packs, "GO", "buracos.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            cat = build_catalog.build(packs, dist)
        self.assertEqual(["BAD"], [r["level"] for r in rows])
        self.assertEqual((m["schema"], m["counts"]["potholes"]), (6, 1))
        self.assertIn("buracos.csv", m["files"])
        self.assertEqual((cat["ufs"]["GO"]["potholes"]["file"], cat["ufs"]["GO"]["potholes"]["count"]), ("estados/GO/buracos.csv", 1))
        self.assertNotIn("potholes", cat["ufs"]["AC"])


class Context(unittest.TestCase):
    def test_the_survey_is_fetched_once_and_a_failure_is_an_empty_list_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import dnit_icm
        calls = []

        def down():
            calls.append(1)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(dnit_icm, "load", down):
                ctx = BuildContext("raw")
                got = (ctx.dnit_potholes(), ctx.dnit_potholes())
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual(((("", []), ("", [])), 1), (got, len(calls)))
        self.assertEqual(["DNIT (buracos, ICM): ConnectionError: fora do ar"], new)


if __name__ == "__main__":
    unittest.main()
