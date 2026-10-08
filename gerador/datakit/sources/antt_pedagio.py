"""ANTT — dataset "Praça de Pedágio" (concessões federais) -> praças de pedágio.

CSV com latitude/longitude prontos, delimitado por ';', encoding latin-1. Só as 'Ativo' com ponto no Brasil. O nome da
praça vai junto ("P2 GOIANÁPOLIS"); a que tem "free flow" no nome é pórtico eletrônico (paga sem parar) e vira
FREE_FLOW, as outras PLAZA. Sem preço: as tabelas de tarifa da ANTT por concessionária estão desatualizadas.
"""
from __future__ import annotations

import csv
import io
from typing import List

from datakit.common import Toll
from datakit.sources._http import ckan_resources, get_bytes, in_br, newest, to_float

CKAN = "https://dados.antt.gov.br/api/3/action/package_show?id=praca-de-pedagio"


def decode(raw: bytes) -> str:
    return raw.decode("latin-1")


def parse(text: str) -> List[Toll]:
    out: List[Toll] = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        if (row.get("situacao") or "").strip().lower() != "ativo":
            continue
        lat, lng = to_float(row.get("latitude")), to_float(row.get("longitude"))
        if lat is None or lng is None or not in_br(lat, lng):
            continue
        name = (row.get("praca_de_pedagio") or "").strip()
        kind = "FREE_FLOW" if "free flow" in name.lower() else "PLAZA"
        out.append(Toll(lat, lng, kind, name, "ANTT"))
    return out


def load() -> List[Toll]:
    """As praças ativas do CSV mais novo do dataset."""
    res = newest(ckan_resources(CKAN), "CSV", "dados dos pra")
    return parse(decode(get_bytes(res["url"])))
