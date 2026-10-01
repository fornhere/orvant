"""Son Codex oturumlarından salt okunur kota gözlemi."""

import os
import re
from pathlib import Path


def kota_oku(oturum_dizini=None):
    codex_evi = os.environ.get("CODEX_HOME")
    varsayilan = ((Path(codex_evi).expanduser() if codex_evi else Path.home() / ".codex")
                  / "sessions")
    kok = Path(os.environ.get("ORVANT_CODEX_OTURUMLARI") or oturum_dizini
               or varsayilan).expanduser()
    try:
        yollar = sorted(kok.rglob("rollout-*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    except OSError:
        yollar = []
    for yol in yollar:
        son = None
        try:
            with yol.open(encoding="utf-8", errors="replace") as dosya:
                for satir in dosya:
                    for deger in re.findall(r'"used_percent"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', satir):
                        son = float(deger)
        except OSError:
            continue
        if son is not None:
            return {"used_percent": son, "dosya": str(yol)}
    return {"used_percent": None, "dosya": None}
