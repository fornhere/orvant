"""KAT'ın işçiye açılan, bilgi amaçlı ve güvenli görünümü."""
from __future__ import annotations

import copy

from orvant_op.kabul_kat import dogrula, yorumla


def gorunum(kat, *, kok=None):
    """Doğrulanmış KAT'tan yalnız işçinin ihtiyaç duyduğu alanları çıkarır.

    Dönen nesne bağımsız bir kopyadır. Üretim kanıtı, referans ve negatif
    fikstür gibi üreticiye özel ayrıntılar bu açık alan listesine eklenmez.
    """
    dogrula(kat, kok=kok)
    return {
        "kat_surumu": kat["kat_surumu"],
        "gorev": kat["gorev"],
        "kat_sha256": kat["kat_sha256"],
        "kapsam": copy.deepcopy(kat["kapsam"]),
        "kontroller": copy.deepcopy(kat["kontroller"]),
        "otorite": False,
    }


def dogrulama(kat, gozlemler, *, kok=None):
    """Çekirdek yorumuyla yerel ön doğrulama yapar; kapı kararı üretmez."""
    sonuc = yorumla(kat, gozlemler, kok=kok)
    return {**sonuc, "otorite": False}
