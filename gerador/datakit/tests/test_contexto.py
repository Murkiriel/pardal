"""Contexto do build (o estado de uma execução num objeto só) e o contrato das fontes."""
import unittest
from unittest import mock


class ContratoDasFontes(unittest.TestCase):
    def test_every_official_source_has_carregar(self):
        from datakit import build
        for name, mod in build.OFFICIAL.items():
            self.assertTrue(callable(getattr(mod, "carregar", None)), name)

    def test_no_module_level_caches_left(self):
        from datakit import build
        from datakit.common import ufpoly
        from datakit.sources import cet_sp, snv
        for mod, attr in ((build, "_OSM_CACHE"), (build, "_OFFICIAL_CACHE"), (build, "_NATIONAL"),
                          (cet_sp, "_DESATIVADOS"), (snv, "_ROUTES"), (ufpoly, "_cache")):
            self.assertFalse(hasattr(mod, attr), f"{mod.__name__}.{attr}")

    def test_cet_returns_deactivated_sites_in_the_carga(self):
        from datakit.contexto import Contexto
        from datakit.sources import cet_sp
        rows = [{"LATITUDE": "-23.55", "LONGITUDE": "-46.63", "ENQUADRAMENTOS": "V", "VELOCIDADE": "50"},
                {"LATITUDE": "-23.56", "LONGITUDE": "-46.64", "ENQUADRAMENTOS": "V", "DESATIVAÇÃO": "2024-01-01"}]
        with mock.patch.object(cet_sp, "_rows_with_fallback", lambda raw_dir: rows):
            carga = cet_sp.carregar(Contexto("raw"))
        self.assertEqual(len(carga.radares), 1)
        self.assertEqual(carga.desativados, [(-23.56, -46.64)])


class Contexto_(unittest.TestCase):
    def test_a_failing_source_is_registered_and_the_others_load(self):
        from datakit import falhas
        from datakit.common import Camera
        from datakit.contexto import Carga, Contexto

        class Boa:
            @staticmethod
            def carregar(ctx):
                return Carga([Camera(-16.0, -49.0, source="BOA")])

        class Ruim:
            @staticmethod
            def carregar(ctx):
                raise ConnectionError("fora do ar")

        antes = list(falhas._FALHAS)
        try:
            ctx = Contexto("raw", fontes={"RUIM": Ruim, "BOA": Boa})
            got = ctx.oficiais()
            self.assertIs(ctx.oficiais(), got)                      # carregado uma vez
            novas = falhas.lista()[len(antes):]
        finally:
            falhas._FALHAS[:] = antes
        self.assertEqual([len(got["RUIM"].radares), len(got["BOA"].radares)], [0, 1])
        self.assertEqual(len(novas), 1)
        self.assertIn("RUIM", novas[0])

    def test_snv_routes_and_polygons_are_loaded_once_per_context(self):
        from datakit.common import ufpoly
        from datakit.contexto import Contexto
        from datakit.sources import snv
        calls = {"snv": 0, "poly": 0}

        def rotas(raw_dir):
            calls["snv"] += 1
            return "rotas"

        def polys(raw_dir):
            calls["poly"] += 1
            return {"GO": object()}

        with mock.patch.object(snv, "load_routes", rotas), mock.patch.object(ufpoly, "polygons", polys):
            ctx = Contexto("raw")
            self.assertEqual([ctx.rotas_snv(), ctx.rotas_snv()], ["rotas", "rotas"])
            ctx.polygons()
            ctx.polygons()
            Contexto("raw").rotas_snv()                               # outro contexto: carrega de novo
        self.assertEqual(calls, {"snv": 2, "poly": 1})


if __name__ == "__main__":
    unittest.main()
