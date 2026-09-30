"""Fontes que falharam nesta execução do build.

Uma fonte fora do ar é pulada e o resto do build segue; o que foi pulado fica registrado aqui,
vai para o campo `failures` do data/dist/catalogo.json e o scripts/publish.py se recusa a montar
os arquivos enquanto a lista não estiver vazia (ou com --accept-failures).

Avisos são o caso mais brando: a fonte falhou, mas havia uma cópia anterior guardada em data/raw/
e ela foi usada (dado de um mês atrás, não dado faltando). Vão para o campo `warnings`, aparecem no
fim do build e no publish.py, e não bloqueiam a publicação.
"""
from __future__ import annotations

from typing import List

_FAILURES: List[str] = []


def record(source: str, error: BaseException) -> None:
    msg = f"{source}: {type(error).__name__}: {error}"[:300]
    print(f"[build] {source}: FALHOU, pulando ({type(error).__name__}: {error})")
    if msg not in _FAILURES:
        _FAILURES.append(msg)


def recorded() -> List[str]:
    return list(_FAILURES)


_WARNINGS: List[str] = []


def warn(source: str, message: str) -> None:
    msg = f"{source}: {message}"[:300]
    print(f"[build] {source}: AVISO — {message}")
    if msg not in _WARNINGS:
        _WARNINGS.append(msg)


def warnings() -> List[str]:
    return list(_WARNINGS)
