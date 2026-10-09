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


def oturum_dosyalari_dogrula(oturum_dizini, isci_yazilabilir_kokler, *, onay_kaynagi="s1"):
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
    if onay_kaynagi == "s1":
        okunacaklar = (oturum_ham / "karsilama/olaylar.jsonl",
                      oturum_ham / "karsilama/sozlesme.json")
    elif onay_kaynagi == "proje":
        okunacaklar = (oturum_ham / "proje.json", oturum_ham / "proje-durum.json")
    else:
        raise ValueError("bilinmeyen onay kaynağı")
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


def onay_oku(oturum_dizini, isci_yazilabilir_kokler, *, onay_kaynagi="s1"):
    """Onayı yalnız korumalı oturum dosyalarından alır; nesne kabul etmez."""
    oturum, icerikler = oturum_dosyalari_dogrula(
        oturum_dizini, isci_yazilabilir_kokler, onay_kaynagi=onay_kaynagi)
    if onay_kaynagi == "s1":
        from orvant_op.karsilama.sozlesme import sozlesme_hash
        onay = _onay_oku(icerikler["olaylar.jsonl"])
        sozlesme = json.loads(icerikler["sozlesme.json"])
        if (sozlesme_hash(sozlesme) != onay["sozlesme_sha256"]
                or sozlesme.get("revizyon") != onay["sozlesme_revizyon"]):
            raise ValueError("onaylı sözleşme hash/revizyon uyuşmazlığı")
        return onay, sozlesme
    from orvant_op.proje import _digest
    bag = json.loads(icerikler["proje.json"])
    durum = json.loads(icerikler["proje-durum.json"])
    sozlesme = bag["contract"]
    digest = bag.get("digest")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest == "0" * 64 or _digest(sozlesme) != digest
            or durum.get("authorized") is not True
            or durum.get("approval_digest") != digest
            or Path(sozlesme["session"]).resolve(strict=True) != oturum):
        raise ValueError("proje onay digest uyuşmazlığı veya onay yok")
    revizyon = sozlesme["plan"]["sozlesme_revizyon"]
    if type(revizyon) is not int:
        raise ValueError("proje sözleşme revizyonu geçersiz")
    return {"sozlesme_sha256": "sha256:" + digest,
            "sozlesme_revizyon": revizyon}, sozlesme


def _skill_modulu(ad):
    """Depo ve yayın engine yerleşiminde motorun kendi skill kopyasını yükler."""
    import importlib.util
    depo = Path(__file__).resolve().parents[2]
    yayin = depo.parent if depo.name == "engine" else depo / "araclar/yayin/sablon"
    yol = yayin / "skills/orvant/scripts" / (ad + ".py")
    spec = importlib.util.spec_from_file_location("orvant_kat_" + ad, yol)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _proje_kat(oturum_dizini, isci_yazilabilir_kokler, gorev):
    """Onay kapsamındaki ontology kurallarını yayın köprüsüyle derler."""
    kat_bridge = _skill_modulu("kat_bridge")
    onay, sozlesme = onay_oku(oturum_dizini, isci_yazilabilir_kokler,
                             onay_kaynagi="proje")
    kayit = sozlesme["tasks"][gorev]
    kurallar = kayit["selection"]["definition"].get("acceptance_rules", [])
    if not kurallar:
        return None, {}
    # Tam ontology kesiti sözleşme digest'ine bağlıdır. İşçinin verdiği
    # gözlem veya aday ağacındaki .project dosyası otorite olamaz.
    state = kayit["kat_state"]
    _skill_modulu("acceptance").validate_contract({"acceptance_rules": kurallar}, state)
    kat, gozlemler = kat_bridge.kurallardan_kat(state, kurallar, gorev=gorev)
    kat.update(onay)
    from orvant_op.kabul_kat import kat_hash
    kat["kat_sha256"] = kat_hash(kat)
    return kat, gozlemler


def proje_kat_derle(*, oturum_dizini, isci_yazilabilir_kokler, gorev):
    return _proje_kat(oturum_dizini, isci_yazilabilir_kokler, gorev)[0]


def _proje_kapi(kat, yalitim_sonucu, oturum_dizini, isci_yazilabilir_kokler,
                beklenen_kat_sha256, beklenen_kat_surumu):
    kat_bridge = _skill_modulu("kat_bridge")
    from orvant_op.kabul_kat import kat_hash
    guvenli, gozlemler = _proje_kat(oturum_dizini, isci_yazilabilir_kokler, kat["gorev"])
    if (guvenli != kat or kat_hash(kat) != beklenen_kat_sha256
            or type(beklenen_kat_surumu) is not int or kat["kat_surumu"] != beklenen_kat_surumu):
        raise ValueError("proje KAT dayanak/hash/sürüm uyuşmazlığı")
    # Köprü biçimi kendi doğrulayıcısından geçer; onay üstverisi ayrı bağlıdır.
    kopru = {k: v for k, v in kat.items() if k not in ("sozlesme_sha256", "sozlesme_revizyon")}
    kopru["kat_sha256"] = kat_bridge.kat_hash(kopru)
    sonuclar = kat_bridge.yorumla(kopru, gozlemler)["sonuclar"]
    hatalar = ["kontrol_basarisiz:" + s["kontrol_id"] for s in sonuclar if not s["gecti"]]
    if not isinstance(yalitim_sonucu, dict) or yalitim_sonucu.get("gecti") is not True:
        hatalar.append("yalitim_basarisiz")
    sonuc = _ret(kat, yalitim_sonucu, hatalar, sonuclar)
    sonuc.update(gecti=not hatalar, onay_kaynagi="proje")
    return sonuc


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


def gozlem_topla(kat, cikti_koku, *, azami_cikti_bayt=8 * 1024 * 1024,
                 kaynak_siniri_bildir=False):
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
            fd = os.open(dosya, os.O_RDONLY | os.O_CLOEXEC)
            try:
                once = os.fstat(fd)
                if not stat.S_ISREG(once.st_mode):
                    raise OSError("çıktı düzenli dosya değil")
                if once.st_size > azami_cikti_bayt:
                    raise OSError("çıktı dosyası boyut sınırını aşıyor")
                with os.fdopen(fd, encoding="utf-8") as akis:
                    fd = -1
                    ham = akis.read(azami_cikti_bayt + 1)
                    sonra = os.fstat(akis.fileno())
                if len(ham.encode("utf-8")) > azami_cikti_bayt or sonra.st_size > azami_cikti_bayt:
                    raise OSError("çıktı dosyası boyut sınırını aşıyor")
            finally:
                if fd >= 0:
                    os.close(fd)
            veri = json.loads(ham, object_pairs_hook=cift_yok)
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
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            durum = ("kaynak_siniri" if kaynak_siniri_bildir and isinstance(exc, OSError)
                     and "boyut sınırını" in str(exc) else "okunamadi")
            sonuc[anahtar] = {"durum": durum}
        else:
            sonuc[anahtar] = {"durum": "deger", "deger": veri}
    return sonuc


def kapi_karari(kat, cikti_koku, yalitim_sonucu, *, oturum_dizini,
                isci_yazilabilir_kokler, beklenen_kat_sha256, beklenen_kat_surumu, kok=None,
                azami_cikti_bayt=8 * 1024 * 1024, onay_kaynagi="s1"):
    """KAT ve OS yalıtım sonucundan, hiçbir eksiği başarı saymadan karar üretir.

    ``yalitim_sonucu`` değiştirilmeden makbuza taşınır. Adaptör yalıtımı
    yeniden yorumlamaz veya gevşetmez; yalnız açık ``gecti is True`` sonucunu
    başarı için zorunlu tutar.
    """
    if onay_kaynagi == "proje":
        return _proje_kapi(kat, yalitim_sonucu, oturum_dizini, isci_yazilabilir_kokler,
                           beklenen_kat_sha256, beklenen_kat_surumu)
    if onay_kaynagi != "s1":
        return _ret(kat, yalitim_sonucu, ["bilinmeyen_onay_kaynagi"])
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

    try: toplayici_kayitlari = gozlem_topla(
        kat, cikti_koku, azami_cikti_bayt=azami_cikti_bayt,
        kaynak_siniri_bildir=True)
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
        if (not isinstance(kayit, dict) or kayit.get("durum") not in ("dosya_yok", "bulunamadi", "deger", "kaynak_siniri")
                or (kayit.get("durum") == "dosya_yok" and set(kayit) != {"durum"})
                or (kayit.get("durum") == "bulunamadi" and set(kayit) != {"durum"})
                or (kayit.get("durum") == "kaynak_siniri" and set(kayit) != {"durum"})
                or (kayit.get("durum") == "deger" and set(kayit) != {"durum", "deger"})):
            hatalar.append(f"gozlem_sarmali_gecersiz:{kontrol['id']}")
            continue
        if kayit["durum"] == "kaynak_siniri":
            hatalar.append(f"kat_kaynak_siniri:{kontrol['id']}")
        elif kayit["durum"] == "dosya_yok":
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
                         "gozlem_dosyasi_yok:", "kat_kaynak_siniri:")) for h in hatalar):
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
