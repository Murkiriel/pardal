"""PRF — trechos aptos à fiscalização de velocidade com radar portátil (Res. Contran 798/2020, art. 7º, § 2º).

A PRF publica a relação na página "trechos críticos" como uma planilha do Google por período de validade ("Lista válida
de 12/09/2026 a 02/10/2026", ..., "Lista válida a partir de 03/10/2026"); a vigente é a "a partir de". A planilha
publicada se lê como CSV (`/pub?...&output=csv`): um cabeçalho em texto livre e depois UF, BR, km inicial e km final,
trechos de ~10 km (medido em 2026-10-08: 3.023 linhas, 27 UFs, ~29.900 km). Os números vêm com vírgula decimal e ponto
de milhar ("1.010,0"), às vezes com ponto decimal ("1.010.0"), e há erros de digitação ("12o"): a linha que não se lê,
de extensão zero ou de um contorno ou acesso ("101 (Contorno viário)", 9 linhas: o km não é o da BR) fica de fora e é
contada.

O trecho diz onde a fiscalização PODE acontecer, não onde acontece num dia: não é a posição de um radar.
"""
from __future__ import annotations

import csv
import html
import io
import re
from typing import List, Optional, Tuple

from datakit.common import RadarStretch
from datakit.sources._http import get_text

PAGE = "https://www.gov.br/prf/pt-br/assuntos/fiscalizacao-de-velocidade/trechos-criticos"

_CURRENT = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*>(?:(?!</a>).)*?a\s+partir\s+de\s+(\d{2})/(\d{2})/(\d{4})', re.S | re.I)
_KM = re.compile(r"\d+(?:\.\d{3})*(?:[.,]\d+)?")


def current_list(page: str) -> Tuple[str, str]:
    """(início da validade AAAA-MM-DD, endereço do CSV) da lista "válida a partir de" mais nova da página."""
    found = [(f"{y}-{m}-{d}", html.unescape(url)) for url, d, m, y in _CURRENT.findall(page)]
    if not found:
        raise ValueError("a página da PRF não tem a 'Lista válida a partir de ...'")
    valid_from, url = max(found)
    base, _, query = url.partition("?")
    params = [p for p in query.split("&") if p and not p.startswith("output=")]
    return valid_from, base.replace("/pubhtml", "/pub") + "?" + "&".join(params + ["output=csv"])


def km(text: str) -> Optional[float]:
    """O km da planilha: '90,0', '1.010,0', '1.010.0' (o último ponto é o decimal) ou '380'; None se não for número."""
    s = text.strip()
    if not _KM.fullmatch(s):
        return None
    if "," in s:
        return float(s.replace(".", "").replace(",", "."))
    whole, dot, frac = s.rpartition(".")
    if dot and len(frac) != 3:
        return float(whole.replace(".", "") + "." + frac)
    return float(s.replace(".", ""))


def parse(text: str) -> Tuple[List[RadarStretch], int]:
    """Os trechos da planilha e quantas linhas de trecho ficaram de fora (km ilegível, extensão zero, contorno)."""
    out: List[RadarStretch] = []
    skipped = 0
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4 or not re.fullmatch(r"[A-Z]{2}", row[0].strip()) or not row[1].strip()[:1].isdigit():
            continue
        start, end = km(row[2]), km(row[3])
        # "101 (Contorno viário)", "163 Acesso Aduana": o km do contorno ou acesso não é o da BR
        if not row[1].strip().isdigit() or start is None or end is None or end <= start:
            skipped += 1
            continue
        out.append(RadarStretch(row[0].strip(), int(row[1]), start, end))
    return out, skipped


def load() -> Tuple[str, List[RadarStretch], int]:
    """(início da validade, trechos, linhas que ficaram de fora) da lista vigente."""
    valid_from, url = current_list(get_text(PAGE))
    stretches, skipped = parse(get_text(url))
    return valid_from, stretches, skipped
