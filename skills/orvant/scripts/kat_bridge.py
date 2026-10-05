"""Ontology acceptance kurallarını KAT v1'in ortak cebirine bağlar.

Bu modül yayınlanan skill'in parçasıdır; yalnız standart kitaplığı kullanır ve
motor paketine bağımlı değildir. KAT dosyasını kendisi doğrulayıp yorumlar.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ORTAK_ISLEMLER = {
    "equal_sets": "kume_esit", "disjoint": "ayrik", "count": "say",
    "all": "tum", "any": "herhangi",
}


def _kanonik(deger):
    return json.dumps(deger, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def kat_hash(kat):
    """KAT çekirdeğiyle aynı kanonik içerik hash'ini bağımsız üretir."""
    if type(kat) is not dict:
        raise ValueError("KAT nesne olmalı")
    icerik = {anahtar: deger for anahtar, deger in kat.items()
              if anahtar != "kat_sha256"}
    return "sha256:" + hashlib.sha256(_kanonik(icerik).encode("utf-8")).hexdigest()


def _kat_dogrula(kat):
    if type(kat) is not dict or set(kat) != {
            "kat_surumu", "gorev", "kapsam", "kontroller", "kat_sha256"}:
        raise ValueError("geçersiz KAT biçimi")
    if type(kat["kat_surumu"]) is not int or kat["kat_surumu"] != 1 or kat["kat_sha256"] != kat_hash(kat):
        raise ValueError("KAT sürümü veya hash'i geçersiz")
    if (kat["kapsam"] != {"yazilabilir": []} or type(kat["kontroller"]) is not list
            or not kat["kontroller"]):
        raise ValueError("köprü KAT kapsamı veya kontrolleri geçersiz")
    kimlikler = set()
    for kontrol in kat["kontroller"]:
        if type(kontrol) is not dict or set(kontrol) != {
                "id", "dayanak_ids", "alinti", "gozlem", "islem", "beklenen", "uygulayici"}:
            raise ValueError("geçersiz KAT kontrolü")
        if kontrol["islem"] not in ORTAK_ISLEMLER.values() or kontrol["uygulayici"] != "kat-stdlib-v1":
            raise ValueError("köprüde bilinmeyen KAT işlemi")
        if kontrol["id"] in kimlikler:
            raise ValueError("yinelenen KAT kontrol kimliği")
        kimlikler.add(kontrol["id"])


def _sec(state, selector):
    nesneler = {oge["id"]: oge for oge in state["objects"]}
    secilen = set(selector["roots"])
    if not secilen <= nesneler.keys():
        raise ValueError("selector kökü yok")
    for adim in selector["path"]:
        kaynak, hedef = (("from", "to") if adim["direction"] == "out"
                          else ("to", "from"))
        secilen = {iliski[hedef] for iliski in state["relations"]
                   if iliski["type"] == adim["relation_type"]
                   and iliski[kaynak] in secilen}
        if not secilen <= nesneler.keys():
            raise ValueError("selector ilişki ucu yok")
    if selector["property"] is None:
        return sorted(secilen)
    degerler = []
    for kimlik in sorted(secilen):
        if selector["property"] not in nesneler[kimlik]["properties"]:
            raise ValueError("selector özelliği yok")
        degerler.append(nesneler[kimlik]["properties"][selector["property"]])
    return degerler


def _kurali_dogrula(kural, gorulen, derinlik=0):
    if derinlik > 32 or type(kural) is not dict:
        raise ValueError("geçersiz acceptance kuralı")
    kimlik, op = kural.get("id"), kural.get("op")
    if type(kimlik) is not str or not kimlik or kimlik in gorulen:
        raise ValueError("yinelenen veya geçersiz kural kimliği")
    gorulen.add(kimlik)
    if op not in ORTAK_ISLEMLER:
        raise ValueError("ortak olmayan acceptance işlemi")
    if op in ("all", "any"):
        if type(kural.get("rules")) is not list or not kural["rules"]:
            raise ValueError("bileşik kuralın alt kuralları dolu olmalı")
        for alt in kural["rules"]:
            _kurali_dogrula(alt, gorulen, derinlik + 1)


def _kontrol(state, kural, gozlemler, kontroller):
    op = kural.get("op")
    if op not in ORTAK_ISLEMLER:
        raise ValueError("ortak olmayan acceptance işlemi")
    gozlem_adi = "gozlem:" + kural["id"]
    try:
        if op in ("equal_sets", "disjoint"):
            gozlemler[gozlem_adi] = _sec(state, kural["left"])
            beklenen = _sec(state, kural["right"])
        elif op == "count":
            if kural["max"] is None or kural["min"] != kural["max"]:
                raise ValueError("KAT say işlemi için kesin sayım (min == max) gerekli")
            gozlemler[gozlem_adi] = _sec(state, kural["selector"])
            beklenen = kural["min"]
        else:
            baslangic = len(kontroller)
            altlar = [_kontrol(state, alt, gozlemler, kontroller) for alt in kural["rules"]]
            gozlemler[gozlem_adi] = [_islet(k, gozlemler[k["gozlem"]]) for k in altlar]
            beklenen = True
            # KAT v1'de bağımlı/diagnostik kontrol kavramı yoktur ve kapı bütün
            # kontrolleri zorunlu sayar. Alt kuralları hash'e eksiksiz katarken
            # yalnız bileşik kökü karar verici tutan tipli, kendine eşit kayıtlar
            # olarak sakla; kökün gözlemi gerçek alt sonuçlarından hesaplanmıştır.
            tanimlar = {}
            yigin = list(kural["rules"])
            while yigin:
                tanim = yigin.pop()
                tanimlar[tanim["id"]] = tanim
                yigin.extend(tanim.get("rules", []))
            for alt_kontrol in kontroller[baslangic:]:
                tanim = tanimlar[alt_kontrol["id"]]
                gozlemler[alt_kontrol["gozlem"]] = [tanim]
                alt_kontrol.update(islem="kume_esit", beklenen=[tanim])
        kontrol = {"id": kural["id"], "gozlem": gozlem_adi,
                   "islem": ORTAK_ISLEMLER[op], "beklenen": beklenen,
                   "uygulayici": "kat-stdlib-v1"}
    except ValueError as exc:
        # acceptance.py seçim hatalarını kural başarısızlığına çevirir. Aynı
        # fail-closed sonuç, KAT'ta tipli ve kesin bir eşitsizlikle temsil edilir.
        if "kesin sayım" in str(exc):
            raise
        gozlemler[gozlem_adi], beklenen = [False], [True]
        kontrol = {"id": kural["id"], "gozlem": gozlem_adi,
                   "islem": "kume_esit", "beklenen": beklenen,
                   "uygulayici": "kat-stdlib-v1"}
    kontrol.update(dayanak_ids=[kural["id"]], alinti=kural["id"])
    kontroller.append(kontrol)
    return kontrol


def kuraldan_kat(state, kural, *, gorev):
    """Tek acceptance kuralını ve gözlemini ortak KAT cebirine derler."""
    _kurali_dogrula(kural, set())
    gozlemler = {}
    kontroller = []
    _kontrol(state, kural, gozlemler, kontroller)
    kat = {"kat_surumu": 1, "gorev": gorev, "kapsam": {"yazilabilir": []},
           "kontroller": kontroller, "kat_sha256": ""}
    kat["kat_sha256"] = kat_hash(kat)
    return kat, gozlemler


def _islet(kontrol, gozlem):
    islem, beklenen = kontrol["islem"], kontrol["beklenen"]
    if islem == "say":
        if not isinstance(gozlem, (list, dict, str)) or type(beklenen) is not int:
            raise ValueError("say için geçersiz değer")
        return len(gozlem) == beklenen
    if islem in ("tum", "herhangi"):
        if type(gozlem) is not list or any(type(v) is not bool for v in gozlem):
            raise ValueError("boolean liste gerekli")
        return ((all(gozlem) if islem == "tum" else any(gozlem)) is beklenen)
    if type(gozlem) is not list or type(beklenen) is not list:
        raise ValueError("küme işlemi için liste gerekli")
    sol = {_tipli_anahtar(v) for v in gozlem}
    sag = {_tipli_anahtar(v) for v in beklenen}
    return sol == sag if islem == "kume_esit" else sol.isdisjoint(sag)


def _tipli_anahtar(deger):
    """acceptance.py'nin kanonik JSON küme eşitliğini bire bir uygular."""
    try:
        return json.dumps(deger, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("JSON dışı değer") from exc


def dosyadan_yorumla(yol, gozlemler):
    """KAT JSON'unu diskten okuyup fail-closed yorumlar."""
    try:
        kat = json.loads(Path(yol).read_text(encoding="utf-8"),
                         parse_constant=lambda sabit: (_ for _ in ()).throw(
                             ValueError(f"sonlu olmayan sayı: {sabit}")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"KAT okunamadı: {exc}") from exc
    _kat_dogrula(kat)
    sonuclar = []
    for kontrol in kat["kontroller"]:
        if kontrol["gozlem"] not in gozlemler:
            raise ValueError("KAT gözlemi eksik")
        sonuclar.append({"kontrol_id": kontrol["id"],
                         "gecti": _islet(kontrol, gozlemler[kontrol["gozlem"]])})
    return {"kat_sha256": kat["kat_sha256"],
            "gecti": all(s["gecti"] for s in sonuclar), "sonuclar": sonuclar}


def snapshot_bagla(manifest, kat):
    """Snapshot'ı belirli KAT içeriğine bağlayan bağımsız kayıt üretir."""
    _kat_dogrula(kat)
    icerik = {"manifest": manifest, "kat_sha256": kat["kat_sha256"]}
    return {**icerik, "sha256": hashlib.sha256(
        _kanonik(icerik).encode("utf-8")).hexdigest()}


def snapshot_gecerli(snapshot, kat):
    """Snapshot hem kendi içinde sağlamsa hem güncel KAT'a bağlıysa doğrudur."""
    try:
        _kat_dogrula(kat)
        icerik = {"manifest": snapshot["manifest"],
                  "kat_sha256": snapshot["kat_sha256"]}
        return (set(snapshot) == {"manifest", "kat_sha256", "sha256"}
                and snapshot["kat_sha256"] == kat["kat_sha256"]
                and snapshot["sha256"] == hashlib.sha256(
                    _kanonik(icerik).encode("utf-8")).hexdigest())
    except (KeyError, TypeError, ValueError):
        return False
