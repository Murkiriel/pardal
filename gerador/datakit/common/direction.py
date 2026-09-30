"""Sentido "crescente/decrescente" (do km) -> rumo de bússola do trânsito.

Radares e placas das fontes oficiais vêm com o sentido em relação ao km da rodovia. O rumo
sai da geometria com km (SNV para as BRs, malha da GOINFRA para as GO): a direção em que o km
cresce naquele ponto, virada 180° no decrescente. Medido: com o radar claramente numa das
pistas (25-80 m do eixo), o lado da pista confirma o sentido em 96% (ANTT/SNV) e 94-100%
(DER-GO/GOINFRA) dos casos; ver FONTES.md.
"""
from __future__ import annotations

from typing import Optional

from datakit.common.lrs import MeasuredLine
from datakit.common.model import to_dir


def parse_increasing(text: Optional[str]) -> Optional[bool]:
    """'Crescente' -> True, 'Decrescente' -> False; os dois sentidos, vazio ou outro -> None."""
    t = (text or "").strip().lower()
    if t == "crescente":
        return True
    if t == "decrescente":
        return False
    return None


CARDINAL = {"N": 0.0, "L": 90.0, "S": 180.0, "O": 270.0}
# Via de mão dupla: o rumo é a orientação da via mais perto do ponto cardeal nominal, e só vale
# se ficar a até HINT_MAX_DEG dele (via quase perpendicular ao sentido nominal = ambíguo).
HINT_MAX_DEG = 60.0


def hint_from_text(text: Optional[str]) -> Optional[str]:
    """'Norte', 'Sul (Marginal)', 'leste', 'O' -> 'N'/'S'/'L'/'O'; o resto -> None."""
    t = (text or "").strip().upper()
    for word, h in (("NORTE", "N"), ("SUL", "S"), ("LESTE", "L"), ("OESTE", "O")):
        if t.startswith(word):
            return h
    return t if t in CARDINAL else None


def hint_target(hint: str) -> float:
    """Rumo nominal de um heading_hint: ponto cardeal ('N', 'S', 'L', 'O') ou '@<graus>'
    (rumo já calculado pela fonte, ex. centro->bairro da CET)."""
    if hint in CARDINAL:
        return CARDINAL[hint]
    if hint.startswith("@"):
        return float(hint[1:]) % 360.0
    if hint == "*":
        raise ValueError("'*' não tem rumo nominal: o sentido vem só da posição (osm_pbf.resolve_probes)")
    raise ValueError(f"heading_hint inválido: {hint!r}")


def orient_to_hint(bearing: float, oneway: bool, hint: str) -> Optional[float]:
    """Rumo do trânsito, a partir do rumo de um trecho de via e do sentido nominal. Mão única:
    o próprio rumo, se não contrariar o nominal. Mão dupla: a orientação mais perto do nominal."""
    from datakit.common.geo import angle_diff

    target = hint_target(hint)
    if oneway:
        return bearing if angle_diff(bearing, target) <= 90.0 else None
    best = min((bearing, (bearing + 180.0) % 360.0), key=lambda b: angle_diff(b, target))
    return best if angle_diff(best, target) <= HINT_MAX_DEG else None


def direction_on(line: MeasuredLine, km: float, increasing: bool) -> Optional[int]:
    """Rumo (0-359) do trânsito no sentido dado, no ponto km da linha."""
    b = line.bearing_at(km)
    if b is None:
        return None
    return to_dir(b if increasing else b + 180.0)
