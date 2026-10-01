"""Endereço escrito à mão -> nome de rua comparável, número de porta e cruzamento.

O Inmetro registra o local de cada medidor como texto livre ("AV. CABO BRANCO, Nº 2300",
"AV. T 7 X AV. CASTELO BRANCO", "R. ADALGISA CARNEIRO CAVALCANTI, 110 METRO ANTES DO Nº1431"). Aqui
sai o que dá para procurar num cadastro de endereços (sources/cnefe.py): a chave da rua (sem
acento, abreviaturas expandidas, sem o tipo do logradouro) e o número, ou as duas ruas de um
cruzamento. As duas formas somam 66% dos locais só com endereço (medição em FONTES.md).
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

# Tipo do logradouro: não entra na chave (o Inmetro escreve "AV.", o cadastro "AVENIDA" num campo à parte).
STREET_TYPES = {"AVENIDA", "RUA", "ALAMEDA", "PRACA", "RODOVIA", "ESTRADA", "TRAVESSA", "VIA", "LARGO",
                "VIADUTO", "TUNEL", "BECO", "VIELA", "LADEIRA", "PARQUE", "CORREDOR"}
ABBREVIATIONS = {
    "AV": "AVENIDA", "AVE": "AVENIDA", "A": "AVENIDA", "R": "RUA", "AL": "ALAMEDA", "PC": "PRACA", "PCA": "PRACA",
    "ROD": "RODOVIA", "EST": "ESTRADA", "ESTR": "ESTRADA", "TV": "TRAVESSA", "TRAV": "TRAVESSA",
    "DR": "DOUTOR", "DRA": "DOUTORA", "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "PRES": "PRESIDENTE",
    "GOV": "GOVERNADOR", "SEN": "SENADOR", "DEP": "DEPUTADO", "ALM": "ALMIRANTE", "GEN": "GENERAL",
    "GAL": "GENERAL", "CEL": "CORONEL", "CAP": "CAPITAO", "MAL": "MARECHAL", "MAR": "MARECHAL",
    "ENG": "ENGENHEIRO", "PE": "PADRE", "D": "DOM", "STA": "SANTA", "STO": "SANTO", "S": "SAO",
    "BRIG": "BRIGADEIRO", "DES": "DESEMBARGADOR", "MIN": "MINISTRO", "VER": "VEREADOR", "PREF": "PREFEITO",
    "CONS": "CONSELHEIRO", "VISC": "VISCONDE", "TEN": "TENENTE", "SGT": "SARGENTO", "MAJ": "MAJOR",
    "CDE": "CONDE", "NS": "NOSSA SENHORA", "SRA": "SENHORA", "N": "NOSSA",
}

# Número de porta: depois de vírgula, "N", "Nº", "NUMERO". Não é número de porta a vírgula de um
# decimal ("A MAIS 24,7 METROS", "KM 9,5") nem uma distância ("2 METROS APÓS A AV. …"): medido em São
# Paulo, esses casos punham o radar a centenas de metros, no número 5 ou 7 da avenida.
_NUMBER = re.compile(
    r"(?:(?<!\d),|\bN[º°o.]?(?=[\s\d])|\bNUMERO\b|\bNUM\b\.?)\s*(?:EM FRENTE AO?\s*)?(?:N[º°o.]?\s*)?"
    r"(\d{1,3}\.\d{3}|\d{1,5})\b(?![.,]\d)(?!\s*(?:M|MT|MTS|METRO|METROS|KM)\b)",
    re.I)
_CROSSING = re.compile(r"\s+X\s+|\s+ESQUINA COM(?: A)?\s+|\s+COM\s+", re.I)
_PARENS = re.compile(r"\([^)]*\)")
_AFTER_STREET = re.compile(r"\b(EM FRENTE|PROX\w*|ENTRE|OPOSTO|ALTURA|ALT)\b.*$", re.I)


def tokens(text: str) -> List[str]:
    """Palavras em maiúsculas, sem acento nem pontuação, com as abreviaturas expandidas."""
    s = unicodedata.normalize("NFKD", text.upper()).encode("ascii", "ignore").decode()
    return [ABBREVIATIONS.get(t, t) for t in re.sub(r"[^A-Z0-9 ]", " ", s).split()]


def street_key(text: str) -> str:
    """Chave da rua: título e nome, sem o tipo ("AV. PROF. X" e "AVENIDA PROFESSOR X" dão o mesmo)."""
    return " ".join(t for t in " ".join(tokens(text)).split() if t not in STREET_TYPES)


def _street_part(text: str) -> str:
    return _AFTER_STREET.sub("", re.split(r"\s-\s|,", text)[0])


def parse_number(local: str) -> Optional[Tuple[str, int]]:
    """(chave da rua, número) de "RUA TAL, Nº 123"; None se o texto não tem número de porta."""
    s = _PARENS.sub(" ", local)
    m = _NUMBER.search(s)
    if not m:
        return None
    number = int(m.group(1).replace(".", ""))
    key = street_key(_street_part(s[:m.start()]))
    return (key, number) if key and number > 0 else None


def parse_crossing(local: str) -> Optional[Tuple[str, str]]:
    """(chave da rua A, chave da rua B) de "RUA A X RUA B" / "RUA A ESQUINA COM RUA B"."""
    s = _PARENS.sub(" ", local)
    parts = _CROSSING.split(s, maxsplit=1)
    if len(parts) != 2:
        return None
    a, b = (street_key(re.split(r"\s-\s|,", p)[0]) for p in parts)
    return (a, b) if a and b and a != b else None
