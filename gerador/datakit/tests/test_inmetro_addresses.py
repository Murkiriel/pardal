"""Endereços do Inmetro localizados pelo cadastro de endereços do IBGE (CNEFE) e juntados aos radares.

    python -m unittest discover -s datakit/tests -t .     # de dentro de gerador/
"""
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from datakit import failures, inmetro_addresses
from datakit.common import Camera, CameraKind, haversine_m
from datakit.common.address import parse_crossing, parse_number, street_key
from datakit.sources import cnefe
from datakit.sources.inmetro import Meter

HEADER = "COD_UNICO_ENDERECO;NOM_TIPO_SEGLOGR;NOM_TITULO_SEGLOGR;NOM_SEGLOGR;NUM_ENDERECO;LATITUDE;LONGITUDE;NV_GEO_COORD"


def _zip(folder, name, rows):
    """Um .zip do CNEFE de mentira: rows = (tipo, título, nome, número, lat, lng, nível)."""
    path = os.path.join(folder, name)
    body = "\n".join([HEADER, *(";".join(["1", *map(str, r)]) for r in rows)]) + "\n"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name.replace(".zip", ".csv"), body)
    return path


def _meter(city, local, limit=60, valid=True, fixed=True, road=None, uf="PB"):
    return Meter(uf=uf, municipality=city, local=local, fixed=fixed, valid=valid, road=road, km=None, limit_kmh=limit)


# Uma avenida leste-oeste (números crescem para leste, 10 m por número) e uma rua norte-sul que a
# cruza na altura do número 200 da avenida. 0,0001° de longitude ~ 11 m perto do Equador.
AVENUE = [("AVENIDA", "", "CABO BRANCO", n, -7.1000, -34.8000 + n * 0.00001, 1) for n in range(100, 301, 10)]
STREET = [("RUA", "MAJOR", "CABOCLO", n, -7.1000 + (n - 50) * 0.00001, -34.7980, 1) for n in range(10, 91, 10)]


class Addresses(unittest.TestCase):
    def test_street_key_ignores_type_accents_and_abbreviations(self):
        self.assertEqual(street_key("AV. PROF. João Antônio"), street_key("AVENIDA PROFESSOR JOAO ANTONIO"))
        self.assertEqual(street_key("R. Cel. Gonzaga"), "CORONEL GONZAGA")

    def test_street_and_number(self):
        self.assertEqual(parse_number("AV. CABO BRANCO, Nº 2300"), ("CABO BRANCO", 2300))
        self.assertEqual(parse_number("ANEL RODOVIÁRIO CELSO MELLO AZEVEDO, N° 1.480"),
                         ("ANEL RODOVIARIO CELSO MELLO AZEVEDO", 1480))
        self.assertEqual(parse_number("ALAMEDA DOS NHAMBIQUARAS (BAIRRO/CENTRO), NUMERO 614"), ("DOS NHAMBIQUARAS", 614))
        self.assertEqual(parse_number("AV. ANTONIO DE GOÉS, EM FRENTE AO N. 124"), ("ANTONIO DE GOES", 124))
        self.assertEqual(parse_number("Rua Posse, 184, Bairro Nossa Sra. de Fatima"), ("POSSE", 184))

    def test_decimals_and_distances_are_not_door_numbers(self):
        # medido em São Paulo: lidos como número 5 ou 7, punham o radar a centenas de metros
        self.assertEqual(parse_number("AV JOAO DIAS (C/B), A MAIS 24,7 METROS DO NUMERO 966"), ("JOAO DIAS", 966))
        self.assertEqual(parse_number("R. ADALGISA CARNEIRO CAVALCANTI, 110 METRO ANTES DO Nº1431"),
                         ("ADALGISA CARNEIRO CAVALCANTI", 1431))
        for local in ("AV JOSÉ PINHEIRO BORGES, A MAIS 147,5 m DO VIADUTO MILTON LEÃO",
                      "AV DAS NACOES UNIDAS, PISTA CENTRAL (INTERLAGOS/C BRANCO), A MENOS 8M DO KM 9,5",
                      "AV. ENG. SANTANA JÚNIOR, 2 METROS APÓS A AV. ANTONIO SALES", "MGC356 km 5,30"):
            self.assertIsNone(parse_number(local), local)

    def test_text_without_a_door_number(self):
        for local in ("GO-469, KM 027+244M", "VIA M2 QNM 20 PROX. CONJ. A CEILANDIA NORTE.",
                      "Av. Laguna, em frente a Congregação Cristã do Brasil, Parque Amazônia"):
            self.assertIsNone(parse_number(local), local)

    def test_crossing(self):
        self.assertEqual(parse_crossing("AV ALM HENRIQUE SABÓIA X RUA CAROLINA SUCUPIRA"),
                         ("ALMIRANTE HENRIQUE SABOIA", "CAROLINA SUCUPIRA"))
        self.assertEqual(parse_crossing("AV. CRUZ DAS ARMAS, ESQUINA COM A R. MAJOR CABOCLO"),
                         ("CRUZ DAS ARMAS", "MAJOR CABOCLO"))
        self.assertEqual(parse_crossing("Av. Goiás X Av. Brasil Norte - Vila Santana"), ("GOIAS", "BRASIL NORTE"))
        self.assertIsNone(parse_crossing("AV. CABO BRANCO, Nº 2300"))


class Register(unittest.TestCase):
    """Leitura do arquivo do município e os dois jeitos de achar o ponto."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.zip = _zip(self.tmp, "2507507_JOAO_PESSOA.zip", [
            *AVENUE, *STREET,
            ("AVENIDA", "", "CABO BRANCO", 999, -7.3, -34.9, 4),        # coordenada da face de quadra: fora
            ("RUA", "", "OUTRA QUALQUER", 5, -7.2, -34.7, 1),
            ("RUA", "", "SEM NUMERO BRANCO", 0, -7.2, -34.7, 1),
        ])

    def test_reads_only_the_streets_that_can_match(self):
        streets = cnefe.read_streets(self.zip, ["CABO BRANCO", "MAJOR CABOCLO"])
        self.assertEqual(sorted(streets), ["CABO BRANCO", "MAJOR CABOCLO"])
        self.assertEqual([p[0] for p in streets["CABO BRANCO"]], list(range(100, 301, 10)))   # sem o 999 (nível 4)

    def test_street_by_the_same_or_a_similar_name(self):
        streets = {"MINISTRO JOSE AMERCO DE ALMEIDA": [], "MINISTRO JOSE AMERICO DE MELO": [], "CABO BRANCO": []}
        self.assertEqual(cnefe.match_street("CABO BRANCO", streets), "CABO BRANCO")
        self.assertEqual(cnefe.match_street("MINISTRO JOSE AMERICO DE ALMEIDA", streets), "MINISTRO JOSE AMERCO DE ALMEIDA")
        self.assertIsNone(cnefe.match_street("PRESIDENTE EPITACIO PESSOA", streets))

    def test_number_exact_interpolated_and_too_far(self):
        pts = cnefe.read_streets(self.zip, ["CABO BRANCO"])["CABO BRANCO"]
        lat, lng = cnefe.locate_number(pts, 200)
        self.assertAlmostEqual(lng, -34.7980, places=6)
        lat, lng = cnefe.locate_number(pts, 205)                      # entre o 200 e o 210
        self.assertAlmostEqual(lng, -34.79795, places=6)
        lat, lng = cnefe.locate_number(pts, 315)                      # 15 depois do último: o último
        self.assertAlmostEqual(lng, -34.7970, places=6)
        self.assertIsNone(cnefe.locate_number(pts, 400))              # o cadastro não tem nada perto do 400
        self.assertEqual(cnefe.MAX_NUMBER_GAP, 20)

    def test_crossing_is_where_the_two_streets_come_closest(self):
        streets = cnefe.read_streets(self.zip, ["CABO BRANCO", "MAJOR CABOCLO"])
        lat, lng = cnefe.crossing_point(streets["CABO BRANCO"], streets["MAJOR CABOCLO"])
        self.assertLess(haversine_m((lat, lng), (-7.1000, -34.7980)), 15)
        far = [(1, -7.2, -34.7)]
        self.assertIsNone(cnefe.crossing_point(streets["CABO BRANCO"], far))

    def test_folder_listing_and_download_once(self):
        html = ('<a href="2507507_JO%c3%83O_PESSOA.zip">2507507_JOÃO_PESSOA.zip</a>'
                '<a href="2504009_CAMPINA_GRANDE.zip">2504009_CAMPINA_GRANDE.zip</a>')
        with mock.patch.object(cnefe, "get_text", return_value=html) as listing:
            files = cnefe.municipality_files("PB")
        self.assertIn("/25_PB/", listing.call_args[0][0])
        self.assertEqual(cnefe.file_for("JOÃO PESSOA", files), "2507507_JO%c3%83O_PESSOA.zip")
        self.assertEqual(cnefe.file_for("Campina Grande", files), "2504009_CAMPINA_GRANDE.zip")
        self.assertIsNone(cnefe.file_for("PATOS", files))
        # o arquivo guarda o nome antigo do município
        self.assertEqual(cnefe.file_for("EMBU DAS ARTES", {"EMBU": "3515004_EMBU.zip"}), "3515004_EMBU.zip")
        self.assertEqual(cnefe.file_for("MOGI MIRIM", {"MOJIMIRIM": "3530805_MOJI-MIRIM.zip"}), "3530805_MOJI-MIRIM.zip")
        raw = tempfile.mkdtemp()
        with mock.patch.object(cnefe, "get_bytes", return_value=b"zip") as get:
            path = cnefe.ensure("PB", "2507507_JO%c3%83O_PESSOA.zip", raw)
            self.assertEqual(cnefe.ensure("PB", "2507507_JO%c3%83O_PESSOA.zip", raw), path)
        self.assertEqual(get.call_count, 1)                           # o cadastro do Censo não muda: baixa uma vez
        self.assertTrue(path.endswith(os.path.join("cnefe", "2507507_JOÃO_PESSOA.zip")))


class Geocode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.zip = _zip(self.tmp, "x.zip", [*AVENUE, *STREET])

    def _run(self, meters, uf="PB", files=None, ensure=None):
        files = {"JOAOPESSOA": "x.zip"} if files is None else files
        with mock.patch.object(cnefe, "municipality_files", return_value=files) as listing, \
                mock.patch.object(cnefe, "ensure", ensure or (lambda uf, href, raw: self.zip)):
            return inmetro_addresses.geocode(meters, uf, self.tmp), listing

    def test_one_camera_per_place_with_the_lowest_speed(self):
        cams, _ = self._run([
            _meter("JOÃO PESSOA", "AV. CABO BRANCO, Nº 200", 60), _meter("JOÃO PESSOA", "AV.  CABO BRANCO, Nº 200", 50),
            _meter("JOÃO PESSOA", "AV. CABO BRANCO, ESQUINA COM A R. MAJOR CABOCLO", 40),
            _meter("JOÃO PESSOA", "AV. CABO BRANCO, EM FRENTE À PADARIA"),             # sem número nem cruzamento
            _meter("JOÃO PESSOA", "AV. CABO BRANCO, Nº 250", valid=False),              # aferição vencida
            _meter("JOÃO PESSOA", "BR-230 KM 12", road=("BR", 230)),                    # rodovia + km: outro caminho
        ])
        self.assertEqual([(c.source, c.kind, c.active) for c in cams], [("INMETRO", CameraKind.FIXED, True)] * 2)
        self.assertEqual(sorted(c.limit_kmh for c in cams), [40, 50])
        for c in cams:
            self.assertLess(haversine_m((c.lat, c.lng), (-7.1000, -34.7980)), 15)

    def test_number_that_cannot_be_located_falls_back_to_the_crossing(self):
        cams, _ = self._run([_meter("JOÃO PESSOA", "AV. CABO BRANCO X R. MAJOR CABOCLO, N 900")])
        self.assertEqual(len(cams), 1)

    def test_city_without_a_file_is_a_warning_and_a_failed_download_a_failure(self):
        f0, w0 = len(failures.recorded()), len(failures.warnings())
        cams, _ = self._run([_meter("PATOS", "RUA TAL, Nº 10")])
        self.assertEqual((cams, len(failures.warnings()) - w0, len(failures.recorded()) - f0), ([], 1, 0))

        def boom(uf, href, raw):
            raise ConnectionError("fora do ar")
        cams, _ = self._run([_meter("JOÃO PESSOA", "AV. CABO BRANCO, Nº 200")], ensure=boom)
        self.assertEqual((cams, len(failures.recorded()) - f0), ([], 1))
        self.assertIn("CNEFE JOÃO PESSOA/PB", failures.recorded()[-1])

    def test_brasilia_comes_in_the_goias_file_and_its_addresses_in_the_df_folder(self):
        _, listing = self._run([_meter("BRASÍLIA", "AV. CABO BRANCO, Nº 200", uf="GO")], uf="GO", files={})
        listing.assert_called_once_with("DF")

    def test_city_with_nothing_addressable_downloads_nothing(self):
        _, listing = self._run([_meter("JOÃO PESSOA", "PRÓXIMO AO SHOPPING")])
        listing.assert_not_called()


class Apply(unittest.TestCase):
    """Perto de um radar conhecido, só confirma; longe de todos, radar novo."""
    P = Camera(-7.1000, -34.8000, CameraKind.FIXED, 50, "INMETRO", True)

    def _at(self, metres_east, **kw):
        return Camera(-7.1000, -34.8000 + metres_east / 110_000, **kw)

    def test_known_radar_nearby_keeps_its_position_and_is_confirmed(self):
        osm = self._at(60, source="OSM")                                  # a ~60 m, sem limite
        cams, st = inmetro_addresses.apply([osm], [self.P])
        self.assertEqual(cams, [Camera(osm.lat, osm.lng, CameraKind.FIXED, 50, "INMETRO+OSM", True)])
        self.assertEqual(st, {"confirmed": 1, "limits_filled": 1, "reactivated": 0, "new": 0})

    def test_known_limit_is_kept(self):
        cams, st = inmetro_addresses.apply([self._at(10, source="CET-SP", limit_kmh=40)], [self.P])
        self.assertEqual((cams[0].limit_kmh, cams[0].source, st["limits_filled"]), (40, "CET-SP+INMETRO", 0))

    def test_far_from_every_radar_is_a_new_one(self):
        cams, st = inmetro_addresses.apply([self._at(150, source="OSM")], [self.P])
        self.assertEqual((len(cams), cams[-1], st["new"]), (2, self.P, 1))

    def test_red_light_camera_is_another_device(self):
        cams, st = inmetro_addresses.apply([self._at(10, source="OSM", kind=CameraKind.RED_LIGHT)], [self.P])
        self.assertEqual((len(cams), st["new"], st["confirmed"]), (2, 1, 0))

    def test_active_radar_is_preferred_and_only_sources_without_own_status_come_back(self):
        dead_osm = self._at(10, source="OSM", active=False)
        alive = self._at(80, source="OSM")
        cams, st = inmetro_addresses.apply([dead_osm, alive], [self.P])
        self.assertEqual(([c.source for c in cams], [c.active for c in cams]), (["OSM", "INMETRO+OSM"], [False, True]))
        cams, st = inmetro_addresses.apply([dead_osm], [self.P])          # só o inativo por perto: volta a ativo
        self.assertEqual((cams[0].active, st["reactivated"]), (True, 1))
        cancelled = self._at(10, source="DER-SP", active=False)           # o órgão disse que cancelou: fica
        cams, st = inmetro_addresses.apply([cancelled], [self.P])
        self.assertEqual((cams[0].active, cams[0].source, st["reactivated"]), (False, "DER-SP+INMETRO", 0))

    def test_two_places_at_the_same_spot_are_one_new_radar(self):
        other = Camera(self.P.lat, self.P.lng + 10 / 110_000, CameraKind.FIXED, 40, "INMETRO", True)
        cams, st = inmetro_addresses.apply([], [self.P, other])
        self.assertEqual((len(cams), cams[0].limit_kmh, st["new"]), (1, 40, 1))


class ContextPoints(unittest.TestCase):
    def test_df_uses_the_goias_file_and_each_file_is_geocoded_once(self):
        from datakit.context import BuildContext
        ctx = BuildContext(tempfile.mkdtemp())
        ctx._national = {"meter_list": [_meter("BRASÍLIA", "X", uf="GO"), _meter("RECIFE", "Y", uf="PE")]}
        seen = []

        def fake(meters, uf, raw_dir):
            seen.append((uf, [m.municipality for m in meters]))
            return [Camera(-15.8, -47.9, source="INMETRO")]
        with mock.patch.object(inmetro_addresses, "geocode", fake):
            self.assertEqual(len(ctx.inmetro_points("DF")), 1)
            ctx.inmetro_points("GO")
            ctx.inmetro_points("PE")
        self.assertEqual(seen, [("GO", ["BRASÍLIA"]), ("PE", ["RECIFE"])])


if __name__ == "__main__":
    unittest.main()
