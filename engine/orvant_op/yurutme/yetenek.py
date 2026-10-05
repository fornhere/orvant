"""Görevin mevcut ortam ve erişim bilgisini salt okunur bir kanıtta birleştirir."""


def manifest_uret(*, envanter, okunabilir_girdiler, izinler, kapi_yalitimi):
    """Yeni izin üretmeden görev yeteneklerinin taşınabilir anlık görüntüsünü döndürür."""
    baglam = (envanter or {}).get("gozlem_baglami") or {
        "ortam": "bilinmiyor", "sandbox": None
    }
    onayli = [dict(izin) for izin in izinler
              if izin.get("durum") == "verildi" and izin.get("onay_olay_id")]
    return {
        "surum": 1,
        "salt_okunur": True,
        "gozlem_ortami": dict(baglam),
        "okunabilir_girdiler": list(okunabilir_girdiler),
        "onayli_izinler": onayli,
        "kapi_ortami": {
            "ortam": "sandbox" if kapi_yalitimi else "bilinmiyor",
            "yalitim": kapi_yalitimi,
        },
    }
