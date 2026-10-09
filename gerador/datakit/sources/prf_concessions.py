"""PRF — radares fixos das concessões federais, pela planilha que a página "Radares fixos" da PRF mostra.

A página oficial (gov.br) não traz arquivo: põe num iframe uma tabela externa ("TABELA DE RADARES FIXOS",
`mapas-html.vercel.app/tabela.html`), que lê uma planilha por um Google Apps Script (JSON público, sem chave,
`{"records": [...]}`). O endereço do script sai da tabela, e o da tabela, da página: quem mudar um deles não quebra os
outros. Medido em 2026-10-08: 1.589 equipamentos de 33 concessionárias em 15 UFs, 1.205 com coordenada; ATIVO 819,
INOPERANTE 491, "E/L ou CERTIFICADO VENCIDOS" 279 (estudo técnico ou aferição vencidos). Cada um traz UF, BR, km, o
sentido do km (crescente, decrescente, ambos), a coordenada e o limite de leves e de pesados.

Fonte informal: fora do gov.br, sem licença escrita, apontada pela página oficial da PRF. Uso (apply): o radar do pacote
que a planilha dá como inoperante ou vencido (a até MATCH_M), sem equipamento ativo dela por perto, sai inativo; o
equipamento ativo dela sem radar nenhum do pacote a até MATCH_M entra, com a fonte "PRF", o limite de leves e o rumo pelo
SNV. Em Goiás (03/10/2026): 10 ativos faltavam no pacote e 37 radares ativos no pacote estavam inoperantes ou vencidos.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from datakit.common import Camera
from datakit.common.direction import direction_on, parse_increasing
from datakit.common.spatial import Grid
from datakit.sources._http import get_json, get_text, in_br

PAGE = "https://www.gov.br/prf/pt-br/assuntos/fiscalizacao-de-velocidade/radares-fixos"
SOURCE = "PRF"
# Mesmo equipamento: a coordenada da planilha cai a algumas dezenas de metros da da ANTT (medido em GO: até 150 m).
MATCH_M = 150.0
SNAP_M = 200.0

_TABLE = re.compile(r'<iframe\b[^>]*title="[^"]*TABELA[^"]*"[^>]*src="([^"]+)"', re.I)
_SCRIPT = re.compile(r"https://script\.google\.com/macros/s/[\w-]+/exec")
_STATES: Dict[str, str] = {"ATIVO": "ACTIVE", "INOPERANTE": "INOPERATIVE", "E/L OU CERTIFICADO VENCIDOS": "EXPIRED"}


@dataclass(frozen=True)
class Record:
    """Um equipamento da planilha: situação (ACTIVE, INOPERATIVE, EXPIRED), lugar, sentido do km (None: os dois ou não
    dito) e o limite de leves."""
    state: str
    uf: str
    br: int
    km: float
    increasing: Optional[bool]
    lat: float
    lng: float
    light_kmh: Optional[int]


def table_url(page: str) -> str:
    """O endereço da tabela, do iframe "TABELA DE RADARES FIXOS" da página."""
    m = _TABLE.search(page)
    if not m:
        raise ValueError("a página 'Radares fixos' da PRF não tem a tabela")
    return html.unescape(m.group(1))


def script_url(table: str) -> str:
    """O endereço do Apps Script que a tabela lê."""
    m = _SCRIPT.search(table)
    if not m:
        raise ValueError("a tabela de radares fixos não aponta um Apps Script")
    return m.group(0)


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(data: dict) -> List[Record]:
    """Os equipamentos com situação conhecida e coordenada no Brasil; o resto fica de fora."""
    out: List[Record] = []
    for r in data.get("records", []):
        state = _STATES.get(str(r.get("ESTADO") or "").strip().upper())
        lat, lng, km = _number(r.get("LATITUDE")), _number(r.get("LONGITUDE")), _number(r.get("LOCAL"))
        br = _number(r.get("BR"))
        if state is None or lat is None or lng is None or km is None or br is None or not in_br(lat, lng):
            continue
        light = _number(r.get("VELOCIDADE LEVE"))
        out.append(Record(state, str(r.get("UF") or "").strip().upper(), int(br), km,
                          parse_increasing(str(r.get("SENTIDO") or "")), lat, lng, int(light) if light else None))
    return out


def load() -> List[Record]:
    """Os equipamentos da planilha, pela página da PRF, a tabela e o Apps Script."""
    return parse(get_json(script_url(get_text(table_url(get_text(PAGE))))))


def _direction(routes, r: Record) -> Optional[int]:
    if routes is None or r.increasing is None:
        return None
    hit = routes.nearest(r.br, r.uf, (r.lat, r.lng), SNAP_M)
    return None if hit is None else direction_on(hit[0], hit[1], r.increasing)


def apply(cams: Sequence[Camera], records: Sequence[Record], routes) -> Tuple[List[Camera], dict]:
    """(radares com a situação da planilha e os ativos que faltavam, contagens); `routes` é SnvRoutes ou None."""
    active = Grid(MATCH_M, ((r.lat, r.lng, r) for r in records if r.state == "ACTIVE"))
    down = Grid(MATCH_M, ((r.lat, r.lng, r) for r in records if r.state != "ACTIVE"))
    out: List[Camera] = []
    retired = 0
    for c in cams:
        if c.active and any(down.within(c.lat, c.lng)) and not any(active.within(c.lat, c.lng)):
            c = replace(c, active=False)
            retired += 1
        out.append(c)
    # o que já está no pacote; entre os novos, os dois sentidos de um lugar são dois radares, a linha repetida é uma
    known = Grid(MATCH_M, ((c.lat, c.lng, c) for c in out))
    added = []
    seen = set()
    for r in records:
        key = (round(r.lat, 5), round(r.lng, 5), r.increasing)
        if r.state == "ACTIVE" and key not in seen and not any(known.within(r.lat, r.lng)):
            seen.add(key)
            added.append(Camera(r.lat, r.lng, limit_kmh=r.light_kmh, source=SOURCE, direction_deg=_direction(routes, r)))
    return out + added, {"records": len(records), "retired": retired, "added": len(added)}
