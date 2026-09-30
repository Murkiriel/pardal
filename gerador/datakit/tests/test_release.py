"""Release de uma geração (scripts/release.py): pacotes e nota."""
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "release.py"))
_spec = importlib.util.spec_from_file_location("release", _PATH)
assert _spec is not None and _spec.loader is not None
rel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rel)


def _cat(built, **ufs):
    return {"built_at": built, "ufs": {uf: {"counts": c} for uf, c in ufs.items()}}


class Nota(unittest.TestCase):
    def test_counts_differences_and_warnings(self):
        antes = _cat("2026-09-30T12:13:21Z", BR={"cameras": 17798, "limits": 100, "limits_estimated": 5, "structs": 9},
                     GO={"cameras": 1428, "limits": 125500, "structs": 4929})
        novo = _cat("2026-10-01T06:40:00Z", BR={"cameras": 17810, "limits": 100, "limits_estimated": 4, "structs": 9},
                    GO={"cameras": 1431, "limits": 125500, "structs": 4929})
        n = rel.nota(novo, antes, ["Inmetro GO: usada a cópia de 2026-09-30"], "https://github.com/x/y")
        self.assertIn("Dados gerados em 01/10/2026", n)
        self.assertIn("| Radares | 17.810 (+12) |", n)
        self.assertIn("| Pontos de limite estimado | 4 (−1) |", n)
        self.assertIn("| Pontos de limite sinalizado | 100 |", n)          # sem mudança: sem parênteses
        self.assertIn("| GO | 1.431 (+3) | 125.500 | 4.929 |", n)
        self.assertIn("## Avisos", n)
        self.assertIn("https://github.com/x/y/releases/latest/download/pardal-SP.zip", n)
        self.assertNotIn("| BR |", n)
        self.assertEqual(rel.etiqueta(novo), "dados-2026-10-01")

    def test_first_release_has_no_differences(self):
        n = rel.nota(_cat("2026-10-01T06:40:00Z", BR={"cameras": 1}, GO={"cameras": 1}), None, [], "u")
        self.assertNotIn("(+", n)
        self.assertNotIn("diferença para a geração anterior", n)
        self.assertNotIn("## Avisos", n)


class Pacotes(unittest.TestCase):
    def test_zips_per_state_brasil_and_radares_only(self):
        with tempfile.TemporaryDirectory() as repo:
            for uf in ("GO", "SP"):
                os.makedirs(os.path.join(repo, "estados", uf))
                for f in ("radares.csv", "radares.gpx", "limites.csv", "estruturas.csv"):
                    with open(os.path.join(repo, "estados", uf, f), "w") as fh:
                        fh.write("x")
            os.makedirs(os.path.join(repo, "brasil"))
            for f in ("radares.csv", "radares.kml", "limites.csv", "limites_estimados.csv", "estruturas.csv"):
                with open(os.path.join(repo, "brasil", f), "w") as fh:
                    fh.write("x")
            with open(os.path.join(repo, "catalog.json"), "w") as fh:
                json.dump(_cat("2026-10-01T06:40:00Z"), fh)
            out = os.path.join(repo, "_out")
            anexos = [os.path.basename(a) for a in rel.pacotes(repo, out)]
            self.assertEqual(anexos, ["pardal-GO.zip", "pardal-SP.zip", "pardal-brasil.zip",
                                      "pardal-brasil-radares.zip", "catalog.json", "SHA256SUMS.txt"])
            with zipfile.ZipFile(os.path.join(out, "pardal-GO.zip")) as z:
                self.assertEqual(sorted(z.namelist()),
                                 ["GO/estruturas.csv", "GO/limites.csv", "GO/radares.csv", "GO/radares.gpx"])
            with zipfile.ZipFile(os.path.join(out, "pardal-brasil-radares.zip")) as z:
                self.assertEqual(sorted(z.namelist()), ["brasil/radares.csv", "brasil/radares.kml"])
            with open(os.path.join(out, "SHA256SUMS.txt"), encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
