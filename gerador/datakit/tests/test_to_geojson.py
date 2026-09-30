"""Testes do scripts/to_geojson.py do repositório (conversor CSV -> GeoJSON para quem baixa).

    python -m unittest discover -s datakit/tests -t .     # de dentro de gerador/
"""
import gzip
import importlib.util
import json
import os
import tempfile
import unittest

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "to_geojson.py"))
_spec = importlib.util.spec_from_file_location("to_geojson", _PATH)
assert _spec is not None and _spec.loader is not None
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)

CAMERA_FILES = ("lat,lng,kind,limit_kmh,source,active,end_lat,end_lng,direction_deg\n"
                "-16.0,-49.0,FIXED,60,DNIT,1,,,275\n"
                "-16.3,-49.3,SECTION,100,OSM,0,-16.4,-49.4,\n")
LIMITS_CSV = ("lat,lng,limit_kmh,source,estimated,limit_low_kmh\n"
              "-16.0,-49.0,60,ANTT,0,\n"
              "-16.1,-49.1,100,OSM:class,1,80\n")
STRUCTURES_CSV = "lat1,lng1,lat2,lng2,kind\n-16.0,-49.0,-16.01,-49.01,BRIDGE\n"


class ToGeojson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, name, text, gz=False):
        path = os.path.join(self.tmp, name)
        if gz:
            with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
                f.write(text)
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        return path

    def _run(self, path, *extra):
        self.assertEqual(pg.main([path, *extra]), 0)
        with open(pg.default_output(path), encoding="utf-8") as f:
            return json.load(f)

    def test_camera_points_with_typed_properties(self):
        g = self._run(self._write("radares.csv", CAMERA_FILES))
        self.assertEqual(g["type"], "FeatureCollection")
        self.assertEqual(len(g["features"]), 2)
        a, b = g["features"]
        self.assertEqual(a["geometry"], {"type": "Point", "coordinates": [-49.0, -16.0]})
        self.assertEqual(a["properties"]["limit_kmh"], 60)
        self.assertIs(a["properties"]["active"], True)
        self.assertIsNone(a["properties"]["end_lat"])
        self.assertIs(b["properties"]["active"], False)
        self.assertEqual(a["properties"]["direction_deg"], 275)
        self.assertIsNone(b["properties"]["direction_deg"])
        self.assertEqual(b["properties"]["end_lng"], -49.4)

    def test_limits_and_the_estimated_filter(self):
        path = self._write("limites.csv", LIMITS_CSV)
        g = self._run(path)
        self.assertEqual([f["properties"]["estimated"] for f in g["features"]], [False, True])
        self.assertEqual(g["features"][1]["properties"]["limit_low_kmh"], 80)
        g = self._run(path, "--no-estimated")
        self.assertEqual(len(g["features"]), 1)
        self.assertEqual(g["features"][0]["properties"]["source"], "ANTT")

    def test_structures_become_lines(self):
        g = self._run(self._write("estruturas.csv", STRUCTURES_CSV))
        self.assertEqual(g["features"][0]["geometry"],
                         {"type": "LineString", "coordinates": [[-49.0, -16.0], [-49.01, -16.01]]})
        self.assertEqual(g["features"][0]["properties"], {"kind": "BRIDGE"})

    def test_reads_gzip_and_names_the_output(self):
        path = self._write("limites_estimados.csv.gz", LIMITS_CSV, gz=True)
        self.assertTrue(pg.default_output(path).endswith("limites_estimados.geojson"))
        self.assertEqual(len(self._run(path)["features"]), 2)

    def test_explicit_output_and_bad_rows_are_skipped(self):
        path = self._write("x.csv", LIMITS_CSV + "nao,e,numero,OSM,0,\n")
        out = os.path.join(self.tmp, "saida.geojson")
        self.assertEqual(pg.main([path, "-o", out]), 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["features"]), 2)

    def test_empty_file_gives_valid_empty_collection(self):
        g = self._run(self._write("vazio.csv", "lat,lng,limit_kmh,source,estimated,limit_low_kmh\n"))
        self.assertEqual(g["features"], [])


if __name__ == "__main__":
    unittest.main()
