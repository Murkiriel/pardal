"""PRF: multas de velocidade por trecho apto ao radar portátil (a coluna speed_fines_12m do radar_portatil.csv).

A PRF publica as autuações dela em "Dados abertos": um zip por ano no Google Drive, um CSV por mês (latin-1, `;`), com
UF, BR e km (sem coordenada). As de velocidade são as do art. 218 do CTB, que mudou de formato no meio de 2025 ("218 I"
para "art. 218, I"). A contagem é dos 12 meses mais novos publicados, somada por trecho da lista da PRF, sem data, hora
nem pessoa: diz onde a PRF de fato multa por velocidade, não onde haverá radar.
"""
import csv
import io
import os
import tempfile
import unittest
import zipfile
from collections import Counter
from unittest import mock

from datakit.common import RadarStretch, RadarZone
from datakit.common.lrs import MeasuredLine, SnvRoutes

PAGE = """
<table><tbody>
<tr class=""><td><p>Documento CSV de multas 2026</p></td><td><p><a href="https://drive.google.com/file/d/ID2026/view?usp=sharing"
 class="external">Baixar planilha</a><a href="https://drive.google.com/file/d/ID2025/view?usp=sharing/download" class="external"></a></p></td></tr>
<tr class=""><td><p>Documento CSV de multas 2025</p></td><td><p><a href="https://drive.google.com/file/d/ID2025/view?usp=sharing/download">Baixar planilha</a></p></td></tr>
<tr class=""><td><p>Documento CSV de Acidentes 2025 (Agrupados por pessoa)</p></td><td><p><a href="https://drive.google.com/file/d/ACID/view">Baixar planilha</a></p></td></tr>
</tbody></table>
"""

HEAD = ["Número do Auto", "Data da Infração (DD/MM/AAAA)", "UF Infração", "BR Infração", "Km Infração",
        "Enquadramento da Infração", "Qtd Infrações"]


def _csv(rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(HEAD)
    w.writerows(rows)
    return out.getvalue().encode("latin-1")


def _zip(path: str, months: dict) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in months.items():
            z.writestr(f"ajustados/infraçoes_{name}.csv", _csv(rows))
    return path


class YearlyFiles(unittest.TestCase):
    def test_each_year_takes_the_first_file_of_its_own_row(self):
        from datakit.sources import prf_fines
        # the 2026 row also carries the 2025 link (an empty anchor): the first one is the year's
        self.assertEqual({2026: "ID2026", 2025: "ID2025"}, prf_fines.yearly_files(PAGE))

    def test_a_page_without_the_fines_is_an_error(self):
        from datakit.sources import prf_fines
        with self.assertRaises(ValueError):
            prf_fines.yearly_files("<table><tr><td>Acidentes 2025</td></tr></table>")

    def test_the_download_link_of_a_drive_file(self):
        from datakit.sources import prf_fines
        self.assertEqual("https://drive.usercontent.google.com/download?id=ID2026&export=download&confirm=t",
                         prf_fines.download_url("ID2026"))


class Count(unittest.TestCase):
    def test_speed_fines_in_both_formats_by_month_state_road_and_km(self):
        from datakit.sources import prf_fines
        with tempfile.TemporaryDirectory() as tmp:
            path = _zip(os.path.join(tmp, "2025.zip"), {
                "2025_05": [["a", "20/05/2025", "GO", "060", "112", "218 I", "1"],
                            ["b", "21/05/2025", "GO", "060", "112", "218 II", "2"],
                            ["c", "21/05/2025", "GO", "060", "112", "Art. 99, XIV", "1"],
                            ["d", "21/05/2025", "GO", "060", "", "218 I", "1"]],
                "2025_08": [["e", "2025-08-03", "GO", "060", "115", "art. 218, I", "1"],
                            ["f", "2025-08-03", "SP", "116", "100,7", "art. 218, III", "1"],
                            ["g", "2025-08-03", "SP", "116", "100", "art. 2180", "1"]],
            })
            got = prf_fines.count(path)
        self.assertEqual(Counter({("2025-05", "GO", 60, 112): 3, ("2025-08", "GO", 60, 115): 1,
                                  ("2025-08", "SP", 116, 100): 1}), got)

    def test_the_twelve_newest_months_published(self):
        from datakit.sources import prf_fines
        counts = Counter({("2025-07", "GO", 60, 112): 5, ("2025-08", "GO", 60, 112): 1,
                          ("2026-07", "GO", 60, 113): 2, ("2026-03", "SP", 116, 100): 4})
        period, by_km = prf_fines.last_months(counts, 12)
        self.assertEqual(("2025-08", "2026-07"), period)
        self.assertEqual(Counter({("GO", 60, 112): 1, ("GO", 60, 113): 2, ("SP", 116, 100): 4}), by_km)

    def test_load_reads_the_two_newest_years(self):
        from datakit.sources import prf_fines
        with tempfile.TemporaryDirectory() as tmp:
            files = {"ID2026": {"2026_07": [["a", "2026-07-01", "GO", "060", "112", "art. 218, I", "1"]]},
                     "ID2025": {"2025_07": [["b", "2025-07-01", "GO", "060", "112", "218 I", "9"]],
                                "2025_08": [["c", "2025-08-01", "GO", "060", "113", "218 I", "1"]]}}
            fetched = []

            def fetch(file_id, dest):
                fetched.append(file_id)
                _zip(dest, files[file_id])
            with mock.patch.object(prf_fines, "get_text", lambda url: PAGE), \
                    mock.patch.object(prf_fines, "_fetch", fetch):
                period, by_km = prf_fines.load(tmp)
        self.assertEqual(["ID2026", "ID2025"], fetched)
        self.assertEqual((("2025-08", "2026-07"), Counter({("GO", 60, 112): 1, ("GO", 60, 113): 1})), (period, by_km))


def _line(km0, km1, lng0, lng1, n=11):
    pts = [(-16.0, lng0 + (lng1 - lng0) * i / (n - 1)) for i in range(n)]
    return MeasuredLine(pts, [km0 + (km1 - km0) * i / (n - 1) for i in range(n)])


class PerStretch(unittest.TestCase):
    def test_each_stretch_sums_the_fines_of_its_own_km(self):
        from datakit import radar_zones
        routes = SnvRoutes({(60, "GO"): [_line(15.0, 40.0, -49.0, -48.5), _line(0.0, 15.0, -49.3, -49.0)]})
        fines = Counter({("GO", 60, 9): 50, ("GO", 60, 10): 3, ("GO", 60, 19): 4, ("GO", 60, 20): 70,
                         ("SP", 60, 12): 99, ("GO", 153, 12): 99})
        zones, _ = radar_zones.place("GO", "2026-10-03", [RadarStretch("GO", 60, 10.0, 20.0)], routes, fines)
        # km 10 to 19 of BR-060 in GO: 3 + 4; the stretch over two SNV lines carries its count on both pieces
        self.assertEqual([(10.0, 15.0, 7), (15.0, 20.0, 7)], [(z.km_from, z.km_to, z.speed_fines) for z in zones])

    def test_a_stretch_with_no_fine_is_zero_and_without_the_source_unknown(self):
        from datakit import radar_zones
        routes = SnvRoutes({(20, "GO"): [_line(0.0, 50.0, -49.5, -49.0)]})
        stretch = [RadarStretch("GO", 20, 10.0, 20.0)]
        self.assertEqual([0], [z.speed_fines for z in radar_zones.place("GO", "x", stretch, routes, Counter())[0]])
        self.assertEqual([None], [z.speed_fines for z in radar_zones.place("GO", "x", stretch, routes)[0]])

    def test_the_row_has_the_count_or_nothing(self):
        z = RadarZone("BR-020", 10.0, 20.0, "2026-10-03", ((-16.0, -49.4), (-16.0, -49.3)), speed_fines=129)
        self.assertEqual("129", z.row()[-1])
        self.assertEqual("", RadarZone("BR-020", 10.0, 20.0, "2026-10-03", ((-16.0, -49.4), (-16.0, -49.3))).row()[-1])
        self.assertEqual("speed_fines_12m", RadarZone.HEADER[-1])


class Context(unittest.TestCase):
    def test_the_fines_are_read_once_and_a_failure_is_unknown_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import prf_fines
        calls = []

        def down(raw_dir):
            calls.append(raw_dir)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(prf_fines, "load", down):
                ctx = BuildContext("raw")
                got = (ctx.prf_speed_fines(), ctx.prf_speed_fines())
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual(((None, None), 1), (got, len(calls)))
        self.assertEqual(["PRF (multas de velocidade): ConnectionError: fora do ar"], new)


if __name__ == "__main__":
    unittest.main()
