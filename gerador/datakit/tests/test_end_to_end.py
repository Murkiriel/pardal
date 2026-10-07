"""Build de uma UF de ponta a ponta, em segundos e sem rede: fonte oficial simulada + um
.osm.pbf minúsculo gravado aqui com o pyosmium + polígonos de UF sintéticos. Confere o pacote
que sai em data/packs/<UF>: radar na ponte (fora do polígono, a < 5 km), duplicata OSM/oficial
a 29 m juntada, sentido da fonte oficial preservado, radar em mar aberto fora, ponte/túnel e
contagens do manifest."""
import csv
import json
import math
import os
import tempfile
import unittest
from unittest import mock

try:
    import osmium
    from osmium.osm.mutable import Node, Way
    from shapely.geometry import box
except ImportError:  # pragma: no cover
    osmium = None  # type: ignore[assignment]

from datakit.common import Camera, CameraKind

LAT = -22.90
INLAND = (-22.90, -43.25)                    # dentro do "RJ"
BRIDGE = (-22.87, -43.15)                    # ~3 km a leste do "RJ": na ponte
SEA = (-22.87, -43.00)                       # ~18 km: mar aberto


def _east(lat, lng, m):
    return lng + math.degrees(m / (6_371_000.0 * math.cos(math.radians(lat))))


def _write_pbf(path):
    with osmium.SimpleWriter(path) as w:
        # radares do OSM: um no continente (60 km/h, sem sentido), um em mar aberto
        w.add_node(Node(id=1, location=(INLAND[1], INLAND[0]), tags={"highway": "speed_camera", "maxspeed": "60"}))
        w.add_node(Node(id=2, location=(SEA[1], SEA[0]), tags={"highway": "speed_camera"}))
        # lombadas: uma no continente, uma em mar aberto (fica de fora)
        w.add_node(Node(id=3, location=(-43.26, -22.91), tags={"traffic_calming": "bump"}))
        w.add_node(Node(id=4, location=(SEA[1], SEA[0] + 0.01), tags={"traffic_calming": "hump"}))
        # uma via com limite sinalizado no continente e uma ponte que sai do "RJ" para a baía
        w.add_node(Node(id=10, location=(-43.29, -22.95)))
        w.add_node(Node(id=11, location=(-43.20, -22.95)))
        w.add_node(Node(id=20, location=(-43.20, -22.87)))
        w.add_node(Node(id=21, location=(-43.14, -22.87)))
        w.add_way(Way(id=100, nodes=[10, 11], tags={"highway": "trunk", "maxspeed": "80"}))
        w.add_way(Way(id=101, nodes=[20, 21], tags={"highway": "trunk", "maxspeed": "80", "bridge": "yes"}))


class _Antt:
    """Fonte oficial simulada: o radar a 29 m a leste do do OSM (mesmo equipamento, com
    sentido) e o radar da ponte."""

    @staticmethod
    def fetch(ctx):
        from datakit.context import SourceData
        return SourceData([Camera(INLAND[0], _east(*INLAND, 29.0), CameraKind.FIXED, None, "ANTT", True, direction_deg=90),
                           Camera(BRIDGE[0], BRIDGE[1], CameraKind.FIXED, 80, "ANTT", True, direction_deg=270)])


@unittest.skipIf(osmium is None, "pyosmium/shapely não instalados")
class EndToEnd(unittest.TestCase):
    def test_build_one_state(self):
        from datakit import build, failures
        from datakit.sources import cet_sp, inmetro, osm_pbf, rio, snv

        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "raw")
            os.makedirs(raw)
            pbf = os.path.join(raw, "sudeste-latest.osm.pbf")
            _write_pbf(pbf)
            extract = osm_pbf.Extract(path=pbf, region="sudeste", file_date="Wed, 30 Sep 2026 00:00:00 GMT",
                                      sha256="0" * 64, bytes=os.path.getsize(pbf))
            polys = {"RJ": box(-43.30, -23.00, -43.18, -22.80), "ES": box(-41.0, -21.0, -40.0, -20.0)}
            before = list(failures._FAILURES)

            def no_network(*a, **k):
                raise AssertionError("o teste não pode ir à rede")

            from datakit.common import ufpoly
            with mock.patch.dict(build.OFFICIAL, {"ANTT": _Antt}, clear=True), \
                    mock.patch.object(ufpoly, "polygons", lambda raw_dir: polys), \
                    mock.patch.object(osm_pbf, "ensure_extract", lambda region, raw_dir, refresh=True: extract), \
                    mock.patch.object(snv, "ensure", lambda raw_dir: None), \
                    mock.patch.object(inmetro, "load", lambda raw_dir: []), \
                    mock.patch.object(rio, "load_limits", lambda: []), \
                    mock.patch.object(cet_sp, "load_limits", lambda: []), \
                    mock.patch("datakit.sources._http.session", no_network):
                try:
                    code = build.main(["--uf", "RJ", "--raw", raw, "--packs", os.path.join(tmp, "packs"),
                                       "--dist", os.path.join(tmp, "dist")])
                    new_failures = failures.recorded()[len(before):]
                finally:
                    failures._FAILURES[:] = before

            self.assertEqual(code, 0)
            self.assertEqual(new_failures, [])
            pack = os.path.join(tmp, "packs", "RJ")
            with open(os.path.join(pack, "radares.csv"), encoding="utf-8") as f:
                cams = list(csv.DictReader(f))
            with open(os.path.join(pack, "limites.csv"), encoding="utf-8") as f:
                lims = list(csv.DictReader(f))
            with open(os.path.join(pack, "estruturas.csv"), encoding="utf-8") as f:
                structs = list(csv.DictReader(f))
            with open(os.path.join(pack, "lombadas.csv"), encoding="utf-8") as f:
                bumps = list(csv.DictReader(f))
            with open(os.path.join(pack, "manifesto.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            with open(os.path.join(tmp, "dist", "catalogo.json"), encoding="utf-8") as f:
                catalog = json.load(f)
            marker = os.path.exists(os.path.join(tmp, "dist", build.IN_PROGRESS_MARKER))
            with open(os.path.join(tmp, "audit", "juncoes_RJ.csv"), encoding="utf-8") as f:
                audit = list(csv.DictReader(f))

        by_pos = {(round(float(c["lat"]), 5), round(float(c["lng"]), 5)): c for c in cams}
        joined = by_pos[(round(INLAND[0], 5), round(_east(*INLAND, 29.0), 5))]   # fica a posição oficial
        self.assertEqual((joined["source"], joined["limit_kmh"], joined["direction_deg"]), ("ANTT+OSM", "60", "90"))
        bridge = by_pos[(round(BRIDGE[0], 5), round(BRIDGE[1], 5))]
        self.assertEqual((bridge["source"], bridge["direction_deg"]), ("ANTT", "270"))
        self.assertEqual(len(cams), 2)                                  # o do mar aberto ficou de fora
        self.assertEqual([(a["rule"], a["source_a"], a["source_b"]) for a in audit], [("osm_official", "ANTT", "OSM")])
        self.assertEqual(len(structs), 1)
        self.assertEqual(structs[0]["kind"], "BRIDGE")
        self.assertTrue(lims and all(x["limit_kmh"] == "80" and x["source"] == "OSM" for x in lims))
        self.assertTrue(any(float(x["lng"]) > -43.18 for x in lims))   # limite da ponte, fora do polígono
        self.assertEqual(manifest["counts"], {"cameras": 2, "cameras_with_limit": 2, "cameras_inactive": 0,
                                              "sections": 0, "red_lights": 0, "limits": len(lims), "structs": 1,
                                              "bumps": 1})
        self.assertEqual([(b["kind"], b["lat"]) for b in bumps], [("BUMP", "-22.910000")])  # a do mar ficou de fora
        self.assertEqual(catalog["ufs"]["RJ"]["bumps"]["count"], 1)
        self.assertEqual(catalog["failures"], [])
        self.assertIn("RJ", catalog["ufs"])
        self.assertFalse(marker)


if __name__ == "__main__":
    unittest.main()
