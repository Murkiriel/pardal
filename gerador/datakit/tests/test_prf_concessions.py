"""PRF: radares fixos das concessões federais (a planilha que a página "Radares fixos" da PRF mostra).

A página oficial não traz arquivo: põe num iframe uma tabela externa (`tabela.html`), que lê uma planilha por um Google
Apps Script (JSON público, `{"records": [...]}`). Cada equipamento: situação (ATIVO, INOPERANTE, "E/L ou CERTIFICADO
VENCIDOS"), UF, BR, km, sentido do km, coordenada (nem todos) e o limite de leves e de pesados. Fonte informal (fora do
gov.br, sem licença escrita, endereço que muda): com ela fora do ar, o Pardal segue com ANTT e DNIT e a falha fica
registrada. Uso (decisão do dono, opção C): o radar do pacote que a planilha dá como inoperante ou vencido, sem
equipamento ativo dela por perto, sai inativo; o ativo dela sem radar nenhum por perto entra, com a fonte "PRF".
"""
import unittest
from unittest import mock

from datakit.common import Camera
from datakit.common.lrs import MeasuredLine, SnvRoutes

PAGE = """
<div class="maps-inner"><iframe title="MAPA DE RADARES FIXOS" src="https://mapas-html.vercel.app/" class="google-map"></iframe></div>
<div class="maps-inner"><iframe title="TABELA DE RADARES FIXOS" src="https://mapas-html.vercel.app/tabela.html" class="google-map"></iframe></div>
"""
TABLE = """<script>
  const appsScriptUrl = 'https://script.google.com/macros/s/AKfy-cb_123/exec';
  fetch(appsScriptUrl, { priority: 'high' })
</script>"""


def _rec(state="ATIVO", uf="GO", br=153, km=70.8, sense="CRESCENTE", lat=-13.5, lng=-49.1, light=80):
    return {"ESTADO": state, "UF": uf, "BR": br, "LOCAL": km, "SENTIDO": sense, "LATITUDE": lat, "LONGITUDE": lng,
            "VELOCIDADE LEVE": light, "VELOCIDADE PESADO": 60, "CONCESSIONÁRIA": "X", "link": "https://drive.google.com/x"}


class Find(unittest.TestCase):
    def test_the_table_of_the_page_then_its_script(self):
        from datakit.sources import prf_concessions
        self.assertEqual("https://mapas-html.vercel.app/tabela.html", prf_concessions.table_url(PAGE))
        self.assertEqual("https://script.google.com/macros/s/AKfy-cb_123/exec", prf_concessions.script_url(TABLE))

    def test_a_page_without_them_is_an_error(self):
        from datakit.sources import prf_concessions
        with self.assertRaises(ValueError):
            prf_concessions.table_url('<iframe title="MAPA" src="https://mapas-html.vercel.app/"></iframe>')
        with self.assertRaises(ValueError):
            prf_concessions.script_url("<script>fetch('x')</script>")

    def test_load_follows_the_page_the_table_and_the_script(self):
        from datakit.sources import prf_concessions
        asked = []

        def text(url):
            asked.append(url)
            return PAGE if "gov.br" in url else TABLE
        with mock.patch.object(prf_concessions, "get_text", text), \
                mock.patch.object(prf_concessions, "get_json", lambda url: {"records": [_rec()]}):
            got = prf_concessions.load()
        self.assertEqual([prf_concessions.PAGE, "https://mapas-html.vercel.app/tabela.html"], asked)
        self.assertEqual([("GO", 153, "ACTIVE")], [(r.uf, r.br, r.state) for r in got])


class Parse(unittest.TestCase):
    def test_each_equipment_with_its_state_place_and_light_vehicle_limit(self):
        from datakit.sources import prf_concessions
        got = prf_concessions.parse({"records": [
            _rec(), _rec(state="INOPERANTE", uf="GO ", sense="DECRESCENTE ", light=None),
            _rec(state="E/L ou CERTIFICADO VENCIDOS", sense="AMBOS"), _rec(lat=None, lng=None),
            _rec(lat=0, lng=0), _rec(state="OUTRO"),
        ]})
        R = prf_concessions.Record
        self.assertEqual([R("ACTIVE", "GO", 153, 70.8, True, -13.5, -49.1, 80),
                          R("INOPERATIVE", "GO", 153, 70.8, False, -13.5, -49.1, None),
                          R("EXPIRED", "GO", 153, 70.8, None, -13.5, -49.1, 80)], got)


def _line():
    # BR-153 em GO, reta para o norte (o km cresce para o norte), km 60 a 80
    n = 21
    pts = [(-13.6 + 0.2 * i / (n - 1), -49.1) for i in range(n)]
    return MeasuredLine(pts, [60.0 + 20.0 * i / (n - 1) for i in range(n)])


class Apply(unittest.TestCase):
    def setUp(self):
        from datakit.sources import prf_concessions
        self.R = prf_concessions.Record
        self.routes = SnvRoutes({(153, "GO"): [_line()]})

    def test_a_radar_the_sheet_gives_as_out_of_order_turns_inactive_unless_an_active_one_is_near(self):
        from datakit.sources import prf_concessions
        here = Camera(-13.5, -49.1, limit_kmh=80, source="ANTT")
        there = Camera(-13.45, -49.1, limit_kmh=80, source="ANTT")
        both = Camera(-13.40, -49.1, limit_kmh=80, source="ANTT")
        records = [self.R("INOPERATIVE", "GO", 153, 70.8, True, -13.5005, -49.1, 80),      # ~56 m
                   self.R("EXPIRED", "GO", 153, 73.6, True, -13.4510, -49.1, 80),          # ~111 m
                   self.R("INOPERATIVE", "GO", 153, 76.0, True, -13.40, -49.1, 80),
                   self.R("ACTIVE", "GO", 153, 76.0, False, -13.4005, -49.1, 80)]          # the other direction, active
        cams, stats = prf_concessions.apply([here, there, both], records, self.routes)
        self.assertEqual([False, False, True], [c.active for c in cams[:3]])
        self.assertEqual(2, stats["retired"])

    def test_an_active_one_with_no_radar_near_comes_in_with_its_direction_and_light_limit(self):
        from datakit.sources import prf_concessions
        known = Camera(-13.5, -49.1, limit_kmh=80, source="ANTT")
        records = [self.R("ACTIVE", "GO", 153, 70.8, True, -13.5010, -49.1, 80),        # ~111 m: the same radar
                   self.R("ACTIVE", "GO", 153, 76.0, False, -13.40, -49.1, 60),        # new, decreasing: south
                   self.R("INOPERATIVE", "GO", 153, 66.0, True, -13.54, -49.1, 80)]    # out of order: not added
        cams, stats = prf_concessions.apply([known], records, self.routes)
        self.assertEqual([known, Camera(-13.40, -49.1, limit_kmh=60, source="PRF", direction_deg=180)], cams)
        self.assertEqual({"records": 3, "retired": 0, "added": 1}, stats)

    def test_both_directions_at_one_place_are_two_radars_and_a_repeated_row_is_one(self):
        from datakit.sources import prf_concessions
        records = [self.R("ACTIVE", "GO", 153, 76.0, True, -13.40, -49.1, 60),
                   self.R("ACTIVE", "GO", 153, 76.0, False, -13.40, -49.1, 60),     # the same point, the other way
                   self.R("ACTIVE", "GO", 153, 76.0, True, -13.40, -49.1, 60)]
        cams, stats = prf_concessions.apply([], records, self.routes)
        self.assertEqual([0, 180], [c.direction_deg for c in cams])
        self.assertEqual(2, stats["added"])

    def test_without_the_snv_the_new_one_has_no_direction(self):
        from datakit.sources import prf_concessions
        cams, _ = prf_concessions.apply([], [self.R("ACTIVE", "GO", 153, 76.0, False, -13.40, -49.1, 60)], None)
        self.assertEqual([None], [c.direction_deg for c in cams])


class Context(unittest.TestCase):
    def test_the_sheet_is_read_once_and_a_failure_is_nothing_recorded(self):
        from datakit import failures
        from datakit.context import BuildContext
        from datakit.sources import prf_concessions
        calls = []

        def down():
            calls.append(1)
            raise ConnectionError("fora do ar")
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(prf_concessions, "load", down):
                ctx = BuildContext("raw")
                got = (ctx.prf_concessions(), ctx.prf_concessions())
            new = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual((([], []), 1), (got, len(calls)))
        self.assertEqual(["PRF (radares das concessões): ConnectionError: fora do ar"], new)


if __name__ == "__main__":
    unittest.main()
