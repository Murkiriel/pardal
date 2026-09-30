"""Leitura de relatório do Power BI publicado na web ("Publicar na Web", sem login).

O relatório público consulta o modelo por uma API sem autenticação, com a chave que está no
próprio link (`app.powerbi.com/view?r=<base64 de {"k": chave, "t": tenant}>`). Aqui: uma
consulta que devolve colunas de uma tabela inteira, e o decodificador do formato comprimido
da resposta (dicionários de valores, bits de "repete o anterior" e de "nulo").

Não é API documentada: se a Microsoft mudar o formato, a fonte falha e o build registra.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from datakit.sources._http import with_retries

WINDOW = 30000  # linhas por consulta; as tabelas usadas aqui têm poucos milhares


def resource_key(view_url_or_r: str) -> str:
    """Chave do relatório a partir do link de visualização (ou só do parâmetro r)."""
    r = view_url_or_r.split("r=", 1)[-1].split("&", 1)[0]
    return json.loads(base64.b64decode(r + "=" * (-len(r) % 4)))["k"]


def _post(api: str, key: str, path: str, body: dict) -> dict:
    from datakit.sources._http import HTTP_TIMEOUT, UA, session

    def once():
        x = session().post(f"{api}{path}", json=body, timeout=HTTP_TIMEOUT,
                          headers={**UA, "X-PowerBI-ResourceKey": key, "Content-Type": "application/json"})
        x.raise_for_status()
        return x.json()
    return with_retries(once, "Power BI " + path)


def model_id(api: str, key: str) -> int:
    """Id do modelo de dados por trás do relatório."""
    from datakit.sources._http import HTTP_TIMEOUT, UA, session

    def once():
        x = session().get(f"{api}/public/reports/{key}/modelsAndExploration?preferReadOnlySession=true",
                         headers={**UA, "X-PowerBI-ResourceKey": key}, timeout=HTTP_TIMEOUT)
        x.raise_for_status()
        return x.json()["models"][0]["id"]
    return with_retries(once, "Power BI modelo")


def query_table(api: str, key: str, model_id: int, entity: str, columns: List[str]) -> List[Dict[str, Any]]:
    """Todas as linhas de `entity` com as `columns` pedidas, como dicionários."""
    q = {"Version": 2, "From": [{"Name": "t", "Entity": entity, "Type": 0}],
         "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": c}, "Name": f"t.{c}"}
                    for c in columns]}
    body = {"version": "1.0.0", "modelId": model_id, "queries": [{"Query": {"Commands": [{
        "SemanticQueryDataShapeCommand": {"Query": q, "Binding": {
            "Primary": {"Groupings": [{"Projections": list(range(len(columns)))}]},
            "DataReduction": {"DataVolume": 6, "Primary": {"Window": {"Count": WINDOW}}}, "Version": 1}}}]}}]}
    rows = decode(_post(api, key, "/public/reports/querydata?synchronous=true", body))
    return [dict(zip(columns, r)) for r in rows]


def decode(resp: dict) -> List[List[Optional[Any]]]:
    """Resposta do querydata -> linhas. Cada linha traz em "C" só os valores que mudaram; o
    bit i de "R" diz que a coluna i repete a linha anterior, o de "Ø" que ela é nula; valores
    de texto vêm como índice no dicionário da coluna ("DN")."""
    ds = resp["results"][0]["result"]["data"]["dsr"]["DS"][0]
    if ds.get("RT"):   # token para continuar = veio só a primeira janela ("IC" true = completa)
        raise RuntimeError("Power BI devolveu a tabela incompleta (mais linhas que a janela)")
    dicts = ds.get("ValueDicts", {})
    raw = ds["PH"][0]["DM0"]
    if not raw:
        return []
    schema = raw[0]["S"]
    out: List[List[Optional[Any]]] = []
    prev: List[Optional[Any]] = [None] * len(schema)
    for r in raw:
        vals = list(r.get("C", []))
        rep, nul = r.get("R", 0), r.get("Ø", 0)
        row: List[Optional[Any]] = []
        for i, col in enumerate(schema):
            if rep & (1 << i):
                row.append(prev[i])
            elif nul & (1 << i):
                row.append(None)
            else:
                v = vals.pop(0)
                if col.get("DN") and isinstance(v, int):
                    v = dicts[col["DN"]][v]
                row.append(v)
        prev = row
        out.append(row)
    return out
