"""Bekleme ve onarım geçişlerinin salt, tablo tabanlı kuralları."""

from types import MappingProxyType


GECISLER = MappingProxyType({
    "girdi_bekleme": MappingProxyType({
        "bekleme_durumu": "girdi_bekliyor",
        "cozen_kanitlar": ("kullanici_girdisi", "bagimli_cikti"),
        "teshis_eylemi": "girdi_bekle",
        "hak_iadesi": "tukendiyse",
        "idempotent": True,
        "yeniden_dene": True,
        "sonraki_durum": "hazir",
    }),
    "yetki_bekleme": MappingProxyType({
        "bekleme_durumu": "yetki_bekliyor",
        "cozen_kanitlar": ("kullanici_yetkisi",),
        "teshis_eylemi": "yetki_bekle",
        "hak_iadesi": "tukendiyse",
        "idempotent": True,
        "yeniden_dene": True,
        "sonraki_durum": "hazir",
    }),
    "kabul_celiskisi": MappingProxyType({
        "bekleme_durumu": None,
        "cozen_kanitlar": ("kabul_degisikligi",),
        "teshis_eylemi": "yukselt",
        "hak_iadesi": "her_zaman",
        "idempotent": False,
        "yeniden_dene": True,
        "sonraki_durum": "hazir",
    }),
})


def engel_gecisi(engel_turu):
    """Engel türünün değiştirilemez geçiş tanımını döndürür."""
    try:
        return GECISLER[engel_turu]
    except KeyError as exc:
        raise ValueError(f"bilinmeyen engel türü: {engel_turu}") from exc


def teshis_eylemi(engel_turu):
    return engel_gecisi(engel_turu)["teshis_eylemi"]


def gecis_uygula(engel_turu, kanit_turu, kanit_id, *, deneme,
                  kullanilan_deneme, islenmis_kanitlar=()):
    """Çözen kanıta göre yeni durum/hakkı hesaplar; hiçbir veriyi değiştirmez."""
    gecis = engel_gecisi(engel_turu)
    if kanit_turu not in gecis["cozen_kanitlar"]:
        raise ValueError(f"{kanit_turu} kanıtı {engel_turu} engelini çözmez")
    if not kanit_id:
        raise ValueError("çözen kanıt kimliği gerekli")
    islenmis = tuple(islenmis_kanitlar)
    yeni_kanit = kanit_id not in islenmis
    iade = (yeni_kanit or not gecis["idempotent"]) and (
        gecis["hak_iadesi"] == "her_zaman" or kullanilan_deneme >= deneme)
    return {
        "durum": gecis["sonraki_durum"],
        "deneme": deneme + int(iade),
        "ek_deneme": int(iade),
        "yeniden_dene": gecis["yeniden_dene"],
        "islenmis_kanitlar": islenmis + ((kanit_id,) if yeni_kanit else ()),
    }
