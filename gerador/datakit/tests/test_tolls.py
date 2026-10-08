"""Praças de pedágio nos pacotes por estado: pedagios.csv (lat,lng,kind,name,source), schema 4 do pacote.

As federais vêm da ANTT ("Dados das Praças de Pedágio", com coordenada e nome, só as ativas); as estaduais, do
OpenStreetMap: barrier=toll_booth (cabine) e highway=toll_gantry (pórtico de free flow). O mesmo pedágio nas duas
fontes, a até 300 m, fica um só, com o nome da ANTT. Sem preço: as tarifas publicadas estão desatualizadas.
"""
import csv
import os
import tempfile
import unittest

try:
    import osmium
    from osmium.osm.mutable import Node
except ImportError:  # pragma: no cover
    osmium = None  # type: ignore[assignment]

from datakit.common import Toll

HEADER = ("concessionaria;praca_de_pedagio;ano_do_pnv_snv;rodovia;uf;km_m;municipal;tipo_de_pista;sentido;situacao;"
          "data_da_inativacao;latitude;longitude")


def _antt_csv(*rows: str) -> bytes:
    return ("\n".join((HEADER,) + rows) + "\n").encode("latin-1")


class AnttTolls(unittest.TestCase):
    def test_the_active_plazas_come_in_with_their_name_and_free_flow_is_told_apart(self):
        from datakit.sources import antt_pedagio
        text = _antt_csv(
            "TRIUNFO CONCEBRA;P2 GOIANÁPOLIS;2019;BR-060;GO;100.5;Goianápolis;Principal;Crescente/Decrescente;Ativo;;-16.51;-49.02",
            "ECOVIAS;Free Flow Indiara - P02A;2023;BR-060;GO;200;Indiara;Principal;Crescente;Ativo;;-17.13;-49.98",
            "VELHA;Praça antiga;2010;BR-153;GO;10;Itumbiara;Principal;Crescente;Inativo;2024-01-01;-18.4;-49.2",
            "SEM PONTO;Praça sem ponto;2010;BR-153;GO;10;Itumbiara;Principal;Crescente;Ativo;;;",
        ).decode("latin-1")
        got = antt_pedagio.parse(text)
        self.assertEqual([(t.name, t.kind, t.source) for t in got],
                         [("P2 GOIANÁPOLIS", "PLAZA", "ANTT"), ("Free Flow Indiara - P02A", "FREE_FLOW", "ANTT")])
        self.assertEqual((got[0].lat, got[0].lng), (-16.51, -49.02))

    def test_the_file_is_read_as_latin_1(self):
        from datakit.sources import antt_pedagio
        raw = _antt_csv("X;P1 ALEXÂNIA;2019;BR-060;GO;1;Alexânia;Principal;Crescente;Ativo;;-16.08;-48.5")
        self.assertEqual("P1 ALEXÂNIA", antt_pedagio.parse(antt_pedagio.decode(raw))[0].name)


@unittest.skipIf(osmium is None, "pyosmium não instalado")
class OsmTolls(unittest.TestCase):
    def test_booths_and_gantries_come_in_and_other_barriers_stay_out(self):
        from datakit.sources import osm_pbf
        with tempfile.TemporaryDirectory() as tmp:
            pbf = os.path.join(tmp, "x.osm.pbf")
            with osmium.SimpleWriter(pbf) as w:
                w.add_node(Node(id=1, location=(-49.0, -16.0), tags={"barrier": "toll_booth", "name": "Pedágio Estadual"}))
                w.add_node(Node(id=2, location=(-49.1, -16.1), tags={"highway": "toll_gantry"}))
                w.add_node(Node(id=3, location=(-49.2, -16.2), tags={"barrier": "gate"}))
                w.add_node(Node(id=4, location=(-48.0, -15.0), tags={"barrier": "toll_booth"}))  # fora do bbox
            got = osm_pbf.load_tolls(pbf, bbox=(-17.0, -50.0, -15.9, -48.9))
        self.assertEqual(sorted((t.kind, t.name, t.source) for t in got),
                         [("FREE_FLOW", "", "OSM"), ("PLAZA", "Pedágio Estadual", "OSM")])


class MergeAndPack(unittest.TestCase):
    def test_the_same_toll_in_both_sources_is_one_with_the_antt_name(self):
        from datakit.common.model import merge_tolls
        antt = [Toll(-16.51, -49.02, "PLAZA", "P2 GOIANÁPOLIS", "ANTT")]
        osm = [Toll(-16.5110, -49.0205, "PLAZA", "", "OSM"),     # ~130 m: a mesma praça
               Toll(-17.00, -49.50, "PLAZA", "Estadual", "OSM")]  # longe: outra
        got = merge_tolls(antt, osm)
        self.assertEqual(sorted((t.name, t.source) for t in got), [("Estadual", "OSM"), ("P2 GOIANÁPOLIS", "ANTT")])

    def test_the_pack_writes_the_tolls_counts_them_and_the_catalog_lists_them(self):
        from datakit import build_catalog
        from datakit.build_pack import build as build_pack
        tolls = [Toll(-16.51, -49.02, "PLAZA", "P2 GOIANÁPOLIS", "ANTT"), Toll(-17.0, -49.5, "FREE_FLOW", "", "OSM")]
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            m = build_pack("GO", packs, [], [], [], sources=[], tolls=tolls)
            build_pack("AC", packs, [], [], [], sources=[])
            with open(os.path.join(packs, "GO", "pedagios.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            cat = build_catalog.build(packs, dist)
        self.assertEqual(rows, [
            {"lat": "-17.000000", "lng": "-49.500000", "kind": "FREE_FLOW", "name": "", "source": "OSM"},
            {"lat": "-16.510000", "lng": "-49.020000", "kind": "PLAZA", "name": "P2 GOIANÁPOLIS", "source": "ANTT"},
        ])
        self.assertEqual((m["schema"], m["counts"]["tolls"]), (4, 2))
        self.assertIn("pedagios.csv", m["files"])
        self.assertEqual((cat["ufs"]["GO"]["tolls"]["file"], cat["ufs"]["GO"]["tolls"]["count"]), ("estados/GO/pedagios.csv", 2))
        self.assertNotIn("tolls", cat["ufs"]["AC"])
