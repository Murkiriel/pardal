"""Testes puros das fontes de 2026-09-29 (Inmetro, SNV, placas ANTT, Rio, BH, DF).

    python -m unittest discover -s datakit/tests -t .
"""
import unittest
from datetime import date

from datakit.common import Camera, CameraKind, Limit
from datakit.common.geo import haversine_m, utm_to_latlng
from datakit.common.lrs import MeasuredLine, SnvRoutes, in_ranges, parse_km, parse_road, snv_key
from datakit.common.model import override_limits
from datakit import inmetro_status
from datakit.sources import antt_placas, bh, df_detran, inmetro, rio
from datakit.sources import cet_sp, der_go, der_sp, dnit, osm_pbf
from datakit.common.model import (absorb_osm, collapse_osm, deactivate_near, join_sources, merge_cameras,
                                  merge_cross_agency, merge_limits, source_parts)
from datakit.common.direction import direction_on, hint_from_text, hint_target, orient_to_hint, parse_increasing


def _line():
    # reta norte-sul de ~11 km, km 0 no sul
    return MeasuredLine([(-16.10, -49.0), (-16.05, -49.0), (-16.00, -49.0)], [0.0, 5.56, 11.12])


class Lrs(unittest.TestCase):
    def test_parse_road_and_km(self):
        self.assertEqual(parse_road("BR 060 KM  181,000"), ("BR", 60))
        self.assertEqual(parse_road("GO-469, KM 027+244M"), ("GO", 469))
        self.assertIsNone(parse_road("AV. ANHANGUERA, 1000"))
        self.assertAlmostEqual(parse_km("GO-469, KM 027+244M"), 27.244)
        self.assertAlmostEqual(parse_km("BR 060 KM  181,000"), 181.0)
        self.assertAlmostEqual(parse_km("km 12,5"), 12.5)
        self.assertIsNone(parse_km("RUA 7"))

    def test_snv_key_only_main_axis(self):
        self.assertEqual(snv_key("010BDF202607A"), (10, "DF"))
        self.assertIsNone(snv_key("010CTO10202607A"))

    def test_measured_line_at_and_project_roundtrip(self):
        line = _line()
        p = line.at(2.78)
        self.assertAlmostEqual(p[0], -16.075, places=3)
        km, d = line.project((p[0], p[1] + 0.0002))  # ~21 m ao lado
        self.assertAlmostEqual(km, 2.78, places=2)
        self.assertLess(d, 30)
        self.assertIsNone(line.at(20.0))

    def test_nearest_any_needs_same_uf_and_distance(self):
        snv = SnvRoutes({(60, "GO"): [_line()]})
        self.assertEqual(snv.nearest_any("GO", (-16.05, -49.0003), 60)[0], 60)
        self.assertIsNone(snv.nearest_any("MG", (-16.05, -49.0003), 60))
        self.assertIsNone(snv.nearest_any("GO", (-16.05, -49.01), 60))  # ~1 km fora

    def test_in_ranges(self):
        self.assertTrue(in_ranges([(10.0, 20.0)], 15.0))
        self.assertFalse(in_ranges([(10.0, 20.0)], 25.0))


class Inmetro(unittest.TestCase):
    REC = {"SiglaUf": "GO", "Municipio": "ABADIA DE GOIÁS", "LocalVerificacao": "BR 060 KM  181,000",
           "TipoMedidor": "Fixo", "DataValidade": "20/08/2027", "UltimoResultado": "Aprovado",
           "Faixas": [{"VelocidadeNominal": "80"}, {"VelocidadeNominal": "60"}]}

    def test_parse_valid_federal_meter(self):
        m = inmetro.parse([self.REC], "GO", date(2026, 9, 29))[0]
        self.assertTrue(m.fixed and m.valid)
        self.assertEqual((m.road, m.km, m.limit_kmh), (("BR", 60), 181.0, 60))

    def test_expired_or_reproved_is_not_valid(self):
        old = dict(self.REC, DataValidade="01/01/2026")
        bad = dict(self.REC, UltimoResultado="Reprovado")
        for r in (old, bad):
            self.assertFalse(inmetro.parse([r], "GO", date(2026, 9, 29))[0].valid)

    def test_decide(self):
        d = inmetro_status.decide
        self.assertTrue(d([(10.0, True), (10.2, False)], []))
        self.assertFalse(d([(10.0, False)], [(10.0, False), (12.5, False)]))
        self.assertIsNone(d([(10.0, False)], [(10.0, False), (12.0, True)]))  # válido perto: não desliga
        self.assertIsNone(d([], []))

    def test_apply_marks_by_km_skips_concessions_and_sources_with_status(self):
        snv = SnvRoutes({(60, "GO"): [_line()]})
        def at(km):
            return _line().at(km)
        cams = [
            Camera(*at(2.0), CameraKind.FIXED, None, "OSM", True),     # medidor vencido perto
            Camera(*at(8.0), CameraKind.FIXED, None, "DNIT", True),    # medidor válido perto
            Camera(*at(2.1), CameraKind.FIXED, None, "ANTT", True),    # tem situação própria
        ]
        idx = {(60, "GO"): [(2.3, False), (8.4, True)]}
        out, st = inmetro_status.apply(cams, "GO", idx, snv, {})
        self.assertEqual([c.active for c in out], [False, True, True])
        self.assertEqual((st["deactivated"], st["confirmed"]), (1, 1))
        out, _ = inmetro_status.apply(cams, "GO", idx, snv, {(60, "GO"): [(0.0, 11.2)]})
        self.assertTrue(all(c.active for c in out))  # trecho concedido: km não vale

    def test_eligible_only_if_every_official_source_lacks_status(self):
        """ANTT e DER-SP já dizem a situação; juntar o radar com o OSM não pode passar a
        situação para o Inmetro."""
        e = inmetro_status.eligible
        self.assertFalse(e("ANTT+OSM"))
        self.assertFalse(e("DER-SP+OSM"))
        self.assertFalse(e("DNIT+ANTT"))
        self.assertTrue(e("DNIT+OSM"))
        self.assertTrue(e("OSM"))
        self.assertTrue(e("DNIT"))
        self.assertTrue(e("DETRAN-DF+DNIT"))

    def test_apply_leaves_antt_osm_alone(self):
        from datakit.common.lrs import MeasuredLine
        snv = SnvRoutes({(60, "GO"): [MeasuredLine([(-16.0, -49.0), (-16.1, -49.0)], [0.0, 11.1])]})
        idx = {(60, "GO"): [(5.55, False)]}
        cam = Camera(-16.05, -49.0, CameraKind.FIXED, 80, "ANTT+OSM", True)
        out, st = inmetro_status.apply([cam], "GO", idx, snv, {})
        self.assertTrue(out[0].active)
        self.assertEqual(st["deactivated"], 0)


class AnttSigns(unittest.TestCase):
    KML = """<Placemark id="1"><description><![CDATA[<table>
      <tr><td>rodovia</td><td>BR-060</td></tr><tr><td>uf</td><td>GO</td></tr>
      <tr><td>sentido</td><td>{s}</td></tr><tr><td>latitude</td><td>{lat}</td></tr>
      <tr><td>longitude</td><td>-49,0</td></tr><tr><td>velocidade</td><td>{v}</td></tr>
      <tr><td>situacao</td><td>{sit}</td></tr></table>]]></description></Placemark>"""

    def _kml(self, *signs):
        return "".join(self.KML.format(s=s, lat=str(lat).replace(".", ","), v=v, sit=sit) for s, lat, v, sit in signs)

    def test_parse_keeps_only_active(self):
        signs = antt_placas.parse_kml(self._kml(("Crescente", -16.09, 80, "Ativo"), ("Crescente", -16.08, 60, "Inativo")))
        self.assertEqual(len(signs), 1)
        self.assertEqual((signs[0].br, signs[0].uf, signs[0].increasing, signs[0].kmh), (60, "GO", True, 80))

    def test_sign_holds_until_next_same_direction(self):
        snv = SnvRoutes({(60, "GO"): [_line()]})
        signs = antt_placas.parse_kml(self._kml(("Crescente", -16.09, 80, "Ativo"), ("Crescente", -16.06, 60, "Ativo")))
        lims = antt_placas.limits_from_signs(signs, snv)
        by_lat = sorted(lims, key=lambda x: x.lat)
        self.assertEqual({x.limit_kmh for x in by_lat if x.lat < -16.062}, {80})
        self.assertEqual({x.limit_kmh for x in by_lat if x.lat > -16.059}, {60})
        self.assertTrue(all(x.source == "ANTT" for x in lims))

    def test_directions_that_disagree_give_one_point_per_direction(self):
        snv = SnvRoutes({(60, "GO"): [_line()]})
        signs = antt_placas.parse_kml(self._kml(("Crescente", -16.09, 80, "Ativo"), ("Decrescente", -16.05, 60, "Ativo")))
        lims = antt_placas.limits_from_signs(signs, snv)
        # entre as duas placas os dois sentidos valem e discordam: 80 rumo norte (km crescente), 60 rumo sul
        between = [x for x in lims if -16.085 < x.lat < -16.055]
        self.assertEqual({(x.limit_kmh, x.direction_deg) for x in between}, {(80, 0), (60, 180)})
        # ao sul da placa crescente só vale a decrescente: só um sentido tem placa -> ponto com sentido
        south = [x for x in lims if x.lat < -16.095]
        self.assertTrue(south and all(x.direction_deg == 180 and x.limit_kmh == 60 for x in south))

    def test_same_value_both_ways_is_one_point_without_direction(self):
        snv = SnvRoutes({(60, "GO"): [_line()]})
        signs = antt_placas.parse_kml(self._kml(("Crescente", -16.09, 80, "Ativo"), ("Decrescente", -16.05, 80, "Ativo")))
        between = [x for x in antt_placas.limits_from_signs(signs, snv) if -16.085 < x.lat < -16.055]
        self.assertTrue(between and all(x.direction_deg is None for x in between))
        self.assertEqual(len(between), len({(x.lat, x.lng) for x in between}))


class Direction(unittest.TestCase):
    def test_bearing_along_increasing_km(self):
        line = _line()
        self.assertAlmostEqual(line.bearing_at(3.0), 0.0, delta=0.5)
        self.assertEqual(direction_on(line, 3.0, True), 0)
        self.assertEqual(direction_on(line, 3.0, False), 180)
        self.assertEqual((parse_increasing("Crescente"), parse_increasing(" decrescente "), parse_increasing("Crescente/Decrescente")),
                         (True, False, None))

    def test_line_from_extent_drawn_backwards(self):
        # desenhada de norte para sul com km 10 -> 0: o km cresce para o norte
        line = MeasuredLine.from_extent([(-16.00, -49.0), (-16.05, -49.0)], 10.0, 4.44)
        self.assertLess(line.ms[0], line.ms[-1])
        self.assertEqual(direction_on(line, 7.0, True), 0)
        self.assertEqual(direction_on(line, 7.0, False), 180)

    def test_der_go_direction_uses_printed_km_to_confirm_drawing(self):
        # trecho desenhado do km 0 (sul) ao km 5,56 (norte); radar a leste do eixo no km ~2,2
        net = {"060EGO0010": [MeasuredLine.from_extent([(-16.10, -49.0), (-16.05, -49.0)], 0.0, 5.56)]}
        self.assertEqual(der_go.direction(net, "060EGO0010", "Crescente", "KM 2 + 220M", -16.08, -48.9997), 0)
        self.assertEqual(der_go.direction(net, "060EGO0010", "Decrescente", "KM 2 + 220M", -16.08, -48.9997), 180)
        # km impresso do outro lado (km 3,34 = espelho de 2,22): o trecho foi desenhado ao contrário
        self.assertEqual(der_go.direction(net, "060EGO0010", "Crescente", "KM 3 + 340M", -16.08, -48.9997), 180)
        self.assertIsNone(der_go.direction(net, "060EGO0010", "Crescente/Decrescente", "", -16.08, -48.9997))
        self.assertIsNone(der_go.direction(net, "999EGO0010", "Crescente", "", -16.08, -48.9997))

    def test_osm_direction_tag(self):
        self.assertEqual(osm_pbf._cam_direction({"direction": "90"}), 90)
        self.assertEqual(osm_pbf._cam_direction({"direction": "360"}), 0)
        self.assertEqual(osm_pbf._cam_direction({"direction": "SW"}), 225)
        self.assertEqual(osm_pbf._cam_direction({"direction": "nne"}), 22)
        for v in ("forward", "backward", "both", "", "500"):
            self.assertIsNone(osm_pbf._cam_direction({"direction": v}))


class NominalDirection(unittest.TestCase):
    def test_dnit_lanes(self):
        self.assertEqual([dnit.lanes_increasing(t) for t in ("P-C-1, P-C-2", "P-D-1", "P-C-1, P-D-1", None)],
                         [True, False, None, None])

    def test_hint_text_and_lanes(self):
        self.assertEqual([hint_from_text(t) for t in ("Norte", "Sul (Marginal)", "leste", "Oeste", "Interno", None)],
                         ["N", "S", "L", "O", None, None])
        self.assertEqual([der_sp.lanes_hint(t) for t in ("O-1", "O-1, O-2", "N-1, S-1", None)], ["O", "O", None, None])

    def test_orient_two_way_road_to_the_nominal_direction(self):
        self.assertEqual(orient_to_hint(10.0, False, "S"), 190.0)
        self.assertEqual(orient_to_hint(10.0, False, "N"), 10.0)
        self.assertIsNone(orient_to_hint(90.0, False, "N"))      # via perpendicular ao nominal: ambíguo
        self.assertEqual(orient_to_hint(100.0, True, "L"), 100.0)  # mão única no sentido nominal
        self.assertIsNone(orient_to_hint(280.0, True, "L"))      # a outra pista

    def _probe(self, hint, ways, **kw):
        cam = Camera(-16.0, -49.0001, heading_hint=hint)
        found = {}
        for pts, oneway in ways:
            osm_pbf._probe_way(osm_pbf._probe_grid([cam]), pts, oneway, found)
        return osm_pbf.resolve_probes(found, **kw).get((-16.0, -49.0001, hint))

    NORTH_LANE = ([(-16.01, -49.0), (-15.99, -49.0)], True)        # ~11 m a leste do radar, rumo norte
    SOUTH_LANE = ([(-15.99, -49.0003), (-16.01, -49.0003)], True)  # ~21 m a oeste, rumo sul
    TWO_WAY = ([(-16.01, -49.0), (-15.99, -49.0)], False)

    def test_position_on_a_one_way_carriageway_decides(self):
        self.assertAlmostEqual(self._probe("N", [self.NORTH_LANE, self.SOUTH_LANE]), 0.0, delta=0.5)
        self.assertIsNone(self._probe("S", [self.NORTH_LANE, self.SOUTH_LANE]))      # nominal contraria a posição
        self.assertAlmostEqual(self._probe("*", [self.NORTH_LANE, self.SOUTH_LANE]), 0.0, delta=0.5)

    def test_carriageways_too_close_or_two_way_road_give_nothing(self):
        near_south = ([(-15.99, -49.00015), (-16.01, -49.00015)], True)            # ~5 m: ambíguo
        self.assertIsNone(self._probe("N", [self.NORTH_LANE, near_south]))
        self.assertIsNone(self._probe("S", [self.TWO_WAY]))
        self.assertIsNone(self._probe("*", [self.TWO_WAY]))
        # com position_confirms=False, o nominal vale em via de mão dupla
        self.assertAlmostEqual(self._probe("S", [self.TWO_WAY], position_confirms=False), 180.0, delta=0.5)

    def test_far_roads_are_ignored(self):
        self.assertIsNone(self._probe("*", [([(-16.01, -49.01), (-15.99, -49.01)], True)]))

    def test_forward_backward_take_the_way_direction(self):
        class Loc:
            def __init__(self, lat, lon): self.lat, self.lon = lat, lon
            def valid(self): return True

        class Node:
            def __init__(self, ref, lat, lon): self.ref, self.location = ref, Loc(lat, lon)

        class Way:
            nodes = [Node(1, -16.02, -49.0), Node(2, -16.01, -49.0), Node(3, -16.0, -49.0)]

        cams = [Camera(-16.01, -49.0, source="OSM"), Camera(-16.01, -49.0, source="OSM")]
        osm_pbf._apply_way_direction(Way(), {2: (0, True)}, cams)
        osm_pbf._apply_way_direction(Way(), {2: (1, False)}, cams)
        self.assertEqual([c.direction_deg for c in cams], [0, 180])


class CetCameras(unittest.TestCase):
    ROWS = [
        {"CÓDIGO LOCAL": "1", "LATITUDE": -23.6798, "LONGITUDE": "-46.6868", "DESCRIÇÃO DO LOCAL":
         "AV. INTERLAGOS (CENTRO/BAIRRO) A MAIS 11 METROS DA R. X", "ENQUADRAMENTOS": "F,R,V,Z",
         "VELOCIDADE": "50 km/h", "DESATIVAÇÃO": None},
        {"CÓDIGO LOCAL": "2", "LATITUDE": -23.60, "LONGITUDE": -46.66, "DESCRIÇÃO DO LOCAL": "AV Y (RAPOSO/MARGINAL)",
         "ENQUADRAMENTOS": "V,A,P", "VELOCIDADE": "90/60 km/h", "DESATIVAÇÃO": None},
        {"CÓDIGO LOCAL": "3", "LATITUDE": -23.61, "LONGITUDE": -46.67, "DESCRIÇÃO DO LOCAL": "R Z (B/C)",
         "ENQUADRAMENTOS": "A,P", "VELOCIDADE": "", "DESATIVAÇÃO": None},
        {"CÓDIGO LOCAL": "4", "LATITUDE": -23.62, "LONGITUDE": -46.68, "DESCRIÇÃO DO LOCAL": "R W",
         "ENQUADRAMENTOS": "R,EXE", "VELOCIDADE": "50 km/h", "DESATIVAÇÃO": None},
        {"CÓDIGO LOCAL": "5", "LATITUDE": -23.63, "LONGITUDE": -46.69, "DESCRIÇÃO DO LOCAL": "R V",
         "ENQUADRAMENTOS": "V", "VELOCIDADE": "40 km/h", "DESATIVAÇÃO": 1711324800000},
    ]

    def test_only_active_speed_and_red_light(self):
        cams = cet_sp.rows_to_cameras(self.ROWS)
        self.assertEqual([(c.kind, c.limit_kmh) for c in cams],
                         [(CameraKind.FIXED, 50), (CameraKind.FIXED, 90), (CameraKind.RED_LIGHT, None)])
        self.assertTrue(all(c.source == "CET-SP" for c in cams))
        self.assertAlmostEqual(cams[0].lng, -46.6868)

    def test_center_suburb_becomes_a_bearing_hint(self):
        cams = cet_sp.rows_to_cameras(self.ROWS)
        # Interlagos fica ao sul-sudoeste do marco zero: centro->bairro aponta para lá
        out = float(cams[0].heading_hint[1:])
        self.assertTrue(180 < out < 230, out)
        self.assertEqual(cams[1].heading_hint, "*")                # par de lugares: só pela posição
        back = float(cams[2].heading_hint[1:])                     # bairro->centro: volta ao marco zero
        self.assertTrue(0 <= back < 90 or back > 330, back)
        self.assertIsNone(cet_sp.center_suburb_hint("R X (CENTRO/BAIRRO)", -23.552, -46.635))  # < 1,5 km
        self.assertEqual(hint_target("@203"), 203.0)
        self.assertEqual(hint_target("L"), 90.0)


class PowerBi(unittest.TestCase):
    def test_decode_repeats_nulls_and_dictionaries(self):
        from datakit.sources import _powerbi
        resp = {"results": [{"result": {"data": {"dsr": {"DS": [{"IC": True, "ValueDicts": {"D0": ["A", "B"]},
            "PH": [{"DM0": [
                {"S": [{"N": "G0", "T": 1}, {"N": "G1", "T": 1, "DN": "D0"}, {"N": "G2", "T": 3}], "C": [1, 0, 2.5]},
                {"C": [2, 1], "Ø": 4},           # G2 nulo
                {"C": [3], "R": 6},              # G1 e G2 repetem a anterior
            ]}]}]}}}}]}
        self.assertEqual(_powerbi.decode(resp), [[1, "A", 2.5], [2, "B", None], [3, "B", None]])
        resp["results"][0]["result"]["data"]["dsr"]["DS"][0]["RT"] = [["x"]]
        with self.assertRaises(RuntimeError):
            _powerbi.decode(resp)

    def test_resource_key_from_the_view_link(self):
        from datakit.sources import _powerbi
        self.assertEqual(_powerbi.resource_key(cet_sp.PBI_VIEW), "d8b9566c-25e1-4f66-a54b-ae3b02bda6e7")


class Absorb(unittest.TestCase):
    def test_osm_duplicate_folds_into_the_official_one(self):
        off = [Camera(-16.0, -49.0, source="DNIT", limit_kmh=None, direction_deg=90)]
        osm = [Camera(-16.0001, -49.0001, source="OSM", limit_kmh=60),        # ~15 m: mesmo radar
               Camera(-16.001, -49.0, source="OSM", limit_kmh=80)]            # ~110 m: outro
        o, rest, n = absorb_osm(off, osm)
        self.assertEqual(n, 1)
        self.assertEqual((o[0].lat, o[0].source, o[0].limit_kmh, o[0].direction_deg), (-16.0, "DNIT+OSM", 60, 90))
        self.assertEqual([c.limit_kmh for c in rest], [80])

    def test_red_light_next_to_a_speed_camera_stays_separate(self):
        off = [Camera(-16.0, -49.0, CameraKind.FIXED, 50, source="CET-SP")]
        osm = [Camera(-16.0001, -49.0, CameraKind.RED_LIGHT, source="OSM")]
        _, rest, n = absorb_osm(off, osm)
        self.assertEqual((n, len(rest)), (0, 1))

    def test_section_end_and_active_official_preferred(self):
        off = [Camera(-16.0, -49.0, source="DER-SP", active=False),
               Camera(-16.0002, -49.0, source="DER-SP", limit_kmh=100)]
        osm = [Camera(-16.0001, -49.0, CameraKind.SECTION, 100, "OSM", end_lat=-16.05, end_lng=-49.0)]
        o, rest, n = absorb_osm(off, osm)
        self.assertEqual((n, rest), (1, []))
        self.assertEqual(o[0], off[0])                        # o inativo não levou nada
        self.assertEqual((o[1].kind, o[1].end_lat), (CameraKind.SECTION, -16.05))

    def test_official_cameras_never_merge_with_each_other(self):
        off = [Camera(-16.0, -49.0, source="ANTT", direction_deg=0), Camera(-16.0001, -49.0, source="ANTT", direction_deg=180)]
        o, _, _ = absorb_osm(off, [])
        self.assertEqual(o, off)

    def test_same_radar_from_two_agencies_becomes_one(self):
        dnit = Camera(-15.80, -47.90, source="DNIT")
        df = Camera(-15.80020, -47.90, source="DETRAN-DF", limit_kmh=60, direction_deg=180, active=False)
        other_df = Camera(-15.80005, -47.90, source="DETRAN-DF")          # mesmo órgão: fica
        out, n = merge_cross_agency([dnit, df, other_df])
        self.assertEqual(n, 1)
        merged = next(c for c in out if "+" in c.source)
        self.assertEqual((merged.source, merged.lat, merged.limit_kmh, merged.direction_deg, merged.active),
                         ("DNIT+DETRAN-DF", -15.80020, 60, 180, True))     # posição de quem tem sentido
        self.assertEqual(len(out), 2)

    def test_opposite_directions_from_two_agencies_stay_apart(self):
        a = Camera(-16.0, -49.0, source="ANTT", direction_deg=0)
        b = Camera(-16.0001, -49.0, source="DNIT", direction_deg=180)
        self.assertEqual(merge_cross_agency([a, b])[1], 0)

    def test_osm_points_a_few_metres_apart_collapse(self):
        lanes = [Camera(-16.0, -49.0, source="OSM"), Camera(-16.00003, -49.0, source="OSM", limit_kmh=80)]  # ~3 m
        other_carriageway = Camera(-16.0002, -49.0, source="OSM")                                            # ~22 m
        red = Camera(-16.00002, -49.0, CameraKind.RED_LIGHT, source="OSM")
        out, n = collapse_osm(lanes + [other_carriageway, red])
        self.assertEqual(n, 1)
        self.assertEqual(sorted((c.kind.value, c.limit_kmh or 0) for c in out),
                         [("FIXED", 0), ("FIXED", 80), ("RED_LIGHT", 0)])

    def test_source_helpers(self):
        self.assertEqual(join_sources("OSM", "DNIT"), "DNIT+OSM")
        self.assertEqual(join_sources("DNIT+OSM", "DETRAN-DF"), "DNIT+DETRAN-DF+OSM")
        self.assertEqual(source_parts("DNIT+OSM"), ["DNIT", "OSM"])

    def test_osm_at_a_deactivated_site_becomes_inactive(self):
        osm = [Camera(-23.60, -46.66, source="OSM"), Camera(-23.61, -46.66, source="OSM"), Camera(-23.62, -46.66, source="OSM")]
        dead = [(-23.60005, -46.66), (-23.61005, -46.66)]
        alive = [Camera(-23.61010, -46.66, source="CET-SP")]  # o segundo local foi reativado ao lado
        out, n = deactivate_near(osm, dead, alive)
        self.assertEqual(([c.active for c in out], n), ([False, True, True], 1))

    def test_cet_deactivated_rows(self):
        rows = CetCameras.ROWS
        self.assertEqual(cet_sp.rows_to_deactivated(rows), [(-23.63, -46.69)])


class MergeDirections(unittest.TestCase):
    def test_camera_keeps_known_direction_and_opposite_ones_mean_both(self):
        a = Camera(-16.0, -49.0, source="ANTT", limit_kmh=80, direction_deg=10)
        b = Camera(-16.0, -49.0, source="OSM")
        self.assertEqual(merge_cameras([b], [a])[0].direction_deg, 10)
        self.assertEqual(merge_cameras([a], [b])[0].direction_deg, 10)
        c = Camera(-16.0, -49.0, source="OSM", direction_deg=190)
        self.assertIsNone(merge_cameras([a], [c])[0].direction_deg)

    def test_limits_of_each_direction_at_one_point_are_both_kept(self):
        up = Limit(-16.0, -49.0, 80, "ANTT", direction_deg=0)
        down = Limit(-16.0, -49.0, 60, "ANTT", direction_deg=180)
        self.assertEqual(len(merge_limits([up, down])), 2)
        rows = [Limit.from_row(dict(zip(Limit.HEADER, x.row()))) for x in (up, down)]
        self.assertEqual([x.direction_deg for x in rows], [0, 180])

    def test_one_way_official_sign_leaves_osm_for_the_other_way(self):
        osm = [Limit(-16.0002, -49.0, 60, "OSM")]
        self.assertEqual(len(override_limits([Limit(-16.0, -49.0, 80, "ANTT", direction_deg=0)], osm)), 2)
        both = [Limit(-16.0, -49.0, 80, "ANTT", direction_deg=0), Limit(-16.0, -49.0, 60, "ANTT", direction_deg=180)]
        self.assertEqual([x.source for x in override_limits(both, osm)], ["ANTT", "ANTT"])


class Override(unittest.TestCase):
    def test_official_removes_nearby_osm_only(self):
        off = [Limit(-16.0, -49.0, 80, "ANTT")]
        osm = [Limit(-16.0002, -49.0, 60, "OSM"), Limit(-16.01, -49.0, 60, "OSM")]
        out = override_limits(off, osm, 50.0)
        self.assertEqual([x.source for x in out], ["ANTT", "OSM"])
        self.assertAlmostEqual(out[1].lat, -16.01)


class Rio(unittest.TestCase):
    LINES = [
        "Sexta-feira, 11 de setembro de 2026", "Lombada Eletrônica",
        "Pontos Fiscalizados (CODCET) Velocidade", "Permitida",
        "AVENIDA X,PROXIMO AO TERMINAL - SENTIDO", "AVENIDA Y  (053001111) 40 24 HORAS",
        "AVENIDA AREIA BRANCA,PROXIMO AO Nº 1672 - SENTIDO SEPETIBA  (053079112) 50 6H AS 22H",
        "21H, SABADO 6H",
        "Avanço de Sinal Vermelho / Parada Sobre Faixa de Pedestre",
        "RUA Z,PROXIMO AO Nº 10 - SENTIDO W  (060001111) 24 HORAS",
        "Invasão de Faixa Exclusiva",
        "RUA K,PROXIMO AO Nº 5 - SENTIDO J  (070001111) 24 HORAS",
    ]

    def test_parse_list_sections_joins_and_speed(self):
        items = rio.parse_list(self.LINES)
        self.assertEqual([(k, v) for _, k, v in items],
                         [(CameraKind.FIXED, 40), (CameraKind.FIXED, 50), (CameraKind.RED_LIGHT, None)])
        self.assertTrue(items[0][0].startswith("AVENIDA X,PROXIMO AO TERMINAL - SENTIDO AVENIDA Y"))

    def test_geocode_query_needs_house_number(self):
        self.assertEqual(rio.geocode_query("AVENIDA AREIA BRANCA,PROXIMO AO Nº 1672 - SENTIDO X"),
                         "AVENIDA AREIA BRANCA 1672")
        self.assertIsNone(rio.geocode_query("AVENIDA X,PROXIMO AO TERMINAL - SENTIDO Y"))

    def test_segment_points(self):
        pts = rio.segment_points([[[-43.2, -22.9], [-43.19, -22.9]]], 40)
        self.assertGreaterEqual(len(pts), 2)
        self.assertTrue(all(p.limit_kmh == 40 and p.source == "RIO" for p in pts))
        for a, b in zip(pts, pts[1:]):
            self.assertLessEqual(haversine_m((a.lat, a.lng), (b.lat, b.lng)), 160)


class BhDf(unittest.TestCase):
    def test_utm_23s(self):
        lat, lng = utm_to_latlng(612616.59, 7796867.80, 23)
        self.assertAlmostEqual(lat, -19.9213, places=3)
        self.assertAlmostEqual(lng, -43.9240, places=3)

    def test_bh_kinds(self):
        csv = ("ID;DESC_LOC_CONTROLADOR_TRANSITO;DESC_TIPO_CONTROLADOR_TRANSITO;VELOCIDADE_REGULAMENTAR;GEOMETRIA\n"
               "1;x;Controlador Eletrônico de Velocidade;60;POINT (612616.59 7796867.80)\n"
               "2;x;Detector de Avanço de Semáforo;60;POINT (612616.59 7796867.80)\n"
               "3;x;Detector de Invasão de Faixa de Exclusiva - MOVE;60;POINT (612616.59 7796867.80)\n")
        cams = bh.parse(csv)
        self.assertEqual([(c.kind, c.limit_kmh) for c in cams],
                         [(CameraKind.FIXED, 60), (CameraKind.RED_LIGHT, None)])

    def test_df_speed_text(self):
        cams = df_detran.parse([{"geometry": {"x": -47.77, "y": -15.9}, "attributes": {"Velocidade": "30 kmh@30"}},
                                {"geometry": {"x": -47.77, "y": -15.9}, "attributes": {"Velocidade": None}}])
        self.assertEqual([c.limit_kmh for c in cams], [30, None])


class Municipal(unittest.TestCase):
    def test_failing_city_is_registered_and_the_others_still_load(self):
        from unittest import mock
        from datakit import failures
        from datakit.sources import municipal

        def boom(bbox):
            raise ConnectionError("fora do ar")

        ok = [Camera(-8.05, -34.9, CameraKind.FIXED, 60, "PCR", True)]
        before = list(failures._FAILURES)
        try:
            with mock.patch.object(municipal, "_pmjp", boom), \
                    mock.patch.object(municipal, "_fortaleza", lambda bbox: []), \
                    mock.patch.object(municipal, "_recife", lambda bbox: ok):
                cams, _ = municipal.load("")
            self.assertEqual(cams, ok)
            self.assertTrue(any("João Pessoa" in f and "ConnectionError" in f for f in failures.recorded()), failures.recorded())
        finally:
            failures._FAILURES[:] = before


class Formats(unittest.TestCase):
    ROWS = [
        {"lat": "-16.0", "lng": "-49.0", "kind": "FIXED", "limit_kmh": "60", "source": "DNIT", "active": "1",
         "end_lat": "", "end_lng": ""},
        {"lat": "-16.1", "lng": "-49.1", "kind": "RED_LIGHT", "limit_kmh": "", "source": "R&D <x>", "active": "1",
         "end_lat": "", "end_lng": ""},
        {"lat": "-16.2", "lng": "-49.2", "kind": "FIXED", "limit_kmh": "80", "source": "OSM", "active": "0",
         "end_lat": "", "end_lng": ""},
        {"lat": "-16.3", "lng": "-49.3", "kind": "SECTION", "limit_kmh": "100", "source": "OSM", "active": "1",
         "end_lat": "-16.4", "end_lng": "-49.4"},
    ]

    def test_geojson_keeps_everything(self):
        from datakit import build_formats as bf
        g = bf.to_geojson(self.ROWS)
        self.assertEqual(len(g["features"]), 4)
        self.assertEqual(g["features"][0]["geometry"]["coordinates"], [-49.0, -16.0])
        self.assertEqual(g["features"][0]["properties"]["limit_kmh"], 60)
        self.assertFalse(g["features"][2]["properties"]["active"])
        self.assertEqual(g["features"][3]["properties"]["end"], [-49.4, -16.4])

    def test_kml_and_gpx_are_valid_xml_with_only_active(self):
        import xml.etree.ElementTree as ET
        from datakit import build_formats as bf
        kml = ET.fromstring(bf.to_kml(self.ROWS))
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        names = [n.text for n in kml.findall(".//k:Placemark/k:name", ns)]
        self.assertEqual(names, ["Radar 60 km/h", "Radar de trecho 100 km/h", "Avanço de sinal"])
        gpx = ET.fromstring(bf.to_gpx(self.ROWS))
        wpts = gpx.findall("{http://www.topografix.com/GPX/1/1}wpt")
        self.assertEqual(len(wpts), 3)
        self.assertEqual(wpts[0].get("lat"), "-16.0")

    def test_direction_in_properties_and_descriptions(self):
        from datakit import build_formats as bf
        rows = [dict(self.ROWS[0], direction_deg="92"), dict(self.ROWS[1], direction_deg="")]
        g = bf.to_geojson(rows)
        self.assertEqual([f["properties"]["direction_deg"] for f in g["features"]], [92, None])
        self.assertEqual(bf.description(rows[0]), "Fonte: DNIT · sentido 92° (leste)")
        self.assertEqual(bf.description(rows[1]), "Fonte: R&D <x>")
        self.assertIn("sentido 92° (leste)", bf.to_gpx(rows))
        self.assertEqual(bf.direction_text({"direction_deg": "350"}), "sentido 350° (norte)")


class Estimated(unittest.TestCase):
    def test_class_limit_typical_and_low_side(self):
        from datakit.common.infer import class_limit
        self.assertEqual(class_limit("trunk", lit=False), (100, 100))
        self.assertEqual(class_limit("trunk", lit=True), (80, 60))
        self.assertEqual(class_limit("primary", lit=False), (100, 80))
        self.assertEqual(class_limit("secondary", lit=True), (60, 50))
        self.assertEqual(class_limit("tertiary", lit=False), (60, 40))
        self.assertEqual(class_limit("unclassified", lit=True), (40, 40))
        self.assertIsNone(class_limit("footway", lit=False))

    def test_zone_limit(self):
        from datakit.common.infer import zone_limit
        self.assertEqual(zone_limit("BR:urban:primary"), (60, 60))
        self.assertEqual(zone_limit("rural"), (100, 80))
        self.assertEqual(zone_limit("40"), (40, 40))
        self.assertIsNone(zone_limit("sign"))

    def test_is_yes(self):
        from datakit.common.infer import is_yes
        self.assertTrue(is_yes("yes") and is_yes("24/7"))
        self.assertFalse(is_yes("no") or is_yes("") or is_yes(None))

    def test_urban_needs_streets_around(self):
        from datakit.sources.osm_pbf import _cell, _is_urban, URBAN_THRESHOLD
        road = [(-16.0, -49.0), (-16.001, -49.0)]
        self.assertFalse(_is_urban(road, {}))
        urban = {_cell(-16.0005, -49.0005): URBAN_THRESHOLD}
        self.assertTrue(_is_urban(road, urban))

    def test_signed_value_is_never_replaced_by_estimate(self):
        from datakit.common.model import merge_limits
        real = Limit(-16.0, -49.0, 60, "OSM")
        est = Limit(-16.0, -49.0, 100, "OSM:class", True, 80)
        self.assertEqual(merge_limits([real], [est]), [real])
        self.assertEqual(merge_limits([est], [real]), [real])

    def test_brazil_splits_estimated_limits(self):
        from datakit.build_brazil import split_estimated
        rows = [{"estimated": "0"}, {"estimated": "1"}, {}]
        signed, est = split_estimated(rows)
        self.assertEqual((len(signed), len(est)), (2, 1))

    def test_unclassified_is_not_estimated(self):
        from datakit.sources.osm_pbf import _INFER_CLASSES
        self.assertNotIn("unclassified", _INFER_CLASSES)
        self.assertIn("tertiary", _INFER_CLASSES)

    def test_publish_paths_for_estimated_file(self):
        import sys
        sys.path.insert(0, "scripts")
        from publish import dest_for
        self.assertEqual(dest_for("limites_estimados_BR.csv"), "brasil/limites_estimados.csv")
        self.assertEqual(dest_for("limites_estimados_BR.csv.gz"), "brasil/limites_estimados.csv.gz")
        self.assertEqual(dest_for("limites_GO.csv"), "estados/GO/limites.csv")

    def test_limit_row_roundtrip(self):
        est = Limit(-16.0, -49.0, 100, "OSM:class", True, 80)
        row = dict(zip(Limit.HEADER, est.row()))
        self.assertEqual(row["estimated"], "1")
        self.assertEqual(row["limit_low_kmh"], "80")
        self.assertEqual(Limit.from_row(row), Limit(-16.0, -49.0, 100, "OSM:class", True, 80))
        real = dict(zip(Limit.HEADER, Limit(-16.0, -49.0, 60, "OSM").row()))
        self.assertEqual((real["estimated"], real["limit_low_kmh"]), ("0", ""))


if __name__ == "__main__":
    unittest.main()
