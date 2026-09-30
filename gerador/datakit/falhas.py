"""Fontes que falharam nesta execução do build.

Uma fonte fora do ar é pulada e o resto do build segue; o que foi pulado fica registrado aqui,
vai para o campo `falhas` do data/dist/catalog.json e o scripts/publicar.py se recusa a montar
os arquivos enquanto a lista não estiver vazia (ou com --aceitar-falhas).

Avisos são o caso mais brando: a fonte falhou, mas havia uma cópia anterior guardada em data/raw/
e ela foi usada (dado de um mês atrás, não dado faltando). Vão para o campo `avisos`, aparecem no
fim do build e no publicar.py, e não bloqueiam a publicação.
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


_AVISOS: List[str] = []


def avisar(fonte: str, mensagem: str) -> None:
    msg = f"{fonte}: {mensagem}"[:300]
    print(f"[build] {fonte}: AVISO — {mensagem}")
    if msg not in _AVISOS:
        _AVISOS.append(msg)


def avisos() -> List[str]:
    return list(_AVISOS)
