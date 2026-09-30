"""Contexto do build (o estado de uma execução num objeto só) e o contrato das fontes."""
import unittest
from unittest import mock


class SourceContract(unittest.TestCase):
    def test_every_official_source_has_fetch(self):
        from datakit import build
        for name, mod in build.OFFICIAL.items():
            self.assertTrue(callable(getattr(mod, "fetch", None)), name)

    def test_no_module_level_caches_left(self):
        from datakit import build
        from datakit.common import ufpoly
        from datakit.sources import cet_sp, snv
        for mod, attr in ((build, "_OSM_CACHE"), (build, "_OFFICIAL_CACHE"), (build, "_NATIONAL"),
                          (cet_sp, "_DESATIVADOS"), (snv, "_ROUTES"), (ufpoly, "_cache")):
            self.assertFalse(hasattr(mod, attr), f"{mod.__name__}.{attr}")

    def test_cet_returns_deactivated_sites_in_the_source_data(self):
        from datakit.context import BuildContext
        from datakit.sources import cet_sp
        rows = [{"LATITUDE": "-23.55", "LONGITUDE": "-46.63", "ENQUADRAMENTOS": "V", "VELOCIDADE": "50"},
                {"LATITUDE": "-23.56", "LONGITUDE": "-46.64", "ENQUADRAMENTOS": "V", "DESATIVAÇÃO": "2024-01-01"}]
        with mock.patch.object(cet_sp, "_rows_with_fallback", lambda raw_dir: rows):
            loaded = cet_sp.fetch(BuildContext("raw"))
        self.assertEqual(len(loaded.cameras), 1)
        self.assertEqual(loaded.deactivated, [(-23.56, -46.64)])


class BuildContextTests(unittest.TestCase):
    def test_a_failing_source_is_registered_and_the_others_load(self):
        from datakit import failures
        from datakit.common import Camera
        from datakit.context import SourceData, BuildContext

        class Good:
            @staticmethod
            def fetch(ctx):
                return SourceData([Camera(-16.0, -49.0, source="BOA")])

        class Bad:
            @staticmethod
            def fetch(ctx):
                raise ConnectionError("fora do ar")

        before = list(failures._FAILURES)
        try:
            ctx = BuildContext("raw", sources={"RUIM": Bad, "BOA": Good})
            got = ctx.official()
            self.assertIs(ctx.official(), got)                      # carregado uma vez
            new_failures = failures.recorded()[len(before):]
        finally:
            failures._FAILURES[:] = before
        self.assertEqual([len(got["RUIM"].cameras), len(got["BOA"].cameras)], [0, 1])
        self.assertEqual(len(new_failures), 1)
        self.assertIn("RUIM", new_failures[0])

    def test_snv_routes_and_polygons_are_loaded_once_per_context(self):
        from datakit.common import ufpoly
        from datakit.context import BuildContext
        from datakit.sources import snv
        calls = {"snv": 0, "poly": 0}

        def rotas(raw_dir):
            calls["snv"] += 1
            return "rotas"

        def polys(raw_dir):
            calls["poly"] += 1
            return {"GO": object()}

        with mock.patch.object(snv, "load_routes", rotas), mock.patch.object(ufpoly, "polygons", polys):
            ctx = BuildContext("raw")
            self.assertEqual([ctx.snv_routes(), ctx.snv_routes()], ["rotas", "rotas"])
            ctx.polygons()
            ctx.polygons()
            BuildContext("raw").snv_routes()                               # outro contexto: carrega de novo
        self.assertEqual(calls, {"snv": 2, "poly": 1})


if __name__ == "__main__":
    unittest.main()
