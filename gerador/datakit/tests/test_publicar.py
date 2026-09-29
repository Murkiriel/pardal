"""Testes da publicação (scripts/publicar.py) e das novas tentativas de download (_http).

    python -m unittest discover -s datakit/tests -t .     # de dentro de gerador/
"""
import importlib.util
import os
import subprocess
import unittest

import requests

from datakit.sources import _http

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "publicar.py"))
_spec = importlib.util.spec_from_file_location("publicar", _PATH)
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)


def _cat(**ufs):
    return {"ufs": {uf: {"counts": c} for uf, c in ufs.items()}}


class Quedas(unittest.TestCase):
    OLD = _cat(SP={"cameras": 4772, "limits": 459962, "structs": 20092},
               GO={"cameras": 1515, "limits": 120941, "structs": 4915})

    def test_same_or_growing_passes(self):
        new = _cat(SP={"cameras": 4800, "limits": 459962, "structs": 20092},
                   GO={"cameras": 1515, "limits": 125310, "structs": 4915})
        self.assertEqual(pub.quedas(self.OLD, new), [])

    def test_a_source_down_is_caught(self):
        # CET-SP fora do ar: ~43 mil pontos de limite a menos em SP
        new = _cat(SP={"cameras": 4772, "limits": 416991, "structs": 20092},
                   GO={"cameras": 1515, "limits": 120941, "structs": 4915})
        self.assertEqual(pub.quedas(self.OLD, new), ["SP: limites 459962 -> 416991 (-9%)"])

    def test_small_states_do_not_trip_on_a_few_items(self):
        old = _cat(AC={"cameras": 5, "limits": 6301, "structs": 377})
        new = _cat(AC={"cameras": 3, "limits": 6301, "structs": 377})   # -40%, mas só 2 radares
        self.assertEqual(pub.quedas(old, new), [])

    def test_failures_list_never_reaches_the_published_catalog(self):
        self.assertNotIn("falhas", pub.rewrite_catalog({"ufs": {}, "falhas": ["CET-SP: erro"]}))

    def test_missing_state_and_big_drop(self):
        new = _cat(SP={"cameras": 2000, "limits": 459962, "structs": 20092})
        self.assertEqual(pub.quedas(self.OLD, new), ["GO: sumiu do catálogo", "SP: radares 4772 -> 2000 (-58%)"])

    def test_first_publication_has_nothing_to_compare(self):
        self.assertEqual(pub.quedas({}, self.OLD), [])


class Falhas(unittest.TestCase):
    def test_registered_once_and_listed(self):
        from datakit import falhas
        antes = len(falhas.lista())
        falhas.registrar("CET-SP", ConnectionResetError("reset"))
        falhas.registrar("CET-SP", ConnectionResetError("reset"))
        self.assertEqual(len(falhas.lista()), antes + 1)
        self.assertTrue(falhas.lista()[-1].startswith("CET-SP: ConnectionResetError"))


class OsmAtualizado(unittest.TestCase):
    def test_downloads_only_a_newer_extract(self):
        from datakit.sources.osm_pbf import needs_update
        old, new = "Mon, 28 Sep 2026 22:49:39 GMT", "Tue, 29 Sep 2026 21:10:02 GMT"
        self.assertTrue(needs_update(old, new))
        self.assertFalse(needs_update(new, new))
        self.assertFalse(needs_update(new, old))
        self.assertTrue(needs_update("", new))        # sem data guardada: baixa
        self.assertTrue(needs_update(old, ""))        # Geofabrik sem data: baixa


class Retentativa(unittest.TestCase):
    def _flaky(self, errors):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) <= len(errors):
                raise errors[len(calls) - 1]
            return "ok"
        return fn, calls

    def test_retries_a_dropped_connection_then_succeeds(self):
        fn, calls = self._flaky([requests.exceptions.ChunkedEncodingError("reset")])
        waits = []
        self.assertEqual(_http.com_retentativa(fn, dormir=waits.append), "ok")
        self.assertEqual((len(calls), waits), (2, [_http.ESPERA_S]))

    def test_gives_up_after_the_last_attempt(self):
        fn, calls = self._flaky([requests.ConnectionError("x")] * 5)
        with self.assertRaises(requests.ConnectionError):
            _http.com_retentativa(fn, dormir=lambda s: None)
        self.assertEqual(len(calls), _http.TENTATIVAS)

    def test_client_errors_are_not_retried(self):
        resp = requests.Response()
        resp.status_code = 404
        fn, calls = self._flaky([requests.HTTPError(response=resp)])
        with self.assertRaises(requests.HTTPError):
            _http.com_retentativa(fn, dormir=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertFalse(_http.transitorio(subprocess.CalledProcessError(22, "curl")))
        resp.status_code = 503
        self.assertTrue(_http.transitorio(requests.HTTPError(response=resp)))


if __name__ == "__main__":
    unittest.main()
