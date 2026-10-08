"""DNIT — Condições do Pavimento (ICM), levantamento mensal das rodovias federais pavimentadas sob o DNIT.

CSV no CKAN do DNIT (`condicoes-do-pavimento`, "Condições Do Pavimento Levantamentos <mês>/<ano>"), separado por ';',
UTF-8 com BOM: uma linha por km e sentido, com a nota de cada defeito (Bom, Regular, Ruim, Péssimo). Entra só a
**panela** (buraco) ruim ou péssima ("Pessimo" também vem sem acento). O sentido é o do km: decrescente quando o km
inicial é maior que o final (ou Sentido = D). O mês vem de Ano/Mes. Medido em agosto de 2026: 93.334 km avaliados,
4.757 com panela ruim ou péssima. Só a malha do DNIT: as concessões não entram no levantamento.
"""
from __future__ import annotations

import csv
import io
from typing import List, Tuple

from datakit.common import RoughKm
from datakit.sources._http import ckan_resources, get_bytes, newest, to_float

CKAN = "https://servicos.dnit.gov.br/dadosabertos/api/3/action/package_show?id=condicoes-do-pavimento"
_LEVEL = {"ruim": "BAD", "pessimo": "VERY_BAD", "péssimo": "VERY_BAD"}


def parse(text: str) -> Tuple[str, List[RoughKm]]:
    """(mês AAAA-MM, os km com panela ruim ou péssima)."""
    month = ""
    out: List[RoughKm] = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"):
        level = _LEVEL.get((row.get("Panela") or "").strip().lower())
        k0, k1 = to_float(row.get("Km_Inicial")), to_float(row.get("Km_Final"))
        road = "".join(ch for ch in (row.get("Rodovia") or "") if ch.isdigit())
        if not month and (row.get("Ano") or "").strip().isdigit() and (row.get("Mes") or "").strip().isdigit():
            month = f"{int(row['Ano']):04d}-{int(row['Mes']):02d}"
        if level is None or k0 is None or k1 is None or k0 == k1 or not road:
            continue
        decreasing = k0 > k1 or (row.get("Sentido") or "").strip().upper() == "D"
        out.append(RoughKm((row.get("UF") or "").strip(), int(road), min(k0, k1), max(k0, k1), decreasing, level))
    return month, out


def load() -> Tuple[str, List[RoughKm]]:
    """O levantamento mais novo das pavimentadas."""
    res = newest(ckan_resources(CKAN), "CSV", "Levantamentos")
    return parse(get_bytes(res["url"]).decode("utf-8-sig", errors="replace"))
