"""PRF: trechos aptos à fiscalização de velocidade com radar portátil (Res. Contran 798/2020, art. 7º).

A PRF publica a relação na página "trechos críticos", uma planilha do Google por período de validade; a vigente é a
"Lista válida a partir de DD/MM/AAAA". Cada linha: UF, BR, km inicial e km final (trechos de ~10 km). Os números vêm
com vírgula decimal e ponto de milhar, e há erros de digitação ("12o"): a linha que não se lê, de extensão zero ou de
um contorno ou acesso ("101 (Contorno viário)": o km não é o da BR) fica de fora e é contada.
"""
import unittest
from unittest import mock

from datakit.common import RadarStretch

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


if __name__ == "__main__":
    unittest.main()
