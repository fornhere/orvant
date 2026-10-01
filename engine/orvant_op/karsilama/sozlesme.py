"""Sözleşme inşası, kaynak ve onay kapısı, v0.3 spec dönüşümü."""
import re
import unicodedata


_OZNEL_YOK = re.compile(r"(?:herhangi bir |yeni )*öznel kalite ölçütü eklenm(?:ez|iyor)\s*\.?\s*$", re.I)
_JSON_KEHANET = re.compile(r"\b([\w.-]+\.json)\s+([A-Za-z_][A-Za-z_0-9]*)\b", re.I)
_JSON_DOSYASI = re.compile(r"\b[\w.-]+\.json\b", re.I)
_OZNEL_ISARET = re.compile(r"\b(?:öznel\w*|esteti\w*|beğeni\w*|görsel kalite\w*|kullanılabilirli\w*|anlaşılabilirli\w*|insan inceleme\w*)\b", re.I)
_OZNEL_DISLAMA = re.compile(
    r"(?:\s+(?:tasarım\w*|kalite\w*|ölçüt\w*|kriter\w*|inceleme\w*|"
    r"değerlendirme\w*|için|kabul|eşiği|beklenti\w*|gereksinim\w*|şart\w*)){0,5}"
    r"\s+(?:yok(?:tur)?|eklenm(?:ez|iyor)|istenmiyor|gerekmiyor|aranmıyor|"
    r"kapsam dışı|dahil değil)\b",
    re.I,
)
_OLCULEBILIR = re.compile(
    r"(?:eşit|eşleş|karşılaştır|\b\d+(?:[.,]\d+)?\s+(?:olmal|olacak))",
    re.I,
)
_ANAHTAR_DISI = {"kabul", "olcut", "icin", "olan", "olarak", "yalniz", "ayni", "alan", "deger", "sonuc", "uretil", "kontrol", "kaynak", "kullanici"}


def _anahtarlar(metin):
    """Türkçe ekleri tolere ederek pasaj, karar ve kabulün konu ortaklığını bul."""
    sade = unicodedata.normalize("NFKD", metin.casefold().replace("ı", "i"))
    sade = "".join(c for c in sade if not unicodedata.combining(c))
    return {kelime[:5] if len(kelime) > 5 else kelime
            for kelime in re.findall(r"[a-z0-9]+", sade)
            if len(kelime) >= 3 and kelime not in _ANAHTAR_DISI}


def _oznel_istek(metin):
    # "Öznel ölçüt yoktur" kaynak kapsamını daraltır; olumlu bir öznel
    # istek ise deterministik kabulün insan incelemesini kaldırmasına engeldir.
    for parca in re.split(r"[,;.!?\n]+|\b(?:ve|veya|fakat|ama|ancak)\b", metin, flags=re.I):
        for isaret in _OZNEL_ISARET.finditer(parca):
            if not _OZNEL_DISLAMA.match(parca, isaret.end()):
                return True
    return False


def _kaynakli_olculebilir_kalite(k, olaylar, kabuller):
    olay = olaylar.get(k.get("kaynak_olay_id"))
    if (not olay or olay.get("tur") not in ("kullanici_hedefi", "kullanici_cevabi")
            or olay.get("aktor") != "kullanici" or _oznel_istek(olay.get("metin") or "")
            or _oznel_istek(k.get("deger") or "")):
        return False
    deger = _anahtarlar(k["deger"])
    for kabul in kabuller:
        if (kabul.get("tur") != "deterministik" or kabul.get("dayanak_olay_id") != olay["id"]):
            continue
        pasaj = kabul.get("dayanak_alinti") or ""
        kaynak = olay.get("metin") or ""
        metin, kehanet = kabul.get("metin") or "", kabul.get("kehanet") or ""
        if (not pasaj.strip() or pasaj not in kaynak or _oznel_istek(pasaj)
                or _oznel_istek(metin) or _oznel_istek(kehanet)
                or not _OLCULEBILIR.search(pasaj)
                or not _OLCULEBILIR.search(metin + " " + kehanet)):
            continue
        konu = _anahtarlar(pasaj)
        if (len(konu & deger) >= 2 and len(konu & _anahtarlar(metin)) >= 2
                and konu & _anahtarlar(kehanet)):
            return True
    return False


def _deterministik_kalite(k, olaylar, kabuller):
    """Yalnız kaynaklı, aynı kehanete bağlı ve açıkça öznel olmayan kalite referansı."""
    if any(_oznel_istek(e.get("metin") or "") for e in olaylar.values()
           if e.get("aktor") == "kullanici" and e.get("tur") in ("kullanici_hedefi", "kullanici_cevabi")):
        return False
    if _kaynakli_olculebilir_kalite(k, olaylar, kabuller):
        return True
    olay = olaylar.get(k.get("kaynak_olay_id"))
    if (not olay or olay.get("tur") != "kullanici_cevabi" or olay.get("aktor") != "kullanici"
            or k.get("kategori") != "kalite_nitelikleri"):
        return False
    deger, cevap = k.get("deger") or "", olay.get("metin") or ""
    if not (_OZNEL_YOK.search(deger) and _OZNEL_YOK.search(cevap)):
        return False
    if any(_OZNEL_ISARET.search(_OZNEL_YOK.sub("", metin)) for metin in (deger, cevap)):
        return False
    dosyalar = [{x.casefold() for x in _JSON_DOSYASI.findall(metin)} for metin in (deger, cevap)]
    if len(dosyalar[0]) != 1 or dosyalar[0] != dosyalar[1]:
        return False
    # Kehanet alanında açıkça adlandırılmış tek JSON alanı, hem kullanıcı
    # cevabında hem karar değerinde bulunmalı. Başka bir deterministik kabul
    # ölçütü kalite kararını kendiliğinden karşılamaz.
    baglar = {(dosya.casefold(), alan.casefold()) for kabul in kabuller
              if kabul.get("tur") == "deterministik"
              for dosya, alan in _JSON_KEHANET.findall(kabul.get("kehanet") or "")
              if dosya.casefold() in deger.casefold() and dosya.casefold() in cevap.casefold()
              and re.search(rf"\b{re.escape(alan)}\b", deger, re.I)
              and re.search(rf"\b{re.escape(alan)}\b", cevap, re.I)}
    return len(baglar) == 1


def kur(oturum, kararlar, islenmis, iddialar, *, eksik_boyutlar=()):
    gereksinimler = islenmis.get("gereksinimler", [])
    kabuller = islenmis.get("kabul_olcutleri", [])
    izinler = islenmis.get("izinler", [])
    return {
        "revizyon": oturum["revizyon"] + 1,
        "hedef": oturum["hedef"],
        "iddia_ids": [i["id"] for i in iddialar],
        "kararlar": kararlar,
        "open_questions": [f"Karşılama kontrol boyutu {n} eksik: {ad}" for n, ad in
                           ((1, "Başarı ölçüsü ve bugünkü yöntem/süre"), (2, "Kalite referansı"),
                            (3, "Negatif ölçütler"), (4, "İlk teslim biçimi"), (5, "Kullanıcı kontrol noktası"),
                            (6, "Yetki kapsamı"), (7, "Bütçe ve durma kuralı"), (8, "Çıktı/kurulum konumu"),
                            (9, "Mevcut araç ve yetenekler")) if n in eksik_boyutlar],
        "gereksinimler": gereksinimler,
        "kabul_olcutleri": [{**k, "kanit_durumu": "calistirilmadi"} for k in kabuller],
        "yetki": {"varsayilan": "ret", "izinler": izinler},
        "onay": None,
    }


def kapi(sozlesme, olaylar):
    """Döner: (izin verilen en yüksek onay durumu, hata listesi)."""
    hatalar = []
    ids = {e["id"]: e for e in olaylar}
    claims = set(sozlesme["iddia_ids"])
    shown = {k["id"] for k in sozlesme["kararlar"] if k.get("onerilen_varsayim") and k.get("soru")}
    ertelenen = False
    for alan in ("gereksinimler", "kabul_olcutleri"):
        if not sozlesme[alan]:
            hatalar.append(f"boş sözleşme listesi: {alan}")
    tum_kimlikler = [x.get("id") for x in sozlesme["gereksinimler"] +
                    sozlesme["kabul_olcutleri"] + sozlesme["yetki"]["izinler"]]
    if len(tum_kimlikler) != len(set(tum_kimlikler)):
        hatalar.append("sözleşmede yinelenen kimlik")
    for alan, kayitlar in (("gereksinim", sozlesme["gereksinimler"]),
                           ("kabul", sozlesme["kabul_olcutleri"]),
                           ("izin", sozlesme["yetki"]["izinler"])):
        kimlikler = [x.get("id") for x in kayitlar]
        if not all(kimlikler) or len(kimlikler) != len(set(kimlikler)):
            hatalar.append(f"{alan} kimliği eksik veya yinelenmiş")
    if sozlesme.get("open_questions"):
        ertelenen = True
    for k in sozlesme["kararlar"]:
        if k["etki"] == "yuksek":
            if k["durum"] == "acik":
                hatalar.append(f"açık yüksek etkili karar: {k['id']}")
            elif k["durum"] == "ertelendi":
                ertelenen = True
                if not k.get("erteleme_gerekcesi"):
                    hatalar.append(f"ertelenen karar gerekçesiz: {k['id']}")
    for g in sozlesme["gereksinimler"]:
        tur, ref = g.get("kaynak_turu"), g.get("kaynak_id")
        if tur == "kullanici":
            if (ref not in ids or ids[ref]["tur"] not in ("kullanici_cevabi", "kullanici_hedefi")
                    or ids[ref].get("aktor") != "kullanici"):
                hatalar.append(f"gereksinim kaynağı geçersiz: {g.get('id')}")
        elif tur == "iddia":
            if ref not in claims:
                hatalar.append(f"gereksinim iddiası geçersiz: {g.get('id')}")
        elif tur == "onerilen_varsayim":
            if ref not in shown:
                hatalar.append(f"gösterilmemiş varsayım: {g.get('id')}")
        else:
            hatalar.append(f"kaynaksız gereksinim: {g.get('id')}")
    for k in sozlesme["kabul_olcutleri"]:
        if k.get("kanit_durumu") != "calistirilmadi":
            hatalar.append(f"S1 kabul kanıtı çalıştırılmış görünüyor: {k.get('id')}")
        if k.get("tur") == "deterministik" and not k.get("kehanet"):
            hatalar.append(f"kehanetsiz kabul: {k.get('id')}")
        elif k.get("tur") == "insan_incelemesi" and (not k.get("rubrik") or not k.get("inceleyen")):
            hatalar.append(f"rubriksiz insan incelemesi: {k.get('id')}")
        elif k.get("tur") not in ("deterministik", "insan_incelemesi"):
            hatalar.append(f"bilinmeyen kabul türü: {k.get('id')}")
    kalite = [k for k in sozlesme["kararlar"] if 2 in k.get("kontrol_boyutlari", [])
              and k["sahip"] == "kullanici" and k["durum"] == "cozuldu" and k.get("deger")]
    if (kalite and not any(k.get("tur") == "insan_incelemesi" for k in sozlesme["kabul_olcutleri"])
            and not all(_deterministik_kalite(k, ids, sozlesme["kabul_olcutleri"]) for k in kalite)):
        hatalar.append("kullanıcı kalite referansı için insan incelemesi ölçütü eksik")
    for izin in sozlesme["yetki"]["izinler"]:
        ref = izin.get("onay_olay_id")
        if not ref or ref not in ids or ids[ref]["tur"] != "kullanici_cevabi":
            hatalar.append(f"onay olaysız izin: {izin.get('eylem')}")
        if not izin.get("eylem") or not izin.get("kapsam"):
            hatalar.append("kapsamsız izin")
        if izin.get("eylem") == "veri_aktarimi" and not izin.get("hedef"):
            hatalar.append("hedefsiz veri aktarımı izni")
    return ("fizibilite_onayli" if ertelenen else "onaylandi"), hatalar


def v03_spec(sozlesme):
    if not sozlesme.get("onay") or sozlesme["onay"]["revizyon"] != sozlesme["revizyon"] or sozlesme["onay"]["durum"] != "onaylandi":
        raise ValueError("tam revizyon onayı olmadan v0.3 spec üretilmez")
    goal = sozlesme["hedef"]
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower().encode("ascii", "ignore").decode()).strip("-")[:40]
    if not slug:
        slug = "yeni-proje"
    kararlar = sozlesme["kararlar"]
    scope = [k["deger"] for k in kararlar if k["kategori"] == "islevsel_kapsam" and k["durum"] == "cozuldu" and k.get("deger")]
    constraints = [k["deger"] for k in kararlar if k["kategori"] == "kisitlar" and k["durum"] == "cozuldu" and k.get("deger")]
    constraints.append("Yetki: varsayılan ret")
    constraints.extend(f"İzin: {i['eylem']} — {i['kapsam']} (onay: {i['onay_olay_id']})"
                       for i in sozlesme["yetki"]["izinler"])
    constraints.extend(f"Kabul: {k['id']} — {k['tur']} — {k['metin']}"
                       for k in sozlesme["kabul_olcutleri"])
    audience = next((k["deger"] for k in kararlar
                     if k["durum"] == "cozuldu" and k.get("deger") and
                     any(ad in k["baslik"].casefold() for ad in ("hedef kitle", "kullanıcı kitlesi", "audience"))),
                    "Belirlenmedi")
    questions = [k["soru"]["metin"] for k in kararlar if k["durum"] == "ertelendi"]
    questions.extend(sozlesme.get("open_questions", []))
    return {"schema_version": 3, "revision": 0,
            "project": {"id": slug, "name": goal, "goal": goal, "audience": audience,
                        "scope": scope, "out_of_scope": [], "constraints": constraints,
                        "open_questions": questions},
            "ontology": {"object_types": [], "relation_types": []},
            "objects": [], "relations": [], "tasks": [], "decisions": [], "history": []}
