"""Oturum olay günlüğündeki kullanıcı onayını fail-closed okur."""
import json
import re
from pathlib import Path


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def onay_oku(oturum_dizini):
    """Son kullanıcı onayını döndürür; biçimsiz veya onaysız kayıt hata olur."""
    yol = Path(oturum_dizini) / "karsilama" / "olaylar.jsonl"
    try:
        olaylar = [json.loads(satir) for satir in yol.read_text(encoding="utf-8").splitlines() if satir]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("kullanıcı onayı okunamadı") from exc
    onaylar = [olay for olay in olaylar if isinstance(olay, dict) and olay.get("tur") == "kullanici_onayi"]
    if not onaylar:
        raise ValueError("kullanıcı onayı yok")
    olay = onaylar[-1]
    veri = olay.get("veri")
    if (olay.get("aktor") != "kullanici" or not isinstance(veri, dict)
            or veri.get("durum") != "onaylandi"
            or type(veri.get("sozlesme_revizyon")) is not int
            or not isinstance(veri.get("sozlesme_sha256"), str)
            or not _SHA256.fullmatch(veri["sozlesme_sha256"])
            or veri["sozlesme_sha256"] == "sha256:" + "0" * 64):
        raise ValueError("kullanıcı onayı geçersiz")
    return veri
