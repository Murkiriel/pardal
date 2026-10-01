"""Testes da publicação (scripts/publish.py) e das novas tentativas de download (_http).

    python -m unittest discover -s datakit/tests -t .     # de dentro de gerador/
"""
import importlib.util
import os
import subprocess
import unittest

import requests

from datakit.sources import _http

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "publish.py"))
_spec = importlib.util.spec_from_file_location("publicar", _PATH)
assert _spec is not None and _spec.loader is not None
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)


def _cat(**ufs):
    return {"ufs": {uf: {"counts": c} for uf, c in ufs.items()}}


class Drops(unittest.TestCase):
    OLD = _cat(SP={"cameras": 4772, "limits": 459962, "structs": 20092},
               GO={"cameras": 1515, "limits": 120941, "structs": 4915})

    def test_same_or_growing_passes(self):
        new = _cat(SP={"cameras": 4800, "limits": 459962, "structs": 20092},
                   GO={"cameras": 1515, "limits": 125310, "structs": 4915})
        self.assertEqual(pub.drops(self.OLD, new), [])

    def test_a_source_down_is_caught(self):
        # CET-SP fora do ar: ~43 mil pontos de limite a menos em SP
        new = _cat(SP={"cameras": 4772, "limits": 416991, "structs": 20092},
                   GO={"cameras": 1515, "limits": 120941, "structs": 4915})
        self.assertEqual(pub.drops(self.OLD, new), ["SP: limites 459962 -> 416991 (-9%)"])

    def test_small_states_do_not_trip_on_a_few_items(self):
        old = _cat(AC={"cameras": 5, "limits": 6301, "structs": 377})
        new = _cat(AC={"cameras": 3, "limits": 6301, "structs": 377})   # -40%, mas só 2 radares
        self.assertEqual(pub.drops(old, new), [])

    def test_failures_list_never_reaches_the_published_catalog(self):
        self.assertNotIn("failures", pub.rewrite_catalog({"ufs": {}, "failures": ["CET-SP: erro"]}))

    def test_missing_state_and_big_drop(self):
        new = _cat(SP={"cameras": 2000, "limits": 459962, "structs": 20092})
        self.assertEqual(pub.drops(self.OLD, new), ["GO: sumiu do catálogo", "SP: radares 4772 -> 2000 (-58%)"])

    def test_first_publication_has_nothing_to_compare(self):
        self.assertEqual(pub.drops({}, self.OLD), [])


class Failures(unittest.TestCase):
    def test_registered_once_and_listed(self):
        from datakit import failures
        before = len(failures.recorded())
        failures.record("CET-SP", ConnectionResetError("reset"))
        failures.record("CET-SP", ConnectionResetError("reset"))
        self.assertEqual(len(failures.recorded()), before + 1)
        self.assertTrue(failures.recorded()[-1].startswith("CET-SP: ConnectionResetError"))


class OsmUpdate(unittest.TestCase):
    def test_downloads_only_a_newer_extract(self):
        from datakit.sources.osm_pbf import needs_update
        old, new = "Mon, 28 Sep 2026 22:49:39 GMT", "Tue, 29 Sep 2026 21:10:02 GMT"
        self.assertTrue(needs_update(old, new))
        self.assertFalse(needs_update(new, new))
        self.assertFalse(needs_update(new, old))
        self.assertTrue(needs_update("", new))        # sem data guardada: baixa
        self.assertTrue(needs_update(old, ""))        # Geofabrik sem data: baixa


class Retries(unittest.TestCase):
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
        self.assertEqual(_http.with_retries(fn, sleep_fn=waits.append), "ok")
        self.assertEqual((len(calls), waits), (2, [_http.WAIT_S]))

    def test_gives_up_after_the_last_attempt(self):
        fn, calls = self._flaky([requests.ConnectionError("x")] * 5)
        with self.assertRaises(requests.ConnectionError):
            _http.with_retries(fn, sleep_fn=lambda s: None)
        self.assertEqual(len(calls), _http.ATTEMPTS)

    def test_client_errors_are_not_retried(self):
        resp = requests.Response()
        resp.status_code = 404
        fn, calls = self._flaky([requests.HTTPError(response=resp)])
        with self.assertRaises(requests.HTTPError):
            _http.with_retries(fn, sleep_fn=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertFalse(_http.is_transient(subprocess.CalledProcessError(22, "curl")))
        resp.status_code = 503
        self.assertTrue(_http.is_transient(requests.HTTPError(response=resp)))


if __name__ == "__main__":
    unittest.main()


class Resilience(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        _http._DEAD_HOSTS.clear()
        # as esperas entre tentativas (5 s, 10 s) não precisam acontecer de verdade no teste
        self._sleep = mock.patch.object(_http.time, "sleep", lambda s: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        _http._DEAD_HOSTS.clear()

    def test_host_that_never_connects_is_skipped_for_the_rest_of_the_run(self):
        calls = []

        def boom():
            calls.append(1)
            raise requests.ConnectTimeout("sem conexão")
        orig = _http.WAIT_S
        _http.WAIT_S = 0
        try:
            with self.assertRaises(requests.ConnectTimeout):
                _http.guarded("https://servicos.exemplo.gov.br/a.json", boom)
            self.assertEqual(len(calls), _http.ATTEMPTS)
            with self.assertRaises(ConnectionError):
                _http.guarded("https://servicos.exemplo.gov.br/b.json", boom)
            self.assertEqual(len(calls), _http.ATTEMPTS)           # nem tentou de novo
            self.assertEqual(_http.guarded("https://outro.gov.br/x", lambda: "ok"), "ok")
        finally:
            _http.WAIT_S = orig
            _http._DEAD_HOSTS.clear()

    def test_read_timeout_does_not_condemn_the_host(self):
        orig = _http.WAIT_S
        _http.WAIT_S = 0
        try:
            def slow():
                raise requests.ReadTimeout("lento")
            with self.assertRaises(requests.ReadTimeout):
                _http.guarded("https://lento.gov.br/a", slow)
            self.assertNotIn("lento.gov.br", _http._DEAD_HOSTS)
        finally:
            _http.WAIT_S = orig

    def test_cet_uses_the_saved_copy_and_warns_when_power_bi_fails(self):
        import json
        import tempfile
        from unittest import mock
        from datakit import failures
        from datakit.sources import cet_sp
        raw = tempfile.mkdtemp()
        rows = [{"CÓDIGO LOCAL": "1", "LATITUDE": -23.60, "LONGITUDE": -46.66, "DESCRIÇÃO DO LOCAL": "R X",
                 "ENQUADRAMENTOS": "V", "VELOCIDADE": "50 km/h", "DESATIVAÇÃO": None}]
        with mock.patch.object(cet_sp._powerbi, "model_id", return_value=1), \
                mock.patch.object(cet_sp._powerbi, "query_table", return_value=rows):
            self.assertEqual(len(cet_sp.load(raw)[0]), 1)             # leitura boa: guarda a cópia
        before = len(failures.warnings())
        with mock.patch.object(cet_sp._powerbi, "model_id", side_effect=RuntimeError("mudou")):
            self.assertEqual(len(cet_sp.load(raw)[0]), 1)             # falhou: usa a cópia
        self.assertEqual(len(failures.warnings()), before + 1)
        self.assertIn("CET-SP", failures.warnings()[-1])
        with open(os.path.join(raw, cet_sp.CACHE_FILE), encoding="utf-8") as f:
            self.assertEqual(json.load(f), rows)

    def test_inmetro_state_without_copy_is_a_failure_with_copy_a_warning(self):
        import tempfile
        from unittest import mock
        from datakit import failures
        from datakit.sources import inmetro
        raw = tempfile.mkdtemp()
        os.makedirs(os.path.join(raw, "inmetro"))
        with open(os.path.join(raw, "inmetro", "GO.json"), "w", encoding="utf-8") as f:
            f.write("[]")
        f0, a0 = len(failures.recorded()), len(failures.warnings())
        with mock.patch.object(inmetro, "get_bytes", side_effect=requests.ConnectTimeout("x")):
            inmetro.load(raw)
        self.assertEqual(len(failures.recorded()) - f0, 25)            # 25 UFs sem cópia (o DF não tem arquivo)
        self.assertEqual(len(failures.warnings()) - a0, 1)            # GO usou a cópia
        self.assertTrue(failures.warnings()[-1].startswith("Inmetro GO"))

    def test_warnings_never_reach_the_published_catalog(self):
        self.assertNotIn("warnings", pub.rewrite_catalog({"ufs": {}, "warnings": ["Inmetro DF: cópia"]}))


class OsmDatedFallback(unittest.TestCase):
    """O <região>-latest.osm.pbf da Geofabrik em laço de redirecionamento (301 para si mesmo)."""
    BASE = "https://download.geofabrik.de/south-america/brazil"
    LISTING = ('<a href="sudeste-260929.osm.pbf">sudeste-260929.osm.pbf</a>'
               '<a href="sudeste-260930.osm.pbf">sudeste-260930.osm.pbf</a>'
               '<a href="sudeste-260930.osm.pbf.md5">sudeste-260930.osm.pbf.md5</a>'
               '<a href="sudeste-latest.osm.pbf">sudeste-latest.osm.pbf</a>'
               '<a href="sul-261001.osm.pbf">sul-261001.osm.pbf</a>')
    STAMP = "Wed, 30 Sep 2026 21:10:02 GMT"

    def setUp(self):
        import tempfile
        from unittest import mock
        from datakit.sources import osm_pbf
        self.osm, self.mock = osm_pbf, mock
        self.raw = tempfile.mkdtemp()
        self.asked = []
        self._sleep = mock.patch.object(_http.time, "sleep", lambda s: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def _urlopen(self, latest_loops=True, dated_ok=True):
        """urlopen simulado: o -latest em laço (como o urllib relata) e o datado respondendo."""
        import io
        from urllib.error import HTTPError

        class Resp(io.BytesIO):
            headers = {"Last-Modified": self.STAMP}

        def fake(req, timeout=None):
            url = req.full_url
            self.asked.append(url.rsplit("/", 1)[-1])
            if url.endswith("-latest.osm.pbf") and latest_loops:
                raise HTTPError(url, 301, "redirect error that would lead to an infinite loop", None, None)
            if not dated_ok:
                raise HTTPError(url, 404, "Not Found", None, None)
            return Resp(b"pbf")
        return fake

    def _stored(self, stamp):
        with open(os.path.join(self.raw, "sudeste-latest.osm.pbf"), "wb") as f:
            f.write(b"stored!")
        with open(os.path.join(self.raw, "sudeste.last-modified"), "w") as f:
            f.write(stamp)

    def test_download_falls_back_to_the_newest_dated_file_and_warns(self):
        from datakit import failures
        f0 = len(failures.recorded())
        with self.mock.patch.object(self.osm, "urlopen", self._urlopen()), \
                self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING) as listing:
            ex = self.osm.ensure_extract("sudeste", self.raw)
        listing.assert_called_once_with(self.BASE + "/")
        self.assertEqual(self.asked, ["sudeste-latest.osm.pbf"] * _http.ATTEMPTS + ["sudeste-260930.osm.pbf"])
        self.assertEqual((ex.bytes, ex.file_date), (3, self.STAMP))
        self.assertTrue(ex.path.endswith("sudeste-latest.osm.pbf"))       # o nome guardado não muda
        self.assertEqual(len(failures.recorded()), f0)                    # aviso, não falha
        # (os avisos não se repetem na lista: outro teste pode já ter registrado este)
        self.assertIn("OSM sudeste: -latest falhou (HTTPError); usado sudeste-260930.osm.pbf", failures.warnings())

    def test_version_check_falls_back_and_the_download_goes_straight_to_the_dated_file(self):
        self._stored("Tue, 29 Sep 2026 21:10:02 GMT")
        heads = []

        def head(url):
            heads.append(url.rsplit("/", 1)[-1])
            if url.endswith("-latest.osm.pbf"):
                raise requests.TooManyRedirects("Exceeded 30 redirects.")
            return self.STAMP
        with self.mock.patch.object(self.osm, "_remote_last_modified", head), \
                self.mock.patch.object(self.osm, "urlopen", self._urlopen()), \
                self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING):
            ex = self.osm.ensure_extract("sudeste", self.raw)
        self.assertEqual(heads[-1], "sudeste-260930.osm.pbf")
        self.assertEqual(self.asked, ["sudeste-260930.osm.pbf"])         # sem insistir no -latest
        self.assertEqual((ex.bytes, ex.file_date), (3, self.STAMP))

    def test_stored_extract_is_kept_when_the_dated_file_is_not_newer(self):
        self._stored(self.STAMP)

        def head(url):
            if url.endswith("-latest.osm.pbf"):
                raise requests.TooManyRedirects("Exceeded 30 redirects.")
            return self.STAMP
        with self.mock.patch.object(self.osm, "_remote_last_modified", head), \
                self.mock.patch.object(self.osm, "urlopen", self._urlopen()), \
                self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING):
            ex = self.osm.ensure_extract("sudeste", self.raw)
        self.assertEqual((self.asked, ex.bytes), ([], 7))   # nada baixado

    def test_listing_down_raises_the_error_of_the_latest_link(self):
        from urllib.error import HTTPError
        with self.mock.patch.object(self.osm, "urlopen", self._urlopen()), \
                self.mock.patch.object(self.osm, "get_text", side_effect=requests.ConnectionError("fora do ar")):
            with self.assertRaises(HTTPError) as raised:
                self.osm.ensure_extract("sudeste", self.raw)
        self.assertEqual(raised.exception.code, 301)

    def test_dated_file_down_too_raises_its_error(self):
        from urllib.error import HTTPError
        with self.mock.patch.object(self.osm, "urlopen", self._urlopen(dated_ok=False)), \
                self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING):
            with self.assertRaises(HTTPError) as raised:
                self.osm.ensure_extract("sudeste", self.raw)
        self.assertEqual(raised.exception.code, 404)
        self.assertFalse(os.path.exists(os.path.join(self.raw, "sudeste-latest.osm.pbf")))

    def test_version_check_with_latest_and_dated_down_keeps_the_stored_file_as_a_failure(self):
        from datakit import failures
        self._stored(self.STAMP)
        f0 = len(failures.recorded())
        with self.mock.patch.object(self.osm, "_remote_last_modified",
                                    side_effect=requests.TooManyRedirects("Exceeded 30 redirects.")), \
                self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING):
            ex = self.osm.ensure_extract("sudeste", self.raw)
        self.assertEqual(ex.bytes, 7)
        self.assertEqual(len(failures.recorded()), f0 + 1)

    def test_newest_dated_file_of_the_region_only(self):
        with self.mock.patch.object(self.osm, "get_text", return_value=self.LISTING):
            self.assertEqual(self.osm._dated_url("sudeste"), self.BASE + "/sudeste-260930.osm.pbf")
            self.assertEqual(self.osm._dated_url("sul"), self.BASE + "/sul-261001.osm.pbf")
            with self.assertRaises(RuntimeError):
                self.osm._dated_url("norte")


class CommitDataOnly(unittest.TestCase):
    """publish.py --commit só leva brasil/, estados/ e catalogo.json — nunca o que mais estiver
    pendente no repositório (código do gerador não revisado, arquivos soltos)."""

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout

    def test_stray_files_stay_out_of_the_data_commit(self):
        import json
        import shutil
        import tempfile
        repo, dist = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "teste@example.invalid")
            self._git(repo, "config", "user.name", "teste")
            with open(os.path.join(repo, "LEIAME.md"), "w", encoding="utf-8") as f:
                f.write("x")
            with open(os.path.join(repo, "catalog.json"), "w", encoding="utf-8") as f:   # nome do schema 1
                f.write('{"schema": 1, "ufs": {}}')
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "inicial")
            # pendências que NÃO podem entrar no commit de dados
            os.makedirs(os.path.join(repo, "gerador"))
            with open(os.path.join(repo, "gerador", "codigo_novo.py"), "w", encoding="utf-8") as f:
                f.write("print(1)\n")
            with open(os.path.join(repo, "solto.txt"), "w", encoding="utf-8") as f:
                f.write("pessoal\n")
            with open(os.path.join(repo, "LEIAME.md"), "w", encoding="utf-8") as f:
                f.write("editado\n")
            # um build mínimo em dist
            os.makedirs(os.path.join(dist, "estados", "GO"))
            with open(os.path.join(dist, "estados", "GO", "radares.csv"), "w", encoding="utf-8") as f:
                f.write("lat,lng\n-16,-49\n")
            with open(os.path.join(dist, "catalogo.json"), "w", encoding="utf-8") as f:
                json.dump({"built_at": "2026-09-30T00:00:00Z", "ufs": {"GO": {"counts": {"cameras": 1}}}}, f)
            self.assertEqual(pub.main(["--repo", repo, "--dist", dist, "--commit"]), 0)
            committed = set(self._git(repo, "show", "--name-only", "--pretty=format:", "HEAD").split())
            self.assertEqual(committed, {"catalogo.json", "catalog.json", "estados/GO/radares.csv"})
            self.assertFalse(os.path.exists(os.path.join(repo, "catalog.json")))   # o nome antigo sai
            self.assertEqual(self._git(repo, "ls-files", "catalog.json"), "")
            pending = self._git(repo, "status", "--porcelain")
            self.assertIn("solto.txt", pending)
            self.assertIn("gerador/", pending)
            self.assertIn("LEIAME.md", pending)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(dist, ignore_errors=True)


class BuildInProgress(unittest.TestCase):
    """Enquanto o build roda (ou se ele caiu no meio), data/dist tem pacotes de dois builds
    misturados: o publish.py não monta nada."""

    def _dist(self, tmp):
        import json
        dist = os.path.join(tmp, "dist")
        os.makedirs(dist)
        with open(os.path.join(dist, "catalogo.json"), "w", encoding="utf-8") as f:
            json.dump({"ufs": {}}, f)
        return dist

    def test_publish_refuses_while_the_marker_exists(self):
        import tempfile
        from datakit import build
        with tempfile.TemporaryDirectory() as tmp:
            dist, repo = self._dist(tmp), os.path.join(tmp, "repo")
            os.makedirs(repo)
            with open(os.path.join(dist, build.IN_PROGRESS_MARKER), "w", encoding="utf-8") as f:
                f.write("2026-09-30T00:00:00Z\n")
            with self.assertRaises(SystemExit) as cm:
                pub.main(["--repo", repo, "--dist", dist, "--accept-failures", "--accept-drop"])
            self.assertIn("andamento", str(cm.exception.code))
            self.assertEqual(os.listdir(repo), [])

    def _run_build(self, tmp, catalog_fails=False):
        import json
        from unittest import mock
        from datakit import build
        dist = os.path.join(tmp, "dist")
        seen = []

        def fake_one(uf, ctx, packs_dir, split):
            seen.append(os.path.exists(os.path.join(dist, build.IN_PROGRESS_MARKER)))

        def fake_catalog(packs, d):
            if catalog_fails:
                raise RuntimeError("disco cheio")
            with open(os.path.join(d, "catalogo.json"), "w", encoding="utf-8") as f:
                json.dump({"ufs": {}}, f)
            return {"ufs": {}}

        with mock.patch.object(build, "build_one", fake_one), \
                mock.patch.object(build, "build_catalog", fake_catalog):
            try:
                build.main(["--uf", "GO", "--no-polygon", "--dist", dist, "--packs", os.path.join(tmp, "p")])
            except RuntimeError:
                pass
        return seen, os.path.exists(os.path.join(dist, build.IN_PROGRESS_MARKER))

    def test_build_marks_while_running_and_clears_at_the_end(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run_build(tmp), ([True], False))

    def test_marker_stays_when_the_build_dies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run_build(tmp, catalog_fails=True), ([True], True))


class HttpSession(unittest.TestCase):
    """Uma sessão HTTP para o build inteiro: as requisições ao mesmo servidor (27 UFs do Inmetro,
    páginas do ArcGIS) reusam a conexão. Mesmos cabeçalhos e tempos de antes."""

    def test_one_shared_session(self):
        self.assertIs(_http.session(), _http.session())
        self.assertIsInstance(_http.session(), requests.Session)

    def test_get_goes_through_the_session_with_the_same_arguments(self):
        from unittest import mock

        class Resp:
            content, text = b"x", "x"

            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": 1}

        with mock.patch.object(_http.session(), "get", return_value=Resp()) as get:
            self.assertEqual(_http.get_json("https://x.gov.br/a", params={"q": 1}), {"ok": 1})
            self.assertEqual(_http.get_bytes("https://x.gov.br/b", headers={"A": "b"}, timeout=5), b"x")
        self.assertEqual(get.call_args_list[0], mock.call(
            "https://x.gov.br/a", headers=_http.UA, timeout=(_http.CONNECT_TIMEOUT, _http.HTTP_TIMEOUT),
            params={"q": 1}))
        self.assertEqual(get.call_args_list[1], mock.call(
            "https://x.gov.br/b", headers={"A": "b"}, timeout=(_http.CONNECT_TIMEOUT, 5), params=None))
