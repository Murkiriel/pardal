"""Consulta ArcGIS paginada (datakit/sources/_arcgis.py), com respostas simuladas."""
import unittest
from unittest import mock


def _feat(i):
    return {"attributes": {"OBJECTID": i, "Velocidade": "60 kmh@60"}, "geometry": {"x": -47.9, "y": -15.8 + i * 1e-5}}


class _Server:
    """Servidor ArcGIS de mentira: `total` registros, devolve no máximo `cap` por página."""

    def __init__(self, total, cap, flag=True, ignore_offset=False):
        self.total, self.cap, self.flag, self.ignore_offset = total, cap, flag, ignore_offset
        self.calls = []

    def __call__(self, url, params=None, **kw):
        self.calls.append(dict(params))
        off = 0 if self.ignore_offset else int(params.get("resultOffset", 0))
        n = min(int(params.get("resultRecordCount", self.cap)), self.cap)
        feats = [_feat(i) for i in range(off, min(off + n, self.total))]
        j = {"features": feats}
        if self.flag and off + len(feats) < self.total:
            j["exceededTransferLimit"] = True
        return j


class QueryAll(unittest.TestCase):
    def _run(self, server, **kw):
        from datakit.sources import _arcgis
        with mock.patch.object(_arcgis, "get_json", server):
            return _arcgis.query_all("https://x/query", {"where": "1=1", "outFields": "*"}, **kw)

    def test_two_pages(self):
        s = _Server(1300, 1000)
        self.assertEqual(len(self._run(s, page=1000)), 1300)
        self.assertEqual([c["resultOffset"] for c in s.calls], [0, 1000])

    def test_server_cap_below_the_requested_page(self):
        """Pede 5.000, o servidor corta em 1.000 e avisa com exceededTransferLimit."""
        s = _Server(2355, 1000)
        feats = self._run(s, page=5000)
        self.assertEqual(len(feats), 2355)
        self.assertEqual([c["resultOffset"] for c in s.calls], [0, 1000, 2000])

    def test_full_page_without_flag_asks_once_more(self):
        s = _Server(2000, 1000, flag=False)
        self.assertEqual(len(self._run(s, page=1000)), 2000)
        self.assertEqual(len(s.calls), 3)   # a terceira volta vazia

    def test_error_response_raises(self):
        with self.assertRaises(RuntimeError):
            self._run(lambda url, params=None, **kw: {"error": {"code": 400, "message": "Invalid query"}})

    def test_server_that_ignores_the_offset_raises_instead_of_looping(self):
        with self.assertRaises(RuntimeError):
            self._run(_Server(5000, 1000, ignore_offset=True), page=1000)


class Sources(unittest.TestCase):
    """As fontes usam a consulta paginada: nada se perde quando o servidor corta a página."""

    def test_df_detran_gets_every_page(self):
        from datakit.sources import _arcgis, df_detran
        s = _Server(2355, 1000)
        with mock.patch.object(_arcgis, "get_json", s):
            cams, _ = df_detran.load("")
        self.assertEqual(len(cams), 2 * 2355)   # duas camadas

    def test_pmjp_gets_every_page(self):
        from datakit.sources import _arcgis, municipal
        s = _Server(1500, 1000)
        with mock.patch.object(_arcgis, "get_json", s):
            self.assertEqual(len(municipal._pmjp(None)), 1500)


if __name__ == "__main__":
    unittest.main()
