"""Kabul Ara Temsili adım 1 için bağımlılıksız, fail-closed çekirdek.

Bu modül yalnız adım 1 alt kümesidir: ``kat_surumu=1``; tasarımdaki ``dayanak``,
``ciktilar``, nesne biçimli ``gozlem`` ve ``hata_kodu`` henüz bu şemada yoktur.
Tam KAT bu alanlarla ``kat_surumu=2`` olacaktır; v1 açıkça geçici alt kümedir.
V1 ``yok`` işlemi null ile alan yokluğunu kendi başına ayıramaz. Bu nedenle
kapıda yalnız kapı toplayıcısının ``bulunamadi`` kaydı bu işleme kanıt olabilir.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath


ISLEMLER = frozenset((
    "var", "yok", "tip", "esit", "aralik", "regex_tam", "kume_esit",
    "ayrik", "say", "tum", "herhangi",
))
UYGULAYICI = "kat-stdlib-v1"
AZAMI_DERINLIK = 64
AZAMI_DUGUM = 10_000
AZAMI_KANONIK_BOYUT = 1_048_576


class KatHatasi(ValueError):
    """KAT doğrulanamadığında veya güvenle yorumlanamadığında yükselir."""


def _sonlu(veri, yol="$"):
    """JSON ağacını özyineleme kullanmadan boyut ve derinlik sınırlarıyla denetler."""
    yigin = [(veri, yol, 0)]
    dugum = 0
    while yigin:
        deger, konum, derinlik = yigin.pop()
        dugum += 1
        if dugum > AZAMI_DUGUM:
            raise KatHatasi(f"{yol}: azami düğüm sayısı aşıldı")
        if derinlik > AZAMI_DERINLIK:
            raise KatHatasi(f"{konum}: azami derinlik aşıldı")
        if isinstance(deger, float) and not math.isfinite(deger):
            raise KatHatasi(f"{konum}: sonlu olmayan sayı")
        if isinstance(deger, dict):
            for anahtar, alt in deger.items():
                if not isinstance(anahtar, str):
                    raise KatHatasi(f"{konum}: metin olmayan anahtar")
                yigin.append((alt, f"{konum}.{anahtar}", derinlik + 1))
        elif isinstance(deger, list):
            for sira, alt in enumerate(deger):
                yigin.append((alt, f"{konum}[{sira}]", derinlik + 1))


def kanonik(veri):
    """KAT değerini sıralı anahtarlı, boşluksuz UTF-8 baytlarına çevirir."""
    _sonlu(veri)
    try:
        sonuc = json.dumps(veri, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(sonuc) > AZAMI_KANONIK_BOYUT:
            raise KatHatasi("kanonik veri boyut sınırını aşıyor")
        return sonuc
    except KatHatasi:
        raise
    except (TypeError, ValueError) as exc:
        raise KatHatasi(f"kanonik serileştirme başarısız: {exc}") from exc


def kat_hash(veri):
    """kat_sha256 alanının kendisini dışarıda bırakarak içerik hash'i üretir."""
    if not isinstance(veri, dict):
        raise KatHatasi("KAT nesne olmalı")
    icerik = {k: v for k, v in veri.items() if k != "kat_sha256"}
    return "sha256:" + hashlib.sha256(kanonik(icerik)).hexdigest()


def _yol_dogrula(yol, kok):
    if not isinstance(yol, str) or not yol or "\\" in yol:
        raise KatHatasi("KAT yolu boş olamaz ve POSIX olmalı")
    parcalar = yol.split("/")
    if any(parca in ("", ".") for parca in parcalar):
        raise KatHatasi(f"normalize edilmemiş KAT yolu: {yol}")
    saf = PurePosixPath(yol)
    if saf.is_absolute() or ".." in saf.parts or "." in saf.parts:
        raise KatHatasi(f"güvensiz KAT yolu: {yol}")
    if kok is not None:
        temel = Path(kok).resolve()
        aday = (temel / Path(*saf.parts)).resolve(strict=False)
        if not aday.is_relative_to(temel):
            raise KatHatasi(f"sembolik bağ depo dışına çıkıyor: {yol}")


def _tam_anahtarlar(nesne, beklenen, yol):
    if not isinstance(nesne, dict) or set(nesne) != set(beklenen):
        raise KatHatasi(f"{yol}: eksik veya fazla alan")


def _beklenen_dogrula(islem, beklenen):
    if islem in ("var", "yok") and beklenen is not None:
        raise KatHatasi(f"{islem}: beklenen null olmalı")
    if islem == "tip" and beklenen not in ("null", "boolean", "number", "string", "array", "object"):
        raise KatHatasi("tip: bilinmeyen beklenen tip")
    if islem == "aralik":
        if not isinstance(beklenen, dict) or set(beklenen) != {"en_az", "en_fazla"}:
            raise KatHatasi("aralik: iki sınır da bildirilmeli")
        alt, ust = beklenen["en_az"], beklenen["en_fazla"]
        if alt is None and ust is None:
            raise KatHatasi("aralik: en az bir sınır gerekli")
        if any(x is not None and type(x) not in (int, float) for x in (alt, ust)) or (alt is not None and ust is not None and alt > ust):
            raise KatHatasi("aralik: geçersiz sınır")
    if islem == "regex_tam":
        if not isinstance(beklenen, str): raise KatHatasi("regex_tam: desen metin olmalı")
        try: re.compile(beklenen)
        except re.error as exc: raise KatHatasi(f"regex_tam: geçersiz desen: {exc}") from exc
    if islem in ("kume_esit", "ayrik") and not isinstance(beklenen, list):
        raise KatHatasi(f"{islem}: beklenen liste olmalı")
    if islem == "say" and (type(beklenen) is not int or beklenen < 0):
        raise KatHatasi("say: beklenen negatif olmayan tam sayı olmalı")
    if islem in ("tum", "herhangi") and type(beklenen) is not bool:
        raise KatHatasi(f"{islem}: beklenen boolean olmalı")
    if islem in ("tum", "herhangi") and beklenen is False:
        raise KatHatasi(f"{islem}: ters koşul desteklenmiyor")
    if islem == "ayrik" and beklenen == []:
        raise KatHatasi("ayrik: boş beklenen anlamsız")


def dogrula(veri, *, kok=None):
    """KAT v1 şemasını, bütünlüğünü ve yol güvenliğini fail-closed doğrular."""
    _sonlu(veri)
    _tam_anahtarlar(veri, ("kat_surumu", "gorev", "sozlesme_sha256", "sozlesme_revizyon", "kapsam", "kontroller", "kat_sha256"), "$")
    if type(veri["kat_surumu"]) is not int or veri["kat_surumu"] != 1:
        raise KatHatasi("bilinmeyen KAT sürümü")
    if not isinstance(veri["gorev"], str) or not veri["gorev"]:
        raise KatHatasi("gorev dolu metin olmalı")
    if (not isinstance(veri["sozlesme_sha256"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", veri["sozlesme_sha256"]) is None
            or veri["sozlesme_sha256"] == "sha256:" + "0" * 64
            or type(veri["sozlesme_revizyon"]) is not int or veri["sozlesme_revizyon"] < 1):
        raise KatHatasi("sözleşme dayanağı geçersiz")
    kapsam = veri["kapsam"]
    _tam_anahtarlar(kapsam, ("yazilabilir",), "$.kapsam")
    if not isinstance(kapsam["yazilabilir"], list):
        raise KatHatasi("yazilabilir liste olmalı")
    for sira, oge in enumerate(kapsam["yazilabilir"]):
        _tam_anahtarlar(oge, ("yol", "anlam"), f"$.kapsam.yazilabilir[{sira}]")
        _yol_dogrula(oge["yol"], kok)
        if oge["anlam"] not in ("dosya", "dizin_ve_alti"):
            raise KatHatasi("bilinmeyen yol anlamı")
    if not isinstance(veri["kontroller"], list) or not veri["kontroller"]:
        raise KatHatasi("kontroller dolu liste olmalı")
    kimlikler = set()
    for sira, kontrol in enumerate(veri["kontroller"]):
        _tam_anahtarlar(kontrol, ("id", "dayanak_ids", "alinti", "gozlem", "islem", "beklenen", "uygulayici"), f"$.kontroller[{sira}]")
        if not all(isinstance(kontrol[k], str) and kontrol[k] for k in ("id", "gozlem", "islem", "uygulayici")):
            raise KatHatasi("kontrol metin alanları dolu olmalı")
        if kontrol["gozlem"].startswith("#"):
            raise KatHatasi("JSON Pointer URI biçimi desteklenmiyor")
        if kontrol["id"] in kimlikler:
            raise KatHatasi("kontrol kimliği benzersiz olmalı")
        kimlikler.add(kontrol["id"])
        if kontrol["islem"] not in ISLEMLER or kontrol["uygulayici"] != UYGULAYICI:
            raise KatHatasi("bilinmeyen işlem veya uygulayıcı")
        if (not isinstance(kontrol["dayanak_ids"], list) or not kontrol["dayanak_ids"]
                or not all(isinstance(x, str) and x for x in kontrol["dayanak_ids"])
                or len(kontrol["dayanak_ids"]) != len(set(kontrol["dayanak_ids"]))
                or not isinstance(kontrol["alinti"], str) or not kontrol["alinti"]):
            raise KatHatasi("kontrol dayanağı ve alıntısı geçersiz")
        _beklenen_dogrula(kontrol["islem"], kontrol["beklenen"])
    if not isinstance(veri["kat_sha256"], str) or veri["kat_sha256"] != kat_hash(veri):
        raise KatHatasi("KAT hash uyuşmazlığı")
    return veri


def _liste(deger, ad):
    if not isinstance(deger, list):
        raise KatHatasi(f"{ad}: liste gerekli")
    return deger


def _tipli_anahtar(deger):
    """JSON değerini Python'ın bool/sayı eşitlemesini önleyen anahtara çevirir."""
    _sonlu(deger)

    def donustur(oge):
        if oge is None:
            return ("null",)
        if type(oge) is bool:
            return ("boolean", oge)
        if type(oge) is int:
            return ("integer", oge)
        if type(oge) is float:
            return ("float", oge)
        if isinstance(oge, str):
            return ("string", oge)
        if isinstance(oge, list):
            return ("array", tuple(donustur(x) for x in oge))
        if isinstance(oge, dict):
            return ("object", tuple(
                (anahtar, donustur(oge[anahtar])) for anahtar in sorted(oge)
            ))
        raise KatHatasi("JSON dışı değer")

    return donustur(deger)


def _tipli_esit(sol, sag):
    return _tipli_anahtar(sol) == _tipli_anahtar(sag)


def _islet(islem, gozlem, beklenen):
    if islem == "var": return gozlem is not None
    if islem == "yok": return gozlem is None
    if islem == "tip":
        tipler = {"null": lambda x: x is None, "boolean": lambda x: type(x) is bool,
                  "number": lambda x: type(x) in (int, float), "string": lambda x: isinstance(x, str),
                  "array": lambda x: isinstance(x, list), "object": lambda x: isinstance(x, dict)}
        if beklenen not in tipler: raise KatHatasi("tip: bilinmeyen beklenen tip")
        return tipler[beklenen](gozlem)
    if islem == "esit": return _tipli_esit(gozlem, beklenen)
    if islem == "aralik":
        if not isinstance(beklenen, dict) or set(beklenen) != {"en_az", "en_fazla"} or type(gozlem) not in (int, float):
            raise KatHatasi("aralik: geçersiz girdi")
        alt, ust = beklenen["en_az"], beklenen["en_fazla"]
        if any(x is not None and type(x) not in (int, float) for x in (alt, ust)) or (alt is not None and ust is not None and alt > ust):
            raise KatHatasi("aralik: geçersiz sınır")
        return (alt is None or gozlem >= alt) and (ust is None or gozlem <= ust)
    if islem == "regex_tam":
        if not isinstance(beklenen, str) or not isinstance(gozlem, str): raise KatHatasi("regex_tam: metin gerekli")
        try: return re.fullmatch(beklenen, gozlem) is not None
        except re.error as exc: raise KatHatasi(f"regex_tam: geçersiz desen: {exc}") from exc
    if islem in ("kume_esit", "ayrik"):
        sol, sag = _liste(gozlem, islem), _liste(beklenen, islem)
        sol_anahtar = {_tipli_anahtar(x) for x in sol}
        sag_anahtar = {_tipli_anahtar(x) for x in sag}
        return sol_anahtar == sag_anahtar if islem == "kume_esit" else sol_anahtar.isdisjoint(sag_anahtar)
    if islem == "say":
        if type(beklenen) is not int or beklenen < 0 or not isinstance(gozlem, (list, dict, str)):
            raise KatHatasi("say: geçersiz girdi")
        return len(gozlem) == beklenen
    if islem in ("tum", "herhangi"):
        liste = _liste(gozlem, islem)
        if type(beklenen) is not bool or any(type(x) is not bool for x in liste): raise KatHatasi(f"{islem}: boolean liste gerekli")
        if islem == "tum" and not liste:
            raise KatHatasi("tum: boş gözlem listesi belirsiz")
        return (all(liste) if islem == "tum" else any(liste)) is beklenen
    raise KatHatasi("bilinmeyen işlem")


def yorumla(kat, gozlemler, *, kok=None):
    """Gözlem → işlem → sonuç zincirini yan etkisiz ve deterministik yorumlar."""
    dogrula(kat, kok=kok)
    if not isinstance(gozlemler, dict): raise KatHatasi("gözlemler nesne olmalı")
    sonuclar = []
    for kontrol in kat["kontroller"]:
        deger = gozlemler.get(kontrol["gozlem"])
        gecti = _islet(kontrol["islem"], deger, kontrol["beklenen"])
        oz = hashlib.sha256(kanonik({"kat_sha256": kat["kat_sha256"], "kontrol": kontrol["id"], "gecti": gecti, "gozlem": deger})).hexdigest()
        sonuclar.append({"id": "sha256:" + oz, "sha256": oz, "kontrol_id": kontrol["id"], "gecti": gecti})
    return {"gecti": all(x["gecti"] for x in sonuclar), "sonuclar": sonuclar}
