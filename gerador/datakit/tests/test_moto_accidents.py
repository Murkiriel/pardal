"""PRF: trechos de rodovia federal com muitos acidentes com moto (acidentes_moto.csv, schema 8).

A PRF publica os acidentes que atende em "Dados abertos": um zip por ano no Google Drive; o corte "agrupados por
pessoa" tem um envolvido por linha, com o acidente (id, data, UF, BR, km, coordenada), o tipo do veículo e o estado
físico. Um acidente é "com moto" quando algum veículo dele é motocicleta, motoneta ou ciclomotor; "motociclista morto" é
quem estava numa delas e morreu. Nos 12 meses mais novos publicados, o km (inteiro) com 5 ou mais acidentes com moto
entra, e km seguidos assim viram um trecho, com a linha da BR pelo SNV. Só contagens: sem data, hora nem pessoa.
"""
import csv
import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from datakit.common.lrs import MeasuredLine, SnvRoutes

PAGE = """
<table><tbody>
<tr><td><p>Documento CSV de Acidentes 2026 (Agrupados por pessoa)</p></td><td><p><a href="https://drive.google.com/file/d/P2026/view">Baixar planilha</a><a href="https://drive.google.com/file/d/X/view"></a></p></td></tr>
<tr><td><p>Documento CSV de Acidentes 2026 (Agrupados por pessoa - Todas as causas e tipos de acidentes)</p></td><td><p><a href="https://drive.google.com/file/d/T2026/view">Baixar planilha</a></p></td></tr>
<tr><td><p>Documento CSV de Acidentes 2026 (Agrupados por ocorrência)</p></td><td><p><a href="https://drive.google.com/file/d/O2026/view">Baixar planilha</a></p></td></tr>
<tr><td><p>Documento CSV de Acidentes 2025 (Agrupados por pessoa - Todas as causas e tipos de acidentes)</p></td><td><p><a href="https://drive.google.com/file/d/T2025/view">Baixar planilha</a></p></td></tr>
<tr><td><p>Documento CSV de Acidentes 2025 (Agrupados por pessoa)</p></td><td><p><a href="https://drive.google.com/file/d/P2025/view">Baixar planilha</a></p></td></tr>
<tr><td><p>Documento CSV de multas 2025</p></td><td><p><a href="https://drive.google.com/file/d/M2025/view">Baixar planilha</a></p></td></tr>
</tbody></table>
"""

HEAD = ["id", "pesid", "data_inversa", "uf", "br", "km", "tipo_veiculo", "estado_fisico", "mortos", "latitude", "longitude"]


def _person(acc, date, uf, br, km, vehicle, state="Ileso", dead="0"):
    return [acc, "1", date, uf, br, km, vehicle, state, dead, "-16,1", "-49,2"]


def _zip(path, rows):
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(HEAD)
    w.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("acidentes.csv", out.getvalue().encode("latin-1"))
    return path


class YearlyFiles(unittest.TestCase):
    def test_the_per_person_file_of_each_year_and_not_the_other_cuts(self):
        from datakit.sources import prf_accidents
        self.assertEqual({2026: "P2026", 2025: "P2025"}, prf_accidents.yearly_files(PAGE))

    def test_a_page_without_them_is_an_error(self):
        from datakit.sources import prf_accidents
        with self.assertRaises(ValueError):
            prf_accidents.yearly_files("<table><tr><td>Documento CSV de multas 2025</td></tr></table>")


class Read(unittest.TestCase):
    def test_one_accident_per_id_with_a_moto_and_its_dead_riders(self):
        from datakit.sources import prf_accidents
        with tempfile.TemporaryDirectory() as tmp:
            path = _zip(os.path.join(tmp, "a.zip"), [
                _person("1", "2026-07-03", "GO", "153", "506,4", "Automóvel"),
                _person("1", "2026-07-03", "GO", "153", "506,4", "Motocicleta", "Óbito", "1"),
                _person("1", "2026-07-03", "GO", "153", "506,4", "Motoneta", "Óbito", "1"),
                _person("2", "2026-07-04", "GO", "153", "506,9", "Caminhão", "Óbito", "1"),
                _person("3", "05/06/2026", "GO", "060", "97", "Ciclomotor"),
                _person("4", "2026-07-04", "GO", "", "12", "Motocicleta"),
            ])
            got = prf_accidents.read(path)
        self.assertEqual({
            "1": prf_accidents.Accident("2026-07", "GO", 153, 506, True, 2),
            "2": prf_accidents.Accident("2026-07", "GO", 153, 506, False, 0),  # a truck driver is not a rider
            "3": prf_accidents.Accident("2026-06", "GO", 60, 97, True, 0),
        }, got)

    def test_load_keeps_the_motorcycle_ones_of_the_twelve_newest_months_of_the_two_newest_years(self):
        from datakit.sources import prf_accidents
        files = {
            "P2026": [_person("9", "2026-08-01", "GO", "153", "506", "Motocicleta"),
                      _person("8", "2026-08-01", "GO", "153", "506", "Automóvel")],
            "P2025": [_person("7", "2025-09-10", "GO", "153", "507", "Motocicleta"),
                      _person("6", "2025-08-31", "GO", "153", "507", "Motocicleta")],
        }
        fetched = []
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(file_id, dest):
                fetched.append(file_id)
                _zip(dest, files[file_id])
            with mock.patch.object(prf_accidents, "get_text", lambda url: PAGE), \
                    mock.patch.object(prf_accidents, "_fetch", fetch):
                period, got = prf_accidents.load(tmp)
        self.assertEqual(["P2026", "P2025"], fetched)
        self.assertEqual(("2025-09", "2026-08"), period)
        self.assertEqual([("GO", 153, 506), ("GO", 153, 507)], sorted((a.uf, a.br, a.km) for a in got))


class Spots(unittest.TestCase):
    def test_a_km_with_five_or_more_goes_in_and_km_in_a_row_are_one_stretch(self):
        from datakit import accident_spots
        from datakit.sources.prf_accidents import Accident

        def many(km, n, dead=0, uf="GO", br=153):
            return [Accident("2026-07", uf, br, km, True, dead if i == 0 else 0) for i in range(n)]
        accidents = (many(504, 5, dead=1) + many(505, 7) + many(506, 4) + many(508, 9, dead=2) + many(504, 6, uf="DF")
                     + many(510, 5) + many(511, 1) + many(512, 5))
        got = accident_spots.spots(accidents)
        self.assertEqual(5, accident_spots.MIN_PER_KM)
        self.assertEqual([accident_spots.AccidentKm("DF", 153, 504, 505, 6, 0),
                          accident_spots.AccidentKm("GO", 153, 504, 506, 12, 1),
                          accident_spots.AccidentKm("GO", 153, 508, 509, 9, 2),
                          accident_spots.AccidentKm("GO", 153, 510, 511, 5, 0),  # one quiet km between: two stretches
                          accident_spots.AccidentKm("GO", 153, 512, 513, 5, 0)], got)


def _line(km0, km1, lng0, lng1, n=11):
    pts = [(-16.0, lng0 + (lng1 - lng0) * i / (n - 1)) for i in range(n)]
    return MeasuredLine(pts, [km0 + (km1 - km0) * i / (n - 1) for i in range(n)])


class Place(unittest.TestCase):
    def test_each_stretch_takes_the_road_between_its_km_and_one_with_no_road_is_counted(self):
        from datakit import accident_spots
        routes = SnvRoutes({(153, "GO"): [_line(500.0, 510.0, -49.5, -49.0)]})
        spots = [accident_spots.AccidentKm("GO", 153, 504, 506, 12, 1), accident_spots.AccidentKm("GO", 60, 97, 98, 6, 0),
                 accident_spots.AccidentKm("DF", 153, 504, 505, 6, 0)]
        zones, unplaced = accident_spots.place("GO", ("2025-09", "2026-08"), spots, routes)
        self.assertEqual(1, unplaced, "BR-060 has no SNV line here; the DF one is another state")
        self.assertEqual([("BR-153", 504.0, 506.0, 12, 1, "2025-09/2026-08")],
                         [(z.road, z.km_from, z.km_to, z.accidents, z.rider_deaths, z.months) for z in zones])
        self.assertAlmostEqual(-49.3, zones[0].points[0][1], places=6)
        self.assertAlmostEqual(-49.2, zones[0].points[-1][1], places=6)

    def test_the_row_for_the_pack(self):
        from datakit.common import AccidentZone
        z = AccidentZone("BR-153", 504.0, 506.0, 12, 1, "2025-09/2026-08", ((-16.0, -49.3), (-16.0, -49.2)))
        self.assertEqual(("road", "km_from", "km_to", "accidents", "rider_deaths", "months", "wkt"), AccidentZone.HEADER)
        self.assertEqual(["BR-153", "504.0", "506.0", "12", "1", "2025-09/2026-08",
                          "LINESTRING(-49.30000 -16.00000, -49.20000 -16.00000)"], z.row())


class PackAndCatalog(unittest.TestCase):
    def test_the_pack_writes_the_stretches_counts_them_and_the_catalog_lists_them(self):
        from datakit import build_catalog
        from datakit.build_pack import build as build_pack
        from datakit.common import AccidentZone
        zones = [AccidentZone("BR-153", 504.0, 506.0, 12, 1, "2025-09/2026-08", ((-16.0, -49.3), (-16.0, -49.2)))]
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            m = build_pack("GO", packs, [], [], [], sources=[], moto_accidents=zones)
            build_pack("AC", packs, [], [], [], sources=[])
            self.assertTrue(os.path.exists(os.path.join(packs, "GO", "acidentes_moto.csv")), "the pack has the file")
            with open(os.path.join(packs, "GO", "acidentes_moto.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            cat = build_catalog.build(packs, dist)
        self.assertEqual([("BR-153", "12", "1")], [(r["road"], r["accidents"], r["rider_deaths"]) for r in rows])
        self.assertEqual((m["schema"], m["counts"]["moto_accidents"]), (8, 1))
        self.assertIn("acidentes_moto.csv", m["files"])
        self.assertEqual((cat["ufs"]["GO"]["moto_accidents"]["file"], cat["ufs"]["GO"]["moto_accidents"]["count"]),
                         ("estados/GO/acidentes_moto.csv", 1))
        self.assertNotIn("moto_accidents", cat["ufs"]["AC"])


class Context(unittest.TestCase):
    def test_the_accidents_are_read_once_and_a_failure_is_nothing_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import prf_accidents
        calls = []

        def down(raw_dir):
            calls.append(raw_dir)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(prf_accidents, "load", down):
                ctx = BuildContext("raw")
                got = (ctx.prf_moto_accidents(), ctx.prf_moto_accidents())
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual((((("", ""), []), (("", ""), [])), 1), (got, len(calls)))
        self.assertEqual(["PRF (acidentes com moto): ConnectionError: fora do ar"], new)


if __name__ == "__main__":
    unittest.main()
