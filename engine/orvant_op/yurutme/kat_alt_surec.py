"""KAT derleme/kapı işlerini çağıranın ``__main__`` modülünden yalıtır."""
from __future__ import annotations

import json
import sys


def _calistir(istek):
    islem = istek.get("islem")
    anahtarlar = istek.get("anahtarlar")
    if not isinstance(anahtarlar, dict):
        raise ValueError("KAT alt süreç anahtarları nesne olmalı")
    if islem == "kapi":
        from orvant_op.yurutme.kat_kapi import kapi_karari
        return kapi_karari(**anahtarlar)
    if islem == "proje_derle":
        from orvant_op.yurutme.kat_kapi import proje_kat_derle
        return proje_kat_derle(**anahtarlar)
    if islem == "derle":
        from orvant_op.mimar.kat_derle import derle
        from orvant_op.yurutme.akis import Yurutme
        taslaklar = anahtarlar.pop("referans_taslaklari")
        anahtarlar["referans_gozlemleri"] = {
            anahtar: Yurutme._kat_referans_degeri(islem_adi, beklenen)
            for anahtar, islem_adi, beklenen in taslaklar}
        return derle(**anahtarlar)
    raise ValueError("bilinmeyen KAT alt süreç işlemi")


def main():
    try:
        istek = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        sonuc = _calistir(istek)
        yanit = {"basarili": True, "sonuc": sonuc}
    except BaseException as exc:
        yanit = {"basarili": False, "hata": f"{type(exc).__name__}: {exc}"}
    sys.stdout.buffer.write(json.dumps(yanit, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
