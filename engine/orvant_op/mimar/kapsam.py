"""S1 sözleşmesi ile S2 görevleri arasında salt okunur kapsam matrisi.

Plan şemasına görev→gereksinim bağı eklenince denetim RET'e yükseltilebilir.
O zamana kadar eksik bağlar yalnız uyarı ve matris raporudur.
"""
import re
import unicodedata

from orvant_op.yer_tutucu import denetle as yer_tutucu_denetle


_KISISEL = re.compile(
    r"\b(kisisel\s+(?:veri|kullanici|kayit)|kisi\s+ad[ıi]|ad\s+soyad|"
    r"e-?posta|telefon|adres|konum|kimlik\s+(?:no|numara)\w*|biyometrik)\b|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.IGNORECASE)
_KISI = r"\b(?:kisi\w*|kullanici\w*|insan\w*|calisan\w*|hasta\w*)\b"
_KAYIT = r"\b(?:yuz|ses)\s+kayd\w*\b"
_KISI_KAYDI = re.compile(
    rf"{_KISI}.{{0,100}}{_KAYIT}|{_KAYIT}.{{0,100}}{_KISI}", re.IGNORECASE)


def _kisisel_veri(metin):
    metin = _duzlestir(metin).replace("ı", "i")
    return bool(_KISISEL.search(metin) or _KISI_KAYDI.search(metin))


def _duzlestir(metin):
    return "".join(c for c in unicodedata.normalize("NFKD", metin or "")
                   if not unicodedata.combining(c)).lower()


def _yasam_dongusu_karari(karar):
    if (karar.get("kaynak") not in ("kullanici", "onayli_sozlesme") or
            karar.get("sahip") == "varsayilan"):
        return False
    deger = str(karar.get("deger") or "").strip()
    if not deger or re.search(r"belirlenecek|belirlenmedi|ertelen|sonra\s+belirle", _duzlestir(deger)):
        return False
    try:
        yer_tutucu_denetle(deger)
    except ValueError:
        return False
    return _yasam_dongusu_konusu(karar)


def _yasam_dongusu_konusu(karar):
    metin = _duzlestir(" ".join(str(karar.get(k) or "")
                                for k in ("baslik", "deger")))
    return ("saklama" in metin or "saklan" in metin) and ("silme" in metin or "silin" in metin)


def _dayanaklar(kayit):
    return {x for x in (kayit.get("kaynak_id"), kayit.get("dayanak_olay_id")) if x}


def _bilgi_sahipleri(kayit, bagli_kararlar):
    sahipler = {k.get("sahip") for k in bagli_kararlar
                if k.get("sahip") in ("kullanici", "arastirilabilir")}
    if not sahipler:
        sahipler.add("kullanici" if kayit.get("kaynak_turu") == "kullanici"
                     else "arastirilabilir")
    return sorted(sahipler)


def kapsam_matrisi(sozlesme, plan, kararlar=None):
    """Kimlik ve kayıtlı dayanaklar dışında çıkarım yapmadan kapsamı üretir.

    Fonksiyon girdileri değiştirmez. Kişisel veri yaşam döngüsü kararı yoksa kararın
    cevabını değil, yalnız kullanıcıya ait açık karar gereksinimini listeler.
    """
    kararlar = list(kararlar if kararlar is not None else sozlesme.get("kararlar", []))
    gorevler = plan.get("gorevler", [])
    kabul_gorevleri = {}
    gereksinim_gorevleri = {}
    for gorev in gorevler:
        for kimlik in gorev.get("gereksinim_ids", []):
            gereksinim_gorevleri.setdefault(kimlik, []).append(gorev["id"])
        for kabul in gorev.get("kabul", []):
            kimlik = kabul.get("sozlesme_kabul_id")
            if kimlik:
                kabul_gorevleri.setdefault(kimlik, []).append(gorev["id"])

    kapsam_disi = {k["sozlesme_kabul_id"]: k["gerekce"]
                   for k in plan.get("kapsanmayan_kabul", [])
                   if (k.get("gerekce") or "").strip()}
    kabuller = sozlesme.get("kabul_olcutleri", [])
    # Ortak olay birden fazla gereksinim taşıyabilir; kapsam kanıtı değildir.
    dolayli_gorevler = {}
    kaynak_gorevler = {}
    kaynak_gereksinimleri = {}
    for gereksinim in sozlesme.get("gereksinimler", []):
        for kaynak in _dayanaklar(gereksinim):
            kaynak_gereksinimleri.setdefault(kaynak, set()).add(gereksinim["id"])
    for gereksinim in sozlesme.get("gereksinimler", []):
        kimlik = gereksinim["id"]
        if kabul_gorevleri.get(kimlik):
            gereksinim_gorevleri.setdefault(kimlik, []).extend(kabul_gorevleri[kimlik])
        if kimlik in gereksinim_gorevleri:
            continue
        dayanak = _dayanaklar(gereksinim)
        for kabul in kabuller:
            ortak = dayanak.intersection(_dayanaklar(kabul))
            if ortak:
                tekil = any(kaynak_gereksinimleri[k] == {kimlik} for k in ortak)
                baglar = kaynak_gorevler if tekil else dolayli_gorevler
                baglar.setdefault(kimlik, []).extend(kabul_gorevleri.get(kabul["id"], []))
        if kaynak_gorevler.get(kimlik):
            gereksinim_gorevleri[kimlik] = kaynak_gorevler[kimlik]

    satirlar = []
    acik_kararlar = []
    for tur, kayitlar, baglar in (
            ("gereksinim", sozlesme.get("gereksinimler", []), gereksinim_gorevleri),
            ("kabul_olcutu", kabuller, kabul_gorevleri)):
        for kayit in kayitlar:
            dayanak = _dayanaklar(kayit)
            bagli_kararlar = [k for k in kararlar if dayanak and
                              (k.get("kaynak_olay_id") in dayanak or k["id"] in dayanak)]
            kisisel = tur == "gereksinim" and _kisisel_veri(kayit.get("metin", ""))
            yasam_kararlari = [k for k in bagli_kararlar
                               if k.get("durum") == "cozuldu" and _yasam_dongusu_karari(k)] if kisisel else []
            if kisisel and not yasam_kararlari:
                mevcut = next((k for k in bagli_kararlar
                               if k.get("sahip") == "kullanici" and k.get("durum") != "cozuldu"
                               and _yasam_dongusu_konusu(k)), None)
                acik_kararlar.append({
                    "id": mevcut["id"] if mevcut else f"K-veri-yasam-{kayit['id']}",
                    "gereksinim_id": kayit["id"],
                    "baslik": "Kişisel veri saklama ve silme kararı",
                    "sahip": "kullanici", "durum": "acik",
                })
            satirlar.append({
                "gereksinim_id": kayit["id"], "tur": tur,
                "karar_ids": sorted({k["id"] for k in bagli_kararlar}),
                "bilgi_sahibi": _bilgi_sahipleri(kayit, bagli_kararlar),
                "veri_yasam_dongusu": {
                    "kisisel_veri": kisisel,
                    "karar_ids": sorted({k["id"] for k in yasam_kararlari}),
                    "durum": "acik" if kisisel and not yasam_kararlari else "uygulanmaz" if not kisisel else "kayitli",
                },
                "kapsam_disi_gerekce": kapsam_disi.get(kayit["id"])
                    if tur == "kabul_olcutu" else None,
                "plan_gorev_ids": sorted(set(baglar.get(kayit["id"], []))),
                "dolayli_plan_gorev_ids": sorted(set(dolayli_gorevler.get(kayit["id"], [])))
                    if tur == "gereksinim" else [],
                "bag_turu": "kaynak" if tur == "gereksinim" and kaynak_gorevler.get(kayit["id"]) else
                    "dogrudan" if baglar.get(kayit["id"]) else
                    "dolayli" if tur == "gereksinim" and dolayli_gorevler.get(kayit["id"]) else "yok",
            })
    atlanan = [s["gereksinim_id"] for s in satirlar if not s["plan_gorev_ids"]
               and not s["kapsam_disi_gerekce"]]
    kararsiz = [s["gereksinim_id"] for s in satirlar if not s["karar_ids"]]
    cozulmemis = [k["id"] for k in kararlar
                  if k.get("sahip") == "kullanici" and k.get("durum") != "cozuldu"]
    uyarilar = (["planın atladığı sözleşme gereksinimleri: " + ", ".join(atlanan)]
                if atlanan else [])
    return {"uyarilar": uyarilar, "satirlar": satirlar, "atlanmis_gereksinimler": atlanan,
            "kararsiz_gereksinimler": kararsiz,
            "acik_kararlar": acik_kararlar,
            "engeller": sorted(set(cozulmemis + [k["id"] for k in acik_kararlar]))}
