"""Porto Alegre — EPTC, medidores eletrônicos de velocidade.

A localização de cada medidor está no relatório Power BI público "Localização da Fiscalização por
Equipamentos Eletrônicos", ligado à página "Medidores Eletrônicos de Velocidade" da EPTC (ObservaMob),
lido por datakit/sources/_powerbi.py como o da CET-SP. Tabela `BaseMevFluxo`: coordenada, sentido,
limite, tipo de controlador e situação. O relatório fica no cluster do norte da Europa do Power BI.

Pardal (fixo controlador), lombada eletrônica (fixo redutor) e DAS (detector de avanço de sinal que
também mede velocidade) viram radar fixo; o medidor portátil é ponto de operação do radar móvel, não
equipamento, e fica de fora. Limite com mais de um valor ("40km/h_60km/h", por faixa ou por tipo de
veículo): o menor. Só os de situação "Ativo". O sentido (BC/CB = bairro-centro/centro-bairro, NS/SN,
IC/CI) não é usado por enquanto.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from datakit.context import SourceData, BuildContext
from datakit.common import Camera, CameraKind, in_bbox
from datakit.common.model import Limit
from datakit.sources import _powerbi
from datakit.sources._http import in_br, to_float

PBI_VIEW = ("https://app.powerbi.com/view?r=eyJrIjoiM2ZiY2M4Y2QtOWQ2Ny00NTUzLTk0NTktZTZkMTc5NzVkOThmIiwidCI6"
            "IjA0NmFkMWJjLWE5NTYtNDA0OC05ODAzLTc4MTIyN2FhMDAzOSIsImMiOjh9")
PBI_API = "https://wabi-north-europe-api.analysis.windows.net"
PBI_TABLE = "BaseMevFluxo"
PBI_COLS = ["IDLocalMEV", "LOCAL", "LATITUDE", "LONGITUDE", "SENTIDO", "LIMITEKMH", "CONTROLADOR", "SITUACAO"]
CACHE_FILE = "eptc_medidores.json"

_FIXED = ("fixo controlador", "fixo redutor", "das")
_SPEED = re.compile(r"(\d{2,3})\s*km", re.I)


def _limit(raw) -> Optional[int]:
    speeds = [int(v) for v in _SPEED.findall(str(raw or "")) if 20 <= int(v) <= 130]
    return min(speeds) if speeds else None


def rows_to_cameras(rows: List[Dict], bbox: Optional[Tuple[float, float, float, float]] = None) -> List[Camera]:
    """Linhas da tabela da EPTC -> radares fixos ativos. Puro, testado."""
    out: List[Camera] = []
    for r in rows:
        if str(r.get("SITUACAO") or "").strip().lower() != "ativo":
            continue
        if not str(r.get("CONTROLADOR") or "").strip().lower().startswith(_FIXED):
            continue
        lat, lng = to_float(r.get("LATITUDE")), to_float(r.get("LONGITUDE"))
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, _limit(r.get("LIMITEKMH")), "EPTC", True))
    return out


def _rows_with_fallback(raw_dir: str) -> List[Dict]:
    """A tabela pelo Power BI; a leitura boa fica em data/raw/ e, se o Power BI falhar (API não
    documentada), vale essa cópia com aviso (failures.warn). Sem cópia, a falha sobe."""
    import json
    import os
    from datetime import date
    path = os.path.join(raw_dir, CACHE_FILE)
    try:
        key = _powerbi.resource_key(PBI_VIEW)
        rows = _powerbi.query_table(PBI_API, key, _powerbi.model_id(PBI_API, key), PBI_TABLE, PBI_COLS)
        if not rows:
            raise RuntimeError("tabela da EPTC veio vazia")
    except Exception as e:  # noqa: BLE001
        if not os.path.exists(path):
            raise
        from datakit import failures
        when = date.fromtimestamp(os.path.getmtime(path)).isoformat()
        failures.warn("EPTC (radares)", f"Power BI falhou ({type(e).__name__}); usada a cópia de {when}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    os.makedirs(raw_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, default=str)
    return rows


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    return rows_to_cameras(_rows_with_fallback(raw_dir), bbox), []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
