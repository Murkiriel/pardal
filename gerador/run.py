"""Shim para rodar um módulo do datakit com a raiz do repo no sys.path.

    python run.py datakit.build --uf GO

Existe para as distribuições do Python que ignoram PYTHONPATH (as "embeddable" do Windows,
com arquivo ._pth). Num Python comum, `python -m datakit.build ...` a partir desta pasta
funciona igual.
"""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: python run.py <modulo> [args...]")
    module = sys.argv.pop(1)
    runpy.run_module(module, run_name="__main__", alter_sys=True)
