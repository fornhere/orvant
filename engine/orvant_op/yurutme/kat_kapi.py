"""KAT yorumlayıcısını yürütme kapısının fail-closed kararına uyarlar."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from orvant_op.kabul_kat import KatHatasi, dogrula, yorumla


YORUMLAYICI_KIMLIGI = "orvant-kat"
YORUMLAYICI_ANA_SURUMU = 1


def oturum_dosyalari_dogrula(oturum_dizini, isci_yazilabilir_kokler):
    """Güven dayanaklarını tek açılıştan, denetlenen aynı fd üzerinden okur."""
    if (not isinstance(isci_yazilabilir_kokler, (list, tuple))
            or not isci_yazilabilir_kokler):
        raise ValueError("işçi yazılabilir kökler geçersiz")
    kokler = []
    for ham in isci_yazilabilir_kokler:
        if not isinstance(ham, (str, os.PathLike)) or not os.fspath(ham):
            raise ValueError("işçi yazılabilir kökler geçersiz")
        yol = Path(ham)
        if not yol.is_absolute():
            raise ValueError("işçi yazılabilir kökler mutlak olmalı")
        kokler.append(yol.resolve(strict=True))

    oturum_ham = Path(oturum_dizini)
    okunacaklar = (
        oturum_ham / "karsilama" / "olaylar.jsonl",
        oturum_ham / "karsilama" / "sozlesme.json",
    )
    icerikler = {}
    for ham in okunacaklar:
        # resolve() bağları görünmez kılmadan önce sözcüksel zincirin tamamını denetle.
        parcalar = ham.absolute().parts
        parca = Path(parcalar[0])
        for ad in parcalar[1:]:
            parca /= ad
            if os.path.islink(parca):
                raise ValueError("oturum dosyası işçi yazma alanında")
        gercek = ham.resolve(strict=True)
        for bilesen in (gercek, *gercek.parents):
            if any(bilesen == kok or bilesen.is_relative_to(kok) for kok in kokler):
                raise ValueError("oturum dosyası işçi yazma alanında")
        bayraklar = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(ham, bayraklar)
        try:
            bilgi = os.fstat(fd)
            if not stat.S_ISREG(bilgi.st_mode):
                raise ValueError("oturum dosyası düzenli dosya değil")
            if bilgi.st_nlink != 1:
                raise ValueError("oturum dosyası birden çok bağa sahip")
            if bilgi.st_uid != os.geteuid():
                raise ValueError("oturum dosyası süreç kullanıcısına ait değil")
            with os.fdopen(fd, encoding="utf-8") as akis:
                fd = -1
                icerikler[ham.name] = akis.read()
        finally:
            if fd >= 0:
                os.close(fd)
    return oturum_ham.resolve(strict=True), icerikler


def _onay_oku(ham):
    """Güvenli açılmış olay günlüğünden son geçerli kullanıcı onayını çıkarır."""
    olaylar = [json.loads(satir) for satir in ham.splitlines() if satir]
    onaylar = [olay for olay in olaylar
               if isinstance(olay, dict) and olay.get("tur") == "kullanici_onayi"]
    if not onaylar:
        raise ValueError("kullanıcı onayı yok")
    olay = onaylar[-1]; veri = olay.get("veri")
    sha = veri.get("sozlesme_sha256") if isinstance(veri, dict) else None
    if (olay.get("aktor") != "kullanici" or not isinstance(veri, dict)
            or veri.get("durum") != "onaylandi"
            or type(veri.get("sozlesme_revizyon")) is not int
            or not isinstance(sha, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", sha)
            or sha == "sha256:" + "0" * 64):
        raise ValueError("kullanıcı onayı geçersiz")
    return veri


def _ret(kat, yalitim_sonucu, hatalar, sonuclar=None):
    return {
        "gecti": False,
        "kat_sha256": kat.get("kat_sha256") if isinstance(kat, dict) else None,
        "kat_surumu": kat.get("kat_surumu") if isinstance(kat, dict) else None,
        "yorumlayici": {
            "kimlik": YORUMLAYICI_KIMLIGI,
            "ana_surum": YORUMLAYICI_ANA_SURUMU,
        },
        "kontrol_sonuclari": sonuclar or [],
        "yalitim_sonucu": yalitim_sonucu,
        "hatalar": hatalar,
    }


def gozlem_topla(kat, cikti_koku):
    """JSON çıktılarını kapı tarafında okuyup zorunlu durum sarmallarını üretir."""
    dogrula(kat, kok=cikti_koku)
    kok = Path(cikti_koku).resolve()
    sonuc = {}
    for kontrol in kat["kontroller"]:
        anahtar = kontrol["gozlem"]
        if not anahtar.startswith("cikti:") or "#" not in anahtar:
            raise KatHatasi("toplayıcı yalnız çıktı JSON gözlemini destekler")
        yol, isaretci = anahtar[6:].split("#", 1)
        ham_dosya = kok / yol
        kirik_bag = ham_dosya.is_symlink() and not ham_dosya.exists()
        dosya = ham_dosya.resolve(strict=False)
        if not dosya.is_relative_to(kok):
            raise KatHatasi("gözlem çıktı kökünden kaçamaz")
        try:
            def cift_yok(ciftler):
                sonuc = {}
                for ad, deger in ciftler:
                    if ad in sonuc: raise ValueError("yinelenen JSON anahtarı")
                    sonuc[ad] = deger
                return sonuc
            if kirik_bag:
                raise OSError("kırık sembolik bağ")
            veri = json.loads(dosya.read_text(encoding="utf-8"), object_pairs_hook=cift_yok)
            if not isaretci.startswith("/"):
                raise KeyError(isaretci)
            for ham_parca in isaretci[1:].split("/"):
                if "~" in ham_parca and re.search(r"~(?![01])", ham_parca):
                    raise ValueError("geçersiz JSON Pointer kaçışı")
                parca = ham_parca
                parca = parca.replace("~1", "/").replace("~0", "~")
                if isinstance(veri, list):
                    if not parca.isdigit() or (len(parca) > 1 and parca.startswith("0")): raise ValueError(parca)
                    veri = veri[int(parca)]
                else: veri = veri[parca]
        except FileNotFoundError:
            sonuc[anahtar] = {"durum": "okunamadi" if kirik_bag else "dosya_yok"}
        except (KeyError, IndexError):
            sonuc[anahtar] = {"durum": "bulunamadi"}
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            sonuc[anahtar] = {"durum": "okunamadi"}
        else:
            sonuc[anahtar] = {"durum": "deger", "deger": veri}
    return sonuc


def kapi_karari(kat, cikti_koku, yalitim_sonucu, *, oturum_dizini,
                isci_yazilabilir_kokler, beklenen_kat_sha256, beklenen_kat_surumu, kok=None):
    """KAT ve OS yalıtım sonucundan, hiçbir eksiği başarı saymadan karar üretir.

    ``yalitim_sonucu`` değiştirilmeden makbuza taşınır. Adaptör yalıtımı
    yeniden yorumlamaz veya gevşetmez; yalnız açık ``gecti is True`` sonucunu
    başarı için zorunlu tutar.
    """
    try:
        dogrula(kat, kok=kok)
    except (KatHatasi, TypeError, KeyError) as exc:
        return _ret(kat, yalitim_sonucu, [f"kat_gecersiz:{exc}"])

    hatalar = []
    try:
        if (not isinstance(isci_yazilabilir_kokler, (list, tuple))
                or not isci_yazilabilir_kokler):
            raise ValueError("işçi yazılabilir kökler geçersiz")
        kokler = [Path(yol).resolve(strict=True) for yol in isci_yazilabilir_kokler]
        if any(not Path(yol).is_absolute() or not os.fspath(yol)
               for yol in isci_yazilabilir_kokler):
            raise ValueError("işçi yazılabilir kökler mutlak olmalı")
        oturum = Path(oturum_dizini).resolve(strict=True)
    except (TypeError, ValueError, OSError) as exc:
        return _ret(kat, yalitim_sonucu, ["oturum_veya_isci_yazilabilir_kokler_gecersiz"])
    if any(oturum == yol or oturum.is_relative_to(yol) for yol in kokler):
        return _ret(kat, yalitim_sonucu, ["oturum işçi yazma alanında"])
    try:
        oturum, oturum_icerikleri = oturum_dosyalari_dogrula(
            oturum_dizini, isci_yazilabilir_kokler)
    except (TypeError, ValueError, OSError) as exc:
        if str(exc).startswith("oturum dosyası "):
            return _ret(kat, yalitim_sonucu, [str(exc)])
        return _ret(kat, yalitim_sonucu,
                    ["oturum_veya_isci_yazilabilir_kokler_gecersiz"])
    try:
        onay_kaydi = _onay_oku(oturum_icerikleri["olaylar.jsonl"])
        from orvant_op.karsilama.sozlesme import sozlesme_hash
        sozlesme = json.loads(oturum_icerikleri["sozlesme.json"])
        if (sozlesme_hash(sozlesme) != onay_kaydi["sozlesme_sha256"]
                or sozlesme.get("revizyon") != onay_kaydi["sozlesme_revizyon"]):
            raise ValueError("onaylı sözleşme hash/revizyon uyuşmazlığı")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        hatalar.append(f"sozlesme_onayi_gecersiz:{exc}")
        onay_kaydi = None
    if (not isinstance(onay_kaydi, dict) or kat.get("sozlesme_sha256") != onay_kaydi.get("sozlesme_sha256")
            or kat.get("sozlesme_revizyon") != onay_kaydi.get("sozlesme_revizyon")):
        hatalar.append("sozlesme_onay_farki")
    if kat["kat_sha256"] != beklenen_kat_sha256:
        hatalar.append("kat_hash_farki")
    if kat["kat_surumu"] != beklenen_kat_surumu:
        hatalar.append("kat_surumu_farki")

    try: toplayici_kayitlari = gozlem_topla(kat, cikti_koku)
    except (KatHatasi, OSError, UnicodeError, TypeError, ValueError) as exc:
        return _ret(kat, yalitim_sonucu, hatalar + [f"gozlemler_gecersiz:{exc}"])
    for kontrol in kat["kontroller"]:
        if kontrol["gozlem"] not in toplayici_kayitlari:
            hatalar.append(f"eksik_gozlem:{kontrol['id']}")
    if any(hata.startswith("eksik_gozlem:") for hata in hatalar):
        return _ret(kat, yalitim_sonucu, hatalar)

    kapili_gozlemler = {}
    for kontrol in kat["kontroller"]:
        kayit = toplayici_kayitlari[kontrol["gozlem"]]
        if (not isinstance(kayit, dict) or kayit.get("durum") not in ("dosya_yok", "bulunamadi", "deger")
                or (kayit.get("durum") == "dosya_yok" and set(kayit) != {"durum"})
                or (kayit.get("durum") == "bulunamadi" and set(kayit) != {"durum"})
                or (kayit.get("durum") == "deger" and set(kayit) != {"durum", "deger"})):
            hatalar.append(f"gozlem_sarmali_gecersiz:{kontrol['id']}")
            continue
        if kayit["durum"] == "dosya_yok":
            hatalar.append(f"gozlem_dosyasi_yok:{kontrol['id']}")
        elif kayit["durum"] == "bulunamadi":
            if kontrol["islem"] != "yok":
                hatalar.append(f"gozlem_bulunamadi:{kontrol['id']}")
            kapili_gozlemler[kontrol["gozlem"]] = None
        elif kontrol["islem"] == "yok":
            kapili_gozlemler[kontrol["gozlem"]] = {"mevcut_deger": kayit["deger"]}
        else:
            kapili_gozlemler[kontrol["gozlem"]] = kayit["deger"]
    if any(h.startswith(("gozlem_sarmali_gecersiz:", "gozlem_bulunamadi:",
                         "gozlem_dosyasi_yok:")) for h in hatalar):
        return _ret(kat, yalitim_sonucu, hatalar)

    try:
        yorum = yorumla(kat, kapili_gozlemler, kok=kok)
    except (KatHatasi, TypeError, KeyError) as exc:
        return _ret(kat, yalitim_sonucu, hatalar + [f"yorumlama_hatasi:{exc}"])

    sonuclar = yorum.get("sonuclar") if isinstance(yorum, dict) else None
    if not isinstance(sonuclar, list):
        return _ret(kat, yalitim_sonucu, hatalar + ["kontrol_sonuclari_gecersiz"])

    zorunlu = [kontrol["id"] for kontrol in kat["kontroller"]]
    gorulen = []
    for sonuc in sonuclar:
        if not isinstance(sonuc, dict) or not isinstance(sonuc.get("kontrol_id"), str):
            hatalar.append("kontrol_sonucu_gecersiz")
            continue
        kimlik = sonuc["kontrol_id"]
        gorulen.append(kimlik)
        if kimlik not in zorunlu:
            hatalar.append(f"bilinmeyen_kontrol_sonucu:{kimlik}")
        if sonuc.get("gecti") is not True:
            hatalar.append(f"kontrol_basarisiz:{kimlik}")

    for kimlik in zorunlu:
        if gorulen.count(kimlik) == 0:
            hatalar.append(f"eksik_kontrol_sonucu:{kimlik}")
        elif gorulen.count(kimlik) > 1:
            hatalar.append(f"yinelenen_kontrol_sonucu:{kimlik}")

    if not isinstance(yalitim_sonucu, dict) or yalitim_sonucu.get("gecti") is not True:
        hatalar.append("yalitim_basarisiz")

    karar = _ret(kat, yalitim_sonucu, hatalar, sonuclar)
    karar["gecti"] = not hatalar
    return karar
