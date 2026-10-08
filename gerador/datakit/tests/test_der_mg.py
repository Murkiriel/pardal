"""DER-MG: a tabela "Localização dos radares fixos em operação" (HTML: rodovia, km, município, velocidade), posta na
rodovia pela malha estadual da IDE-Sisema (SRE com km) e pelo SNV, e conferida pelo município que a tabela informa.
"""
import os
import tempfile
import unittest

try:
    from shapely.geometry import box
except ImportError:  # pragma: no cover
    box = None  # type: ignore[assignment]

from datakit.common.lrs import MeasuredLine, SnvRoutes

TABLE = """
<table><tr><td>184 TRECHOS/LOCAIS COM RADARES PORTÁTEIS</td></tr>
<tr><td>1º</td><td>BR356</td><td>34,0</td><td>Nova Lima</td><td>80 km/h</td></tr></table>
<table><tr><td>LOCALIZAÇÃO DOS 4 RADARES FIXOS EM OPERAÇÃO</td></tr><tr><td></td></tr>
<tr><th>URG</th><th>Rodovia</th><th>km</th><th>Município</th><th>Velocidade Regulamen. (Atual)</th><th>Nº</th></tr>
<tr><td>1º</td><td>MG050</td><td>10,0</td><td>Itaúna</td><td>60 km/h</td><td>1</td></tr>
<tr><td>1º</td><td>LMG800</td><td>2,6</td><td>Lagoa&nbsp;Santa</td><td>110 km/h 80 km/h</td><td>2</td></tr>
<tr><td>2º</td><td>BR356</td><td>3,1</td><td>Belo Horizonte</td><td>80 km/h</td><td>3</td></tr>
<tr><td>3º</td><td>MGC120</td><td>485,55</td><td>Nova Era</td><td></td><td>4</td></tr>
<tr><td colspan="5">Obs.: a maior velocidade vale para veículos leves.</td></tr></table>
"""


def _line(km0, km1, lng0, lng1, lat=-20.0, n=5):
    pts = [(lat, lng0 + (lng1 - lng0) * i / (n - 1)) for i in range(n)]
    return MeasuredLine(pts, [km0 + (km1 - km0) * i / (n - 1) for i in range(n)])


class Parse(unittest.TestCase):
    def test_the_fixed_radars_table_only_with_road_km_town_and_the_light_vehicles_limit(self):
        from datakit.sources import der_mg
        got = der_mg.parse(TABLE)
        self.assertEqual([("MG", 50, 10.0, "itauna", 60), ("LMG", 800, 2.6, "lagoa santa", 110),
                          ("BR", 356, 3.1, "belo horizonte", 80), ("MGC", 120, 485.55, "nova era", None)],
                         [(r.prefix, r.number, r.km, r.town, r.limit) for r in got])


@unittest.skipIf(box is None, "shapely não instalado")
class Place(unittest.TestCase):
    def setUp(self):
        from datakit.sources import der_mg
        self.der_mg = der_mg
        self.rows = [der_mg.Row("MG", 50, 10.0, "itauna", 60), der_mg.Row("LMG", 800, 2.6, "lagoa santa", 110),
                     der_mg.Row("BR", 356, 3.1, "belo horizonte", 80), der_mg.Row("MGC", 120, 30.0, "nova era", None),
                     der_mg.Row("MGC", 262, 5.0, "sabara", 60), der_mg.Row("MG", 50, 40.0, "itauna", 60)]
        self.network = {("E", 50): [_line(0.0, 50.0, -45.0, -44.5)], ("B", 120): [_line(0.0, 50.0, -43.0, -42.5, lat=-19.0)]}
        self.snv = SnvRoutes({(356, "MG"): [_line(0.0, 20.0, -44.0, -43.8, lat=-19.9)],
                              (262, "MG"): [_line(0.0, 20.0, -41.0, -40.8, lat=-19.5)]})
        self.towns = {"itauna": box(-45.0, -20.1, -44.85, -19.9),          # km 0-15 of the MG-050 line
                      "belo horizonte": box(-44.1, -20.0, -43.9, -19.8),
                      "nova era": box(-43.0, -19.1, -42.5, -18.9),
                      "sabara": box(-41.0, -19.6, -40.9, -19.4)}

    def test_each_radar_on_its_road_and_the_ones_with_no_road_or_out_of_town_counted(self):
        cams, out = self.der_mg.place(self.rows, self.network, self.snv, self.towns)
        self.assertEqual(4, len(cams))
        self.assertEqual({"no road": 1, "out of town": 1}, out)
        mg = cams[0]
        self.assertAlmostEqual(-44.9, mg.lng, places=6)
        self.assertEqual((mg.kind.value if hasattr(mg.kind, "value") else mg.kind, mg.limit_kmh, mg.source, mg.active),
                         ("FIXED", 60, "DER-MG", True))

    def test_mgc_goes_on_the_br_line_of_the_network_or_else_the_snv(self):
        cams, _ = self.der_mg.place(self.rows, self.network, self.snv, self.towns)
        by_town = {round(c.lat, 1): c for c in cams}
        self.assertEqual({-20.0, -19.9, -19.0, -19.5}, set(by_town))
        self.assertAlmostEqual(-42.7, by_town[-19.0].lng, places=6)     # MGC-120 km 30 on the network's BR line
        self.assertAlmostEqual(-40.95, by_town[-19.5].lng, places=6)    # MGC-262 km 5 on the SNV's BR-262

    def test_a_town_the_table_names_but_the_mesh_does_not_have_is_left_out(self):
        rows = [self.der_mg.Row("MG", 50, 10.0, "cidade nova", 60)]
        self.assertEqual(([], {"town not found": 1}), self.der_mg.place(rows, self.network, self.snv, self.towns))

    def test_two_km_out_of_town_is_still_in(self):
        rows = [self.der_mg.Row("MG", 50, 16.5, "itauna", 60)]    # ~1.5 km past the box's east edge
        cams, out = self.der_mg.place(rows, self.network, self.snv, self.towns)
        self.assertEqual((1, {}), (len(cams), out))


class Network(unittest.TestCase):
    def test_the_ide_sisema_segments_by_kind_and_road_number_with_their_km(self):
        import shapefile
        from datakit.sources import der_mg
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "ide_0401_mg_rodovias_lin")
            w = shapefile.Writer(base, shapeType=shapefile.POLYLINE)
            w.field("codigo_sre", "C")
            w.field("codigo_snv", "C")
            w.field("quilometra", "N", decimal=3)
            w.field("quilometr0", "N", decimal=3)
            w.line([[[-45.0, -20.0], [-44.5, -20.0]]])
            w.record("050EMG0010", "050EMG0010", 0.0, 50.0)
            w.line([[[-43.0, -19.0], [-42.5, -19.0]]])
            w.record("", "120BMG0010", 0.0, 50.0)
            w.line([[[-42.0, -18.0], [-41.5, -18.0]]])
            w.record("XYZ", "", 0.0, 10.0)
            w.close()
            import zipfile
            zpath = os.path.join(tmp, "mg.zip")
            with zipfile.ZipFile(zpath, "w") as z:
                for ext in (".shp", ".shx", ".dbf"):
                    z.write(base + ext, "ide_0401_mg_rodovias_lin" + ext)
            net = der_mg.load_network(zpath)
        self.assertEqual({("E", 50), ("B", 120)}, set(net))
        self.assertAlmostEqual(-44.9, net[("E", 50)][0].at(10.0)[1], places=6)



class Registered(unittest.TestCase):
    def test_the_build_takes_it_as_an_official_source_of_minas_gerais(self):
        from datakit import build
        from datakit.common.ufs import SOURCE_HOME_UF
        from datakit.sources import der_mg
        self.assertIs(der_mg, build.OFFICIAL.get("DER-MG"))
        self.assertEqual("MG", SOURCE_HOME_UF.get("DER-MG"))


if __name__ == "__main__":
    unittest.main()
