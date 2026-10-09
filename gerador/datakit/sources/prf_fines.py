"""PRF — multas de velocidade, contadas por UF, BR e km (dados abertos da PRF, "Documento CSV de multas <ano>").

A página de dados abertos da PRF lista um zip por ano no Google Drive, com um CSV por mês (latin-1, `;`, atualizado todo
mês pela DIOP). Campos de lugar: UF, BR e km (inteiro; sem coordenada). As multas de velocidade são as do art. 218 do
CTB; o enquadramento mudou de formato no meio de 2025 ("218 I" para "art. 218, I"), e quem lê só um perde meio ano. A
data vem como AAAA-MM-DD (2026) ou DD/MM/AAAA. Os radares fixos das BR são do DNIT e da ANTT: a multa de velocidade da
PRF é das operações dela com o medidor portátil ou estático, ou seja, de onde a fiscalização de fato aconteceu.

Só a contagem sai daqui, somada por mês e km, nunca a data, a hora ou o veículo. A contagem dos 12 meses mais novos vai
para cada trecho apto ao radar portátil (radar_zones.place): "aqui a PRF multa", não a posição de um radar amanhã.
Medido em 2026-10-08: 7,2 milhões de multas de velocidade em 2025, 84% dentro da lista de trechos aptos; um terço dos
trechos da lista teve alguma.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
from collections import Counter
from typing import Dict, Optional, Tuple

from datakit.sources._http import CONNECT_TIMEOUT, get_text, guarded, session

PAGE = "https://www.gov.br/prf/pt-br/acesso-a-informacao/dados-abertos/dados-abertos-da-prf"
FOLDER = "prf_multas"
# um zip baixado há menos que isto vale para outra geração local; a geração mensal sempre baixa de novo
FRESH_S = 7 * 24 * 3600

_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_YEAR = re.compile(r"CSV\s+de\s+multas\s+(\d{4})", re.I)
_FILE = re.compile(r"drive\.google\.com/file/d/([\w-]+)")
SPEED = re.compile(r"^(art\.\s*)?218(?!\d)", re.I)
_ISO = re.compile(r"^(\d{4})-(\d{2})-\d{2}")
_BR_DATE = re.compile(r"^\d{2}/(\d{2})/(\d{4})")


def yearly_files(page: str) -> Dict[int, str]:
    """{ano: id do arquivo no Drive} das linhas "Documento CSV de multas <ano>"; o primeiro link de cada linha é o dela."""
    found: Dict[int, str] = {}
    for row in _ROW.findall(page):
        year, file = _YEAR.search(row), _FILE.search(row)
        if year and file:
            found.setdefault(int(year.group(1)), file.group(1))
    if not found:
        raise ValueError("a página de dados abertos da PRF não tem o 'Documento CSV de multas'")
    return found


def download_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


def _month(text: str) -> Optional[str]:
    m = _ISO.match(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _BR_DATE.match(text)
    return f"{m.group(2)}-{m.group(1)}" if m else None


def count(path: str) -> Counter:
    """Multas de velocidade do zip de um ano por (mês AAAA-MM, UF, BR, km); a linha sem data, BR ou km fica de fora."""
    out: Counter = Counter()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if not info.filename.lower().endswith(".csv"):
                continue
            with z.open(info) as raw:
                rows = csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""), delimiter=";")
                head = next(rows, [])
                col = {h.strip(): i for i, h in enumerate(head)}
                i_enq, i_date = col["Enquadramento da Infração"], col["Data da Infração (DD/MM/AAAA)"]
                i_uf, i_br, i_km, i_qtd = col["UF Infração"], col["BR Infração"], col["Km Infração"], col["Qtd Infrações"]
                for row in rows:
                    if len(row) < len(head) or not SPEED.match(row[i_enq].strip()):
                        continue
                    month = _month(row[i_date].strip())
                    try:
                        br = int(row[i_br])
                        km = int(float(row[i_km].replace(",", ".")))
                        qtd = int(row[i_qtd] or 1)
                    except ValueError:
                        continue
                    if month:
                        out[(month, row[i_uf].strip(), br, km)] += qtd
    return out


def first_month(last: str, months: int) -> str:
    """O primeiro mês (AAAA-MM) da janela de `months` meses do calendário que termina em `last`."""
    index = int(last[:4]) * 12 + int(last[5:7]) - 1 - (months - 1)
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def last_months(counts: Counter, months: int) -> Tuple[Tuple[str, str], Counter]:
    """((primeiro mês, último mês), multas por (UF, BR, km)) dos `months` meses do calendário que terminam no mês mais
    novo de `counts` (um mês sem multa no meio ainda conta como mês)."""
    if not counts:
        return ("", ""), Counter()
    last = max(k[0] for k in counts)
    first = first_month(last, months)
    by_km: Counter = Counter()
    for (m, uf, br, km), n in counts.items():
        if first <= m <= last:
            by_km[(uf, br, km)] += n
    return (first, last), by_km


def _fetch(file_id: str, dest: str) -> None:
    guarded(download_url(file_id), lambda: _fetch_once(file_id, dest))


def _fetch_once(file_id: str, dest: str) -> None:
    with session().get(download_url(file_id), stream=True, timeout=(CONNECT_TIMEOUT, 900)) as r:
        r.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if not zipfile.is_zipfile(tmp):
        os.remove(tmp)
        raise RuntimeError(f"o Drive não devolveu o zip das multas ({file_id})")
    os.replace(tmp, dest)


def load(raw_dir: str) -> Tuple[Tuple[str, str], Counter]:
    """((primeiro mês, último mês), multas por (UF, BR, km)) dos 12 meses mais novos, dos zips dos dois anos mais novos."""
    files = yearly_files(get_text(PAGE))
    folder = os.path.join(raw_dir, FOLDER)
    os.makedirs(folder, exist_ok=True)
    counts: Counter = Counter()
    for year in sorted(files, reverse=True)[:2]:
        dest = os.path.join(folder, f"{files[year]}.zip")
        if not (os.path.exists(dest) and time.time() - os.path.getmtime(dest) < FRESH_S):
            _fetch(files[year], dest)
        counts.update(count(dest))
    return last_months(counts, 12)
