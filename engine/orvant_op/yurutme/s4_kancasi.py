"""S4 önerisini S3 yürütme yetkisiyle uygular; kabul sözleşmesini değiştirmez."""

import json
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from orvant_op.iz import kaydet
from orvant_op.bagimli_ciktilar import istenen_ciktilar
from orvant_op.mimar.dogrulama import durumlari_hesapla as _durumlari_hesapla

from orvant_op.teshis.karar import teshis_et
from orvant_op.teshis.gecici_artik import artik_incele, artik_temizle
from orvant_op.butce import kok_butce_durumu


def orvant_surumu():
    """Teşhis ve yeniden değerlendirme için ortak, değiştirilebilir kod kimliği."""
    from orvant_op.iz import kod_surumu
    return kod_surumu()


def _teshis_yaz(yurutme, kayit):
    yurutme.kok.mkdir(parents=True, exist_ok=True)
    with (yurutme.kok / "teshis.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    kaydet(yurutme.iz_yolu, yurutme.calisma.name, "ariza_teshisi", calisma=yurutme.calisma,
           aktor_tur="orvant", kimlik="teshis", sonuc="ret",
           ozet=kayit["gerekce"], kanit=[p for p in (kayit.get("makbuz"), kayit.get("iade_makbuzu")) if p],
           gorev=kayit["gorev"], ham={k: kayit[k] for k in ("gorev", "sinif", "eylem", "imza")})


def istisnayi_isle(yurutme, plan, gorev, exc, *, isci_kostu):
    """İşçi öncesi iç hatayı ayrı makbuzla belgeler; asıl istisnayı maskelemez."""
    try:
        surum = orvant_surumu()
        karar = teshis_et({"gorev": gorev, "istisna": str(exc),
            "istisna_turu": type(exc).__name__, "isci_kostu": isci_kostu,
            "orvant_surumu": surum, "plan_ozeti": {"surum": plan["surum"]},
            "onceki_teshisler": _satirlar(yurutme.kok / "teshis.jsonl")})
        kayit = {**karar, "t": datetime.now(timezone.utc).isoformat(), "gorev": gorev["id"],
                 "makbuz": None, "iade_makbuzu": None, "istisna": str(exc),
                 "istisna_turu": type(exc).__name__, "isci_kostu": isci_kostu}
        if karar["sinif"] == "orvant_kusuru":
            kullanilan = len(yurutme._makbuzlar(gorev))
            dizin = yurutme.kok / "iade_makbuzlari"
            dizin.mkdir(parents=True, exist_ok=True)
            n = 1
            while (dizin / f"{gorev['id']}-{n}.json").exists():
                n += 1
            yol = dizin / f"{gorev['id']}-{n}.json"
            iade = {k: kayit[k] for k in ("gorev", "istisna", "istisna_turu", "imza", "orvant_surumu", "t")}
            iade.update(gorev_denemesi=kullanilan + 1, butce_deneme=gorev["butce"]["deneme"],
                        kullanilan_deneme=kullanilan, isci_kostu=False)
            yol.write_text(json.dumps(iade, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            kayit["iade_makbuzu"] = str(yol)
            yurutme._olay("deneme_iade_edildi", gorev["id"], is_turu="yeniden_is_kapsami",
                          iade_makbuzu=str(yol), imza=karar["imza"], orvant_surumu=surum)
            yurutme._engel(gorev, "Orvant kusuru: " + str(exc))
        _teshis_yaz(yurutme, kayit)
        return kayit
    except Exception as hata:
        try:
            yurutme._uyari("s4_istisna_kancasi_hatasi", gorev["id"], hata=f"{type(hata).__name__}: {hata}")
        except Exception:
            # Kayıt altyapısı da bozuk olabilir; yürütmenin asıl hatası korunur.
            pass
        return None


def _kehanet_sha256(yurutme, kimlik):
    from orvant_op.mimar.kehanet import kehanet_yolu
    yol = kehanet_yolu(yurutme.calisma, kimlik)
    return hashlib.sha256(yol.read_bytes()).hexdigest() if yol.is_file() else None


def yeniden_degerlendirilecekler(yurutme, gorev_id=None):
    """Planı, dosyaları veya izi değiştirmeden uygulanabilir sürüm adaylarını döndürür."""
    surum = orvant_surumu()
    son = {k["gorev"]: k for k in _satirlar(yurutme.kok / "teshis.jsonl")}
    plan = yurutme._plan()
    gorevler = {g["id"]: g for g in plan["gorevler"]}
    adaylar = []
    # Eski salt-okuma çağıranları yalnız plan/sürüm arayüzünü sağlayabilir.
    bekletilenler = yurutme._bekletilenler(plan) if hasattr(yurutme, "_bekletilenler") else {}
    for kimlik, eski in son.items():
        if kimlik in bekletilenler:
            continue
        if gorev_id is not None and kimlik != gorev_id:
            continue
        gorev = gorevler.get(kimlik)
        if not gorev or gorev["durum"] != "engelli":
            continue
        degisti = eski.get("orvant_surumu") != surum
        if eski["sinif"] == "orvant_kusuru" and degisti:
            eylem = "yeniden_dene"
            gerekce = f"Orvant sürümü değişti: {eski.get('orvant_surumu')} → {surum}; iade edilen deneme yeniden deneniyor"
        elif eski["sinif"] == "dogrulayici_kusuru":
            sha = _kehanet_sha256(yurutme, kimlik)
            # Silinmiş kehanet onarım kanıtı değildir; kehanetsiz kapıyla kabul açma.
            if eski.get("kehanet_sha256") and sha is None:
                continue
            if not (degisti or sha != eski.get("kehanet_sha256")):
                continue
            if not yurutme._agac(yurutme._depo(plan), kimlik).is_dir():
                continue
            eylem, gerekce = "yeniden_denetle", "doğrulayıcı değişti"
        else:
            continue
        adaylar.append({"gorev": kimlik, "sinif": eski["sinif"], "eylem": eylem,
                        "gerekce": gerekce, "eski_surum": eski.get("orvant_surumu"),
                        "yeni_surum": surum})
    return adaylar


def yeniden_degerlendir(yurutme, gorev_id=None):
    """Yalnız değişmiş Orvant/kehanet için son teşhisi bir kez yeniden uygular."""
    son = {k["gorev"]: k for k in _satirlar(yurutme.kok / "teshis.jsonl")}
    uygulanan = []
    for kimlik, eski in son.items():
        if gorev_id is not None and kimlik != gorev_id:
            continue
        # Önceki kapı kabulü bağımlıları ve planı değiştirmiş olabilir.
        adaylar = yeniden_degerlendirilecekler(yurutme, kimlik)
        if not adaylar:
            continue
        aday = adaylar[0]
        yeni = {**eski, "t": datetime.now(timezone.utc).isoformat(),
                "orvant_surumu": aday["yeni_surum"], "eylem": aday["eylem"],
                "gerekce": aday["gerekce"], "hipotez": aday["gerekce"]}
        if aday["eylem"] == "yeniden_dene":
            plan = yurutme._plan()
            gorev = next(g for g in plan["gorevler"] if g["id"] == kimlik)
            _teshis_yaz(yurutme, yeni)
            yurutme._olay("orvant_kusuru_yeniden_dene", kimlik, imza=yeni["imza"],
                          orvant_surumu=aday["yeni_surum"], gerekce=aday["gerekce"])
            gorev["durum"] = "hazir"
            yurutme._bagimlilari_ac(plan)
            yurutme._kaydet_plan(plan)
        else:
            yeni["kehanet_sha256"] = _kehanet_sha256(yurutme, kimlik)
            _teshis_yaz(yurutme, yeni)
            try:
                yurutme.kapi(kimlik)
            except Exception as hata:
                # Yeniden denetim hatası diğer görevlerin yürütülmesini durdurmaz; kapı zaten engel kaydetti.
                yurutme._uyari("s4_yeniden_denetim_hatasi", kimlik, hata=f"{type(hata).__name__}: {hata}")
        uygulanan.append(yeni)
    return uygulanan


def durumlari_hesapla_korunmus(plan, cozulmus_kararlar=()):
    """S3 hesaplamasında kullanıcı girdisi bekleyen görevleri kilitli tutar."""
    bekleyen = {g["id"]: g["durum"] for g in plan["gorevler"]
                if g["durum"] in ("girdi_bekliyor", "kota_bekleniyor")}
    _durumlari_hesapla(plan, cozulmus_kararlar)
    for gorev in plan["gorevler"]:
        if gorev["id"] in bekleyen:
            gorev["durum"] = bekleyen[gorev["id"]]
    return plan


def _satirlar(yol):
    if not yol.exists():
        return []
    return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines() if s.strip()]


def beklenen_girdiler(yurutme, plan):
    """Durum çıktısına açık beklenen girdi ve dayandığı engeli ekler."""
    engeller = _satirlar(yurutme.kok / "engeller.jsonl")
    return [{"gorev": g["id"], "tur": "girdi",
             "beklenen": next((e["neden"].split(": ", 1)[-1] for e in reversed(engeller)
                               if e["gorev"] == g["id"]), "Kullanıcı girdisi"),
             "neden": "İşçi görevi tamamlamak için girdi bekliyor"}
            for g in plan["gorevler"] if g["durum"] == "girdi_bekliyor"]


def basarisizligi_isle(yurutme, plan, gorev, makbuz_yolu, *, istisna=None):
    """Makbuzdan teşhis kaydeder; yalnız S3 durum ve mevcut kapı/işçi metotlarını çağırır."""
    if hasattr(yurutme, "_jeton_dogrula"):
        yurutme._jeton_dogrula(gorev["id"])
    # Yalnız karantina S4'ü durdurur; yeniden denetim işareti bekletmesi (G-057) teşhisi engellemez.
    if str(yurutme._bekletilenler(yurutme._plan()).get(gorev["id"], "")).startswith("karantina"):
        gorev["durum"] = "engelli"
        yurutme._kaydet_plan(plan)
        return {"gorev": gorev["id"], "durum": "engelli", "makbuz": str(makbuz_yolu)}
    makbuz = json.loads(makbuz_yolu.read_text(encoding="utf-8"))
    oncekiler = [x for x in _satirlar(yurutme.kok / "teshis.jsonl") if x.get("gorev") == gorev["id"]]
    makbuzlar = [json.loads(p.read_text(encoding="utf-8")) for p in yurutme._makbuzlar(gorev)]
    butce = kok_butce_durumu(yurutme.calisma, yurutme._plan(), gorev["id"])
    toplam = butce["harcanan"]
    karar_yolu = yurutme.calisma / "plan" / "kararlar.json"
    kararlar = json.loads(karar_yolu.read_text(encoding="utf-8")) if karar_yolu.exists() else []
    envanter_yolu = yurutme.calisma / "plan" / "envanter.json"
    envanter = json.loads(envanter_yolu.read_text(encoding="utf-8")).get("kayitlar", []) if envanter_yolu.exists() else []
    izinler = [{k: y.get(k) for k in ("id", "durum", "onay_olay_id", "ayrinti")}
              for y in plan["yetki_istekleri"] if y["id"] in gorev["yetki_istek_ids"]]
    plan_ozeti = {"surum": plan["surum"], "sozlesme_revizyon": plan["sozlesme_revizyon"],
                  "gorev": {k: gorev[k] for k in ("id", "amac", "yazilabilir", "kabul")},
                  "token_butcesi": gorev["butce"]["token"]}
    baglam = {"makbuz": makbuz, "gorev": gorev, "plan_ozeti": plan_ozeti,
              "istenen_bagimli_ciktilar": istenen_ciktilar(
                  yurutme.calisma, plan, gorev, makbuz.get("isci_ozeti")),
              "kararlar": kararlar, "izinler": izinler,
              "girdi": makbuz.get("isci_ozeti"), "istisna": str(istisna) if istisna is not None else None,
              "istisna_turu": (type(istisna).__name__ if isinstance(istisna, BaseException)
                               else "RuntimeError" if istisna is not None else None),
              "isci_kostu": True, "orvant_surumu": orvant_surumu(), "envanter": envanter,
              "onceki_teshisler": oncekiler, "toplam_tokens": toplam}
    baglam["yetenek_manifesti"] = makbuz.get("yetenek_manifesti")
    # Kararsız kapı dizisi makbuzun içindedir; S4'e ayrıca taşımak iz incelemesini kolaylaştırır.
    baglam["kapi_tekrarlari"] = makbuz.get("kapi_tekrarlari", [])
    if makbuz.get("kapsam_ihlalleri"):
        agac = yurutme._agac(yurutme._depo(plan), gorev["id"])
        baglam["artik_incelemesi"] = artik_incele(agac, gorev, makbuz["kapsam_ihlalleri"])
    karar = teshis_et(baglam)
    if karar["sinif"] == "gecici_artik" and karar["eylem"] == "yeniden_denetle":
        from orvant_op.yurutme.akis import _json_yaz

        def silindi(ad):
            makbuz.setdefault("temizlenen_artiklar", []).append(ad)
            _json_yaz(makbuz_yolu, makbuz)
            yurutme._olay("gecici_artik_temizlendi", gorev["id"],
                          temizlenen_artiklar=[ad], makbuz=str(makbuz_yolu))

        try:
            artik_temizle(agac, gorev, baglam["artik_incelemesi"], yurutme._kapsam, silindi)
        except (OSError, ValueError) as exc:
            karar["eylem"] = "yukselt"
            karar["gerekce"] = karar["hipotez"] = "Artık temizliği durduruldu: " + str(exc)
    if butce["kalan"] <= 0 and karar["eylem"] == "yeniden_dene":
        karar["eylem"] = "yukselt"
        karar["gerekce"] += "; kümülatif token bütçesi doldu"
    if len(makbuzlar) >= gorev["butce"]["deneme"] and karar["eylem"] == "yeniden_dene":
        karar["eylem"] = "yukselt"
        karar["gerekce"] += "; görev deneme bütçesi doldu"
    kayit = {"t": datetime.now(timezone.utc).isoformat(), "gorev": gorev["id"],
             "makbuz": str(makbuz_yolu), "toplam_tokens": toplam, **karar}
    yurutme.kok.mkdir(parents=True, exist_ok=True)
    with (yurutme.kok / "teshis.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    kaydet(yurutme.iz_yolu, yurutme.calisma.name, "ariza_teshisi", calisma=yurutme.calisma,
           aktor_tur="orvant", kimlik="teshis", sonuc="ret",
           ozet=karar["gerekce"], kanit=[str(makbuz_yolu)], gorev=gorev["id"],
           ham={"gorev": gorev["id"], "sinif": karar["sinif"],
                "eylem": karar["eylem"], "imza": karar["imza"]})
    eylem = karar["eylem"]
    if eylem == "yeniden_dene":
        if karar.get("bekleme_saniye"):
            time.sleep(karar["bekleme_saniye"])
        yurutme._engel(gorev, "S4 teşhisi: " + karar["gerekce"],
                       thread_id=makbuz.get("thread_id"), goal=makbuz.get("goal"))
        gorev["durum"] = "hazir"
        yurutme._kaydet_plan(plan)
        # İşçi isteği önceki makbuz hatalarını içerir; çağrı yine yalnız S3 içindedir.
        return yurutme._gorev_yurut(plan, gorev)
    if eylem == "yeniden_denetle":
        sonuc = yurutme.kapi(gorev["id"])
        if sonuc.get("hatalar"):
            gorev["durum"] = "engelli"
            yurutme._kaydet_plan(plan)
            sonuc["durum"] = "engelli"
        return sonuc
    if eylem == "yeniden_planla":
        from orvant_op.mimar.durum import Mimar
        try:
            with yurutme._kilit():
                sonuc = Mimar(yurutme.calisma, yurutucu=getattr(yurutme, "plan_yurutucu", None),
                              iz_yolu=yurutme.iz_yolu).yeniden_planla(
                                  karar["gerekce"], gorev=gorev["id"],
                                  teshis_dosyasi=yurutme.kok / "teshis.jsonl")
            yeni_gorev = next(g for g in sonuc["plan"]["gorevler"] if g["id"] == gorev["id"])
            return {"gorev": gorev["id"], "durum": yeni_gorev["durum"],
                    "makbuz": str(makbuz_yolu), "teshis": karar,
                    "plan_surumu": sonuc["surum"]}
        except Exception as exc:
            gorev["durum"] = "engelli"
            yurutme._engel(gorev, "Yeniden planlama başarısız: " + str(exc),
                           thread_id=makbuz.get("thread_id"), goal=makbuz.get("goal"))
            yurutme._kaydet_plan(plan)
            return {"gorev": gorev["id"], "durum": "engelli", "makbuz": str(makbuz_yolu),
                    "teshis": karar, "yukselt": str(exc)}
    if eylem == "girdi_bekle":
        gorev["durum"] = "girdi_bekliyor"
    elif eylem == "yetki_bekle":
        gorev["durum"] = "yetki_bekliyor"
    elif eylem == "uzlastir":
        gorev["durum"] = "engelli"
    else:
        gorev["durum"] = "engelli"
    neden = (makbuz.get("isci_ozeti") or makbuz.get("hatalar") or [karar["belirti"]])
    soru = karar.get("kullanici_sorusu")
    yurutme._engel(gorev, karar["gerekce"] + ": " +
                   (neden if isinstance(neden, str) else "; ".join(neden)) +
                   (" " + soru if soru else ""),
                   thread_id=makbuz.get("thread_id"), goal=makbuz.get("goal"))
    yurutme._kaydet_plan(plan)
    return {"gorev": gorev["id"], "durum": gorev["durum"],
            "makbuz": str(makbuz_yolu), "teshis": karar,
            **({"kullanici_sorusu": soru} if soru else {})}


def mudahale_bagi(olaylar, mudahale, *, calisma_yolu=None):
    """Aynı proje/görevin kesin önceki, en son açık teşhisini bulur.

    Kabul kapısı veya birleştirme teşhisi kapatır. Görevsiz olaylara proje
    düzeyinde tahmin yapılmaz; aynı zaman damgası da nedensellik kanıtı değildir.
    """
    from orvant_op.teshis.karar import SINIFLAR

    gorev = mudahale.get("ham", {}).get("gorev")
    if not isinstance(gorev, str) or not gorev.strip():
        return {}
    zaman = lambda e: datetime.fromisoformat(e["t"].replace("Z", "+00:00"))
    calisma = mudahale.get("ham", {}).get("calisma")

    def ayni_oturum(e):
        aday = e.get("ham", {}).get("calisma")
        # Yeni kayıtlarda çalışma kökü süreçler arası kalıcı kimliktir. Bir
        # tarafta bulunup diğerinde bulunmuyorsa ortak ORVANT_IZ için tahmin
        # yapma. Eski kayıtlarda kesin proje kimliğiyle geriye uyumlu kal.
        if calisma is not None or aday is not None:
            if not isinstance(calisma, str) or not isinstance(aday, str):
                return False
            if calisma == aday:
                return e.get("proje") == mudahale.get("proje")
            # Eski mutlak kayıt yalnız çalışan oturumun bilinen gerçek köküyle
            # tam eşitse yeni kalıcı kimliğe bağlanabilir. Dizin adından tahmin
            # etmek ayrı makinelerdeki/üst dizinlerdeki oturumları karıştırır.
            eski_calisma = Path(calisma).is_absolute()
            eski_aday = Path(aday).is_absolute()
            if eski_calisma == eski_aday:
                return False
            eski, yeni = (calisma, aday) if eski_calisma else (aday, calisma)
            if calisma_yolu is None or e.get("proje") != mudahale.get("proje"):
                return False
            from orvant_op.iz import calisma_kimligi
            return (Path(eski).resolve() == Path(calisma_yolu).resolve()
                    and yeni == calisma_kimligi(calisma_yolu, mudahale.get("proje")))
        return e.get("proje") == mudahale.get("proje")

    onceki = [e for e in olaylar
              if ayni_oturum(e)
              and e.get("ham", {}).get("gorev") == gorev
              and not e.get("miras") and not e.get("benzetim")
              and e.get("ham", {}).get("iptal") is not True
              and zaman(e) < zaman(mudahale)]
    ilgili = [e for e in onceki
              if (e["is_turu"] == "ariza_teshisi" and e["ham"].get("sinif") in SINIFLAR)
              or (e["is_turu"] in {"kapi_karari", "birlestirme"} and e["sonuc"] == "ok")]
    if not ilgili:
        return {}
    son_zaman = max(zaman(e) for e in ilgili)
    sonlar = {e["id"]: e for e in ilgili if zaman(e) == son_zaman}
    # Aynı anda kapanış/teşhis veya birden çok teşhis varsa sıra kanıtı yoktur.
    if len(sonlar) != 1:
        return {}
    son = next(iter(sonlar.values()))
    if son["is_turu"] != "ariza_teshisi":
        return {}
    return {"teshis_ref": son["id"], "sinif": son["ham"]["sinif"]}



def mudahale_bagini_ekle(iz_yolu, item, *, calisma_yolu=None):
    """Ortak yazıcının kullanacağı dar kanca; şema ve eski kayıtlar değişmez."""
    from pathlib import Path
    from orvant_gelisim.kayit import oku

    if not item.get("mudahale") or item.get("benzetim") or not item["ham"].get("gorev"):
        return item
    yol = Path(iz_yolu)
    olaylar = oku(yol) if yol.exists() else []
    # Çağıran tahmini/eskimiş bir bağı aktarmışsa onun yerine doğrulanmış bağ gelir.
    item["ham"].pop("teshis_ref", None)
    item["ham"].pop("sinif", None)
    item["ham"].update(mudahale_bagi(olaylar, item, calisma_yolu=calisma_yolu))
    return item
