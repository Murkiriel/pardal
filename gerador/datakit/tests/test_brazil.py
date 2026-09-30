"""Arquivo do Brasil (build_brazil) e o teto de tamanho dos arquivos publicados."""
import csv
import os
import tempfile
import unittest
from unittest import mock

from datakit import build_brazil

CAM_H = "lat,lng,kind,limit_kmh,source,active,end_lat,end_lng,direction_deg"
LIM_H = "lat,lng,limit_kmh,source,estimated,limit_low_kmh,direction_deg"
STR_H = "lat1,lng1,lat2,lng2,kind"


def _pack(root, uf, cams, lims, structs):
    d = os.path.join(root, uf)
    os.makedirs(d)
    for name, header, rows in (("cameras.csv", CAM_H, cams), ("limits.csv", LIM_H, lims),
                               ("structs.csv", STR_H, structs)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join([header, *rows]) + "\n")
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{}")


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [tuple(r.values()) for r in csv.DictReader(f)]


class Brazil(unittest.TestCase):
    def test_merges_states_dedupes_borders_and_splits_estimated(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            # GO e DF repetem o mesmo radar e a mesma ponte na divisa; no DF o radar tem outro limite
            _pack(packs, "DF", ["-15.9,-47.9,FIXED,60,DETRAN-DF,1,,,", "-15.5,-48.2,FIXED,80,OSM,1,,,90"],
                  ["-15.9,-47.9,60,OSM,0,,", "-15.8,-47.8,40,OSM:class,1,30,"],
                  ["-15.5,-48.2,-15.5,-48.21,BRIDGE"])
            _pack(packs, "GO", ["-16.0,-49.0,FIXED,,OSM,1,,,", "-15.5,-48.2,FIXED,60,OSM,1,,,90"],
                  ["-16.0,-49.0,80,OSM,0,,"],
                  ["-15.5,-48.2,-15.5,-48.21,BRIDGE", "-16.1,-49.1,-16.1,-49.11,TUNNEL"])
            br = build_brazil.build(packs, dist)
            cams = _rows(os.path.join(dist, "radares_BR.csv"))
            self.assertEqual(cams, [
                ("-15.9", "-47.9", "FIXED", "60", "DETRAN-DF", "1", "", "", ""),
                ("-15.5", "-48.2", "FIXED", "60", "OSM", "1", "", "", "90"),   # 1ª posição, dados do último
                ("-16.0", "-49.0", "FIXED", "", "OSM", "1", "", "", ""),
            ])
            self.assertEqual(len(_rows(os.path.join(dist, "limites_BR.csv"))), 2)
            self.assertEqual(_rows(os.path.join(dist, "limites_estimados_BR.csv")),
                             [("-15.8", "-47.8", "40", "OSM:class", "1", "30", "")])
            self.assertEqual(len(_rows(os.path.join(dist, "estruturas_BR.csv"))), 2)
            self.assertEqual(br["counts"], {"cameras": 3, "limits": 2, "limits_estimated": 1, "structs": 2})
            self.assertEqual(br["radares"]["count"], 3)

    def test_estimated_over_the_cap_is_gzipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs, dist = os.path.join(tmp, "p"), os.path.join(tmp, "d")
            _pack(packs, "GO", [], [f"-16.{i:04d},-49.0,60,OSM:class,1,40," for i in range(200)], [])
            with mock.patch.object(build_brazil, "MAX_FILE_BYTES", 1000):
                br = build_brazil.build(packs, dist)
            self.assertEqual(br["limites_estimados"]["file"], "limites_estimados_BR.csv.gz")
            self.assertFalse(os.path.exists(os.path.join(dist, "limites_estimados_BR.csv")))


class FileSize(unittest.TestCase):
    """Nenhum arquivo publicado pode passar do teto (o GitHub recusa acima de 100 MB)."""

    def test_oversized_lists_every_big_published_file(self):
        with tempfile.TemporaryDirectory() as d:
            for name, size in (("radares_GO.csv", 10), ("limites_SP.csv", 2000), ("radares_BR.geojson", 3000),
                               ("limites_estimados_BR.csv.gz", 500), ("BUILD_EM_ANDAMENTO", 5000)):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"x" * size)
            self.assertEqual(build_brazil.oversized(d, limit=1000),
                             [("limites_SP.csv", 2000), ("radares_BR.geojson", 3000)])

    def test_publish_refuses_an_oversized_file(self):
        import importlib.util
        import json
        path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "publish.py")
        spec = importlib.util.spec_from_file_location("publicar_tamanho", path)
        pub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pub)
        with tempfile.TemporaryDirectory() as tmp:
            dist, repo = os.path.join(tmp, "d"), os.path.join(tmp, "r")
            os.makedirs(dist)
            os.makedirs(repo)
            with open(os.path.join(dist, "catalog.json"), "w", encoding="utf-8") as f:
                json.dump({"ufs": {}}, f)
            with open(os.path.join(dist, "limites_SP.csv"), "wb") as f:
                f.write(b"x" * 2000)
            with mock.patch.object(pub, "MAX_FILE_BYTES", 1000), self.assertRaises(SystemExit) as cm:
                pub.main(["--repo", repo, "--dist", dist])
            self.assertIn("limites_SP.csv", str(cm.exception.code))
            self.assertEqual(os.listdir(repo), [])


if __name__ == "__main__":
    unittest.main()
