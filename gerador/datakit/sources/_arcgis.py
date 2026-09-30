"""Consulta a uma camada ArcGIS (FeatureServer/MapServer .../query), paginada.

O servidor devolve no máximo `maxRecordCount` registros por pedido (muitas vezes 1.000 ou 2.000,
qualquer que seja o `resultRecordCount` pedido) e avisa com `exceededTransferLimit`. Sem paginar,
uma camada que cresça além disso perde o resto em silêncio.
"""
from __future__ import annotations

from typing import List, Optional

from datakit.sources._http import get_json

MAX_PAGES = 1000


def query_all(url: str, params: dict, page: int = 1000, order_by: Optional[str] = None) -> List[dict]:
    """Todas as `features` de `url` com `params` (where, outFields, outSR...). Segue pedindo a
    partir do que já veio enquanto o servidor disser que cortou (`exceededTransferLimit`) ou
    devolver uma página cheia; resposta de erro (`error`) vira exceção, e um servidor que ignora
    `resultOffset` (repete a mesma página) também, em vez de laço infinito."""
    base = {"f": "json", **params}
    if order_by:
        base["orderByFields"] = order_by
    out: List[dict] = []
    offset = 0
    prev = None
    for _ in range(MAX_PAGES):
        j = get_json(url, params={**base, "resultOffset": offset, "resultRecordCount": page})
        if "error" in j:
            err = j["error"] or {}
            raise RuntimeError(f"ArcGIS {err.get('code')}: {err.get('message')}")
        feats = j.get("features") or []
        if feats and prev is not None and feats[0] == prev[0] and feats[-1] == prev[-1]:
            raise RuntimeError(f"{url}: o servidor ignora resultOffset (mesma página de novo)")
        out += feats
        if not feats or not (j.get("exceededTransferLimit") or len(feats) >= page):
            return out
        prev = feats
        offset += len(feats)
    raise RuntimeError(f"{url}: mais de {MAX_PAGES} páginas")
