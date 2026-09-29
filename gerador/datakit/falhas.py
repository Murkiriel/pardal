"""Fontes que falharam nesta execução do build.

Uma fonte fora do ar é pulada e o resto do build segue; o que foi pulado fica registrado aqui,
vai para o campo `falhas` do data/dist/catalog.json e o scripts/publicar.py se recusa a montar
os arquivos enquanto a lista não estiver vazia (ou com --aceitar-falhas).
"""
from __future__ import annotations

from typing import List

_FALHAS: List[str] = []


def registrar(fonte: str, erro: BaseException) -> None:
    msg = f"{fonte}: {type(erro).__name__}: {erro}"[:300]
    print(f"[build] {fonte}: FALHOU, pulando ({type(erro).__name__}: {erro})")
    if msg not in _FALHAS:
        _FALHAS.append(msg)


def lista() -> List[str]:
    return list(_FALHAS)
