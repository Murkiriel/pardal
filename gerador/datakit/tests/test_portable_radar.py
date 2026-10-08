"""PRF: trechos aptos à fiscalização de velocidade com radar portátil (Res. Contran 798/2020, art. 7º).

A PRF publica a relação na página "trechos críticos", uma planilha do Google por período de validade; a vigente é a
"Lista válida a partir de DD/MM/AAAA". Cada linha: UF, BR, km inicial e km final (trechos de ~10 km). Os números vêm
com vírgula decimal e ponto de milhar, e há erros de digitação ("12o"): a linha que não se lê, de extensão zero ou de
um contorno ou acesso ("101 (Contorno viário)": o km não é o da BR) fica de fora e é contada.
"""
import csv
import os
import tempfile
import unittest
from unittest import mock

from datakit.common import RadarStretch
from datakit.common.lrs import MeasuredLine, SnvRoutes

PAGE = """
<a href="https://docs.google.com/spreadsheets/d/e/DDD/pubhtml?gid=1">Lista válida a partir de 01/09/2026 (rótulo antigo esquecido)</a>
<a href="https://docs.google.com/spreadsheets/d/e/AAA/pubhtml?gid=891430342&amp;single=true">Lista válida de 12/09/2026 a 02/10/2026</a>
<a class="x" href="https://docs.google.com/spreadsheets/d/e/BBB/pubhtml?gid=891430342&amp;single=true"><span>Lista válida a partir de 03/10/2026</span></a>
<a href="https://docs.google.com/spreadsheets/d/e/CCC/pubhtml">Lista válida de 24/12/2020 a 30/06/2021</a>
"""

CSV = '''"POLÍCIA RODOVIÁRIA FEDERAL
LOCAIS COM FISCALIZAÇÃO DE
VELOCIDADE POR RADAR PORTÁTIL
(Resolução 798/20 do CONTRAN)",,,
,,,
ESTADO (UF),RODOVIA (BR),KM INICIAL,KM FINAL
AC,317,"90,0","95,0"
GO,020,"10,0","20,0"
BA,116,"1.010,0","1.020,0"
RN,304,"110,0",12o
SC,101,380,"380,0"
SC,101 (Contorno viário),20,"30,0"
,,,
'''


class CurrentList(unittest.TestCase):
    def test_the_newest_list_valid_from_a_date_on_is_the_current_one_read_as_csv(self):
        from datakit.sources import prf_portable_radar
        valid_from, url = prf_portable_radar.current_list(PAGE)
        self.assertEqual("2026-10-03", valid_from)
        self.assertEqual("https://docs.google.com/spreadsheets/d/e/BBB/pub?gid=891430342&single=true&output=csv", url)

    def test_a_page_with_no_current_list_is_an_error(self):
        from datakit.sources import prf_portable_radar
        with self.assertRaises(ValueError):
            prf_portable_radar.current_list(PAGE.replace("a partir de", "de"))


class Parse(unittest.TestCase):
    def test_each_stretch_with_its_state_road_and_km_and_the_broken_ones_counted(self):
        from datakit.sources import prf_portable_radar
        stretches, skipped = prf_portable_radar.parse(CSV)
        self.assertEqual([RadarStretch("AC", 317, 90.0, 95.0), RadarStretch("GO", 20, 10.0, 20.0),
                          RadarStretch("BA", 116, 1010.0, 1020.0)], stretches)
        self.assertEqual(3, skipped, "'12o', the zero-length one and the bypass (its km is not the main road's)")

    def test_km_with_a_dot_as_decimal_or_no_decimal(self):
        from datakit.sources import prf_portable_radar
        self.assertEqual(1010.0, prf_portable_radar.km("1.010.0"))
        self.assertEqual(380.0, prf_portable_radar.km("380"))
        self.assertEqual(12.5, prf_portable_radar.km(" 12,5 "))
        self.assertIsNone(prf_portable_radar.km("12o"))


class Load(unittest.TestCase):
    def test_the_page_then_the_current_sheet(self):
        from datakit.sources import prf_portable_radar
        fetched = []

        def text(url, **_):
            fetched.append(url)
            return PAGE if url == prf_portable_radar.PAGE else CSV
        with mock.patch.object(prf_portable_radar, "get_text", text):
            valid_from, stretches, skipped = prf_portable_radar.load()
        self.assertEqual(("2026-10-03", 3, 3), (valid_from, len(stretches), skipped))
        self.assertEqual(prf_portable_radar.PAGE, fetched[0])
        self.assertIn("/BBB/pub?", fetched[1])


def _line(km0, km1, lng0, lng1, lat=-16.0, n=6):
    """Uma reta para leste, com o km crescendo de km0 a km1."""
    pts = [(lat, lng0 + (lng1 - lng0) * i / (n - 1)) for i in range(n)]
    return MeasuredLine(pts, [km0 + (km1 - km0) * i / (n - 1) for i in range(n)])


class Place(unittest.TestCase):
    def test_each_stretch_takes_the_road_between_its_km_and_one_with_no_road_is_counted(self):
        from datakit import radar_zones
        routes = SnvRoutes({(20, "GO"): [_line(0.0, 50.0, -49.5, -49.0)]})
        stretches = [RadarStretch("GO", 20, 10.0, 20.0), RadarStretch("GO", 153, 0.0, 10.0),
                     RadarStretch("DF", 20, 0.0, 10.0)]
        zones, unplaced = radar_zones.place("GO", "2026-10-03", stretches, routes)
        self.assertEqual(1, unplaced, "BR-153 has no SNV line here; the DF one is another state")
        self.assertEqual([("BR-020", 10.0, 20.0, "2026-10-03")], [(z.road, z.km_from, z.km_to, z.valid_from) for z in zones])
        first, last = zones[0].points[0], zones[0].points[-1]
        self.assertAlmostEqual(-49.4, first[1], places=6)
        self.assertAlmostEqual(-49.3, last[1], places=6)

    def test_a_stretch_over_two_snv_lines_is_one_piece_per_line(self):
        from datakit import radar_zones
        routes = SnvRoutes({(60, "GO"): [_line(15.0, 40.0, -49.0, -48.5), _line(0.0, 15.0, -49.3, -49.0)]})
        zones, unplaced = radar_zones.place("GO", "2026-10-03", [RadarStretch("GO", 60, 10.0, 20.0)], routes)
        self.assertEqual((0, [(10.0, 15.0), (15.0, 20.0)]), (unplaced, [(z.km_from, z.km_to) for z in zones]))

    def test_without_the_snv_every_stretch_is_counted_out(self):
        from datakit import radar_zones
        self.assertEqual(([], 1), radar_zones.place("GO", "2026-10-03", [RadarStretch("GO", 20, 10.0, 20.0)], None))


class Shape(unittest.TestCase):
    def test_points_on_a_straight_road_go_and_a_bend_stays(self):
        from datakit import radar_zones
        straight = [(-16.0, -49.0 + i * 0.001) for i in range(10)]
        self.assertEqual([straight[0], straight[-1]], radar_zones.simplify(straight, 15.0))
        bend = [(-16.0, -49.0), (-16.0, -48.99), (-15.99, -48.99)]
        self.assertEqual(bend, radar_zones.simplify(bend, 15.0))

    def test_the_row_carries_the_line_as_wkt(self):
        from datakit.common import RadarZone
        z = RadarZone("BR-020", 10.0, 20.5, "2026-10-03", ((-16.0, -49.4), (-16.012346, -49.3)))
        self.assertEqual(["BR-020", "10.0", "20.5", "2026-10-03", "LINESTRING(-49.40000 -16.00000, -49.30000 -16.01235)"], z.row())
        self.assertEqual(("road", "km_from", "km_to", "valid_from", "wkt"), RadarZone.HEADER)


class Context(unittest.TestCase):
    def test_the_list_is_fetched_once_and_a_failure_is_an_empty_list_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import prf_portable_radar
        calls = []

        def down():
            calls.append(1)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(prf_portable_radar, "load", down):
                ctx = BuildContext("raw")
                got = (ctx.prf_portable_radar(), ctx.prf_portable_radar())
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual(((("", []), ("", [])), 1), (got, len(calls)))
        self.assertEqual(["PRF (trechos de radar portátil): ConnectionError: fora do ar"], new)


class PackAndCatalog(unittest.TestCase):
    def test_the_pack_writes_the_zones_counts_them_and_the_catalog_lists_them(self):
        from datakit import build_catalog
        from datakit.build_pack import build as build_pack
        from datakit.common import RadarZone
        zones = [RadarZone("BR-020", 10.0, 20.0, "2026-10-03", ((-16.0, -49.4), (-16.0, -49.3)))]
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            m = build_pack("GO", packs, [], [], [], sources=[], radar_zones=zones)
            build_pack("AC", packs, [], [], [], sources=[])
            with open(os.path.join(packs, "GO", "radar_portatil.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            cat = build_catalog.build(packs, dist)
        self.assertEqual([{"road": "BR-020", "km_from": "10.0", "km_to": "20.0", "valid_from": "2026-10-03",
                           "wkt": "LINESTRING(-49.40000 -16.00000, -49.30000 -16.00000)"}], rows)
        self.assertEqual((m["schema"], m["counts"]["portable_radar"]), (5, 1))
        self.assertIn("radar_portatil.csv", m["files"])
        self.assertEqual((cat["ufs"]["GO"]["portable_radar"]["file"], cat["ufs"]["GO"]["portable_radar"]["count"]),
                         ("estados/GO/radar_portatil.csv", 1))
        self.assertNotIn("portable_radar", cat["ufs"]["AC"])


if __name__ == "__main__":
    unittest.main()
