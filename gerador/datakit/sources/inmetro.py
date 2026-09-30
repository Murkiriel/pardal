"""Inmetro — PSIE, medidores de velocidade aferidos (todo o país, todos os órgãos).

`https://servicos.rbmlq.gov.br/dados-abertos/{UF}/medidores.json`, mensal, Creative Commons
(metadados em dados-abertos/metadados_medidores_velocidade.pdf). Sem coordenada: o local é
texto ("GO-469, KM 027+244M" ou um endereço). Aqui só vira `Meter`; quem casa com os
radares conhecidos é `datakit.inmetro_status`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

from datakit.common.lrs import parse_km, parse_road
from datakit.common.ufs import UF_BBOX
from datakit.sources._http import get_bytes

URL = "https://servicos.rbmlq.gov.br/dados-abertos/{uf}/medidores.json"


@dataclass(frozen=True)
class Meter:
    uf: str
    municipality: str
    local: str
    fixed: bool
    valid: bool                    # aferição dentro da validade e último resultado "Aprovado"
    road: Optional[Tuple[str, int]]  # ("BR", 60) / ("GO", 469)
    km: Optional[float]
    limit_kmh: Optional[int]       # menor velocidade nominal entre as faixas


def parse(records: list, uf: str, today: date) -> List[Meter]:
    out: List[Meter] = []
    for r in records:
        local = str(r.get("LocalVerificacao") or "")
        try:
            valid_until = datetime.strptime(str(r.get("DataValidade") or ""), "%d/%m/%Y").date()
        except ValueError:
            valid_until = None
        valid = valid_until is not None and valid_until >= today and r.get("UltimoResultado") == "Aprovado"
        speeds = []
        for f in r.get("Faixas") or []:
            v = str(f.get("VelocidadeNominal") or "").strip()
            if v.isdigit() and 20 <= int(v) <= 130:
                speeds.append(int(v))
        road = parse_road(local)
        km = parse_km(local) if road else None
        out.append(Meter(
            uf=str(r.get("SiglaUf") or uf).upper(), municipality=str(r.get("Municipio") or ""),
            local=local, fixed=r.get("TipoMedidor") == "Fixo", valid=valid,
            road=road, km=km, limit_kmh=min(speeds) if speeds else None,
        ))
    return out


def load(raw_dir: str, today: Optional[date] = None) -> List[Meter]:
    """Todas as UFs; UF que falha no download fica de fora (e é avisada)."""
    today = today or date.today()
    folder = os.path.join(raw_dir, "inmetro")
    os.makedirs(folder, exist_ok=True)
    meters: List[Meter] = []
    for uf in sorted(UF_BBOX):
        if uf == "DF":
            continue  # não há arquivo do DF (404 sempre): os medidores de Brasília estão no de GO
        path = os.path.join(folder, f"{uf}.json")
        try:
            data = get_bytes(URL.format(uf=uf))
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e:  # noqa: BLE001
            from datakit import failures
            if not os.path.exists(path):
                failures.record(f"Inmetro {uf}", e)
                continue
            when = date.fromtimestamp(os.path.getmtime(path)).isoformat()
            failures.warn(f"Inmetro {uf}", f"download falhou ({type(e).__name__}); usada a cópia de {when}")
        try:
            with open(path, encoding="utf-8-sig") as f:
                records = json.load(f)
        except ValueError:
            continue  # cópia corrompida/não-JSON: melhor sem esta UF do que parar o build
        meters.extend(parse(records, uf, today))
    return meters
