"""Salt okunan tur kararları ve mevcut yetkilerle bunları uygulayan döngü."""

import json
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from orvant_op import ayarlar
from orvant_op.butce import etkin_toplam_butce
from orvant_op.iz import kaydet
from orvant_op.mimar.cli import kehanet_hazirla
from orvant_op.mimar.durum import Mimar
from orvant_op.mimar.kehanet import kehanet_yolu, sozlesme_yolu, kehanet_gecersiz_mi
from orvant_op.yurutme import Yurutme, s4_kancasi
from orvant_op.yurutme.zamanlayici import girdi_bekleme_baglami
from orvant_op.yurutucu import YurutucuZamanAsimi
from orvant_op.yurutme.s4_kancasi import beklenen_girdiler
from .kota import kota_oku
from .soru_kuyrugu import (SoruKuyrugu, atomik_yaz, satirlar, simdi, birlestir,
                          gecerlilik_denetle, paket_metni, olcum, kok_eslesir, oz_ayni, TUR_DURUMLARI)

# S3 kehanet denetimi engelleri (yurutme/akis.py `_gorev_yurut`).
KEHANET_ENGELLERI = {"kehanet zayıf", "kehanet geçersiz", "kehanet doğru referansı reddediyor"}
TESHIS_DURUMLARI = {"engelli", "ret", "girdi_bekliyor", "yetki_bekliyor"}


def teshis_kimligi(kayit):
    return tuple(kayit.get(k) for k in ("gorev", "imza", "kanit_ozeti", "t"))


def _kisa(metin, sinir=240):
    """Kullanıcıya dönük soru tek satır ve kısa kalır; tam metin `neden`dedir."""
    metin = " ".join(str(metin).replace("*", "").replace("`", "").split())
    return metin if len(metin) <= sinir else metin[:sinir - 1].rstrip() + "…"


def soru_iz_turu(soru):
    if soru["tur"] == "orvant_kusuru":
        return "ariza_teshisi"
    return "hedef_netlestirme" if soru["tur"] in ("karar", "girdi") else "yetki_karari"


class KotaSiniri(RuntimeError):
    """Model kullanan adım başlamadan kota eşiğine ulaşıldı."""


class Operator:
    def __init__(self, calisma, *, yurutucu=None, goals_db=None, kehanet_yurutucu=None,
                 plan_yurutucu=None, iz_yolu=None, oturum_dizini=None, kota_esigi=None,
                 skill_dizinleri=None, path=None, yurut_zaman_asimi=3600, kehanet_zaman_asimi=1500):
        self.calisma = Path(calisma).resolve()
        self.kok = self.calisma / "operator"
        from orvant_op.iz import oturum_iz_yolu
        self.iz_yolu = oturum_iz_yolu(self.calisma, iz_yolu)
        if yurut_zaman_asimi <= 0 or kehanet_zaman_asimi <= 0:
            raise ValueError("Zaman aşımı sınırları pozitif olmalı")
        self.yurut_zaman_asimi = yurut_zaman_asimi
        self.kehanet_zaman_asimi = kehanet_zaman_asimi
        self.yurutme = Yurutme(self.calisma, yurutucu=yurutucu, goals_db=goals_db, iz_yolu=iz_yolu,
                               isci_zaman_asimi=yurut_zaman_asimi)
        self.yurutme.plan_yurutucu = plan_yurutucu
        self.mimar = Mimar(self.calisma, yurutucu=plan_yurutucu, iz_yolu=iz_yolu)
        self.kehanet_yurutucu = kehanet_yurutucu
        self.oturum_dizini = oturum_dizini
        # G-162: None → ayar katmanı; ayar yoksa eşik kapalı (kota yine okunur ve raporlanır).
        self.kota_esigi = ayarlar.kota_esigi() if kota_esigi is None else kota_esigi
        self.envanter_ayarlari = {"skill_dizinleri": skill_dizinleri, "path": path}
        self.sorular = SoruKuyrugu(self.calisma)
        self.kehanet_denendi = set()
        self.uyarilar = []
        self.son_kota = {"used_percent": None, "dosya": None}

    def _iz(self, tur, ozet, gorev=None, *, sonuc="ok", ham=None):
        kaydet(self.iz_yolu, self.calisma.name, tur, calisma=self.calisma, aktor_tur="orvant", kimlik="operator",
               gorev=gorev, ozet=ozet, sonuc=sonuc, ham=ham)

    def _kota(self):
        self.son_kota = kota_oku(self.oturum_dizini)
        oran = self.son_kota["used_percent"]
        if oran is None and "kota okunamadı" not in self.uyarilar:
            self.uyarilar.append("kota okunamadı")
        if oran is not None and self.kota_esigi is not None and oran >= self.kota_esigi:
            raise KotaSiniri("Kota eşiğine ulaşıldı")

    def _gorev(self, kimlik):
        return next(g for g in self.yurutme._plan()["gorevler"] if g["id"] == kimlik)

    def _komut(self, *parcalar):
        return "python3 -m orvant_op " + " ".join(shlex.quote(str(p)) for p in parcalar)

    def _soru(self, gorev, tur, neden, *, teshis=None, karar=None, istek=None, komut=None):
        teshis = teshis or {}
        baglam = {"teshis_imza": teshis.get("imza"), "kanit_ozeti": teshis.get("kanit_ozeti", "")}
        metin = teshis.get("kullanici_sorusu")
        anahtar = teshis.get("imza", "bekleme") + "|" + teshis.get("kanit_ozeti", "")
        if tur == "karantina":
            kayit = self.yurutme.karantinalar()[gorev["id"]]
            anahtar = kayit["tetik"] + "|" + kayit["t"]
            metin = f"{kayit['tetik']}: {kayit['neden']}; kanıt: " + "; ".join(kayit["kanit"])
            komut = self._komut("yurut", "karantina-kaldir", self.calisma, gorev["id"], "<gerekçe>")
            baglam.update(secenekler=["kehaneti/sözleşmeyi incele, sonra kaldır", "görevi yeniden planla"],
                          risk="kapı bütünlüğü doğrulanmadan kaldırılırsa yanlış kabul riski")
        elif tur == "orvant_kusuru":
            surum = teshis.get("orvant_surumu")
            baglam.update(orvant_surumu=surum, bilgi=True,
                          kullanici_eylemi="Orvant'ı düzeltip yeni sürüm (commit) yayımla")
            anahtar += "|" + str(surum)
            metin = (f"Orvant hatası; düzeltme sürümü bekleniyor (sürüm {surum}). "
                     "İşçi koşmadı, deneme hakkı iade edildi; sürüm değişince görev kendiliğinden yeniden denenir.")
            komut = None
        elif tur == "karar":
            baglam["karar_id"] = karar["id"]
            soru_bilgisi = karar.get("soru") or {}
            baglam.update(secenekler=soru_bilgisi.get("secenekler") or [],
                          oneri=soru_bilgisi.get("onerilen") or karar.get("onerilen_varsayim"),
                          risk="; ".join(f"{ad}: {karar[alan]}" for alan, ad in (
                              ("etki", "etki"), ("geri_alinabilir", "geri alınabilir"),
                              ("belirsizlik", "belirsizlik")) if alan in karar) or None)
            anahtar = karar["id"]
            metin = (karar.get("soru") or {}).get("metin") or karar.get("baslik") or "Kararınızı belirtin."
            if teshis.get("sinif") == "urun_karari_eksik":
                baslik = _kisa(karar.get("baslik") or "ürün kararı", 100).rstrip(".?!")
                secenekler = baglam["secenekler"]
                secim = " veya ".join(_kisa(s, 60).rstrip(".?!") for s in secenekler)
                metin = (f"{karar['id']}: {baslik} — {secim} seçeneklerinden hangisi?"
                         if secim else f"{karar['id']}: {baslik} için kararınız nedir?")
        elif tur == "girdi":
            # Teşhis ve bağımsız bekleme gözlemi tek açık girdiye dönüşür.
            anahtar = "girdi"
            baglam["baslik"] = gorev["baslik"]
            beklenen = next((g["beklenen"] for g in beklenen_girdiler(
                self.yurutme, self.yurutme._plan()) if g["gorev"] == gorev["id"]), None)
            baglam["beklenen"] = beklenen or gorev["baslik"]
            metin = metin or beklenen or neden
            _, uygunluk = girdi_bekleme_baglami(self.calisma, gorev["id"])
            if uygunluk:
                if uygunluk.get("karar_ids"):
                    baglam["girdi_karar_id"] = uygunluk["karar_ids"][0]
                neden = "; ".join(str(n) for n in uygunluk.get("nedenler", [])) or neden
                metin = f"Uygun yeni dosya yolunu belirtin. Girdi uygun değil: {neden}"
        elif tur == "geri_alma":
            anahtar = "geri_alma"
            metin = f"{gorev['id']} yeniden denetimde kaldı. Geri alma komutunu inceleyin."
        elif tur == "inceleme":
            kabul_id = baglam["kabul_id"] = teshis["kabul_id"]
            anahtar = kabul_id
            metin = f"{gorev['id']} için {kabul_id} insan/duzenlemetör incelemesini yapın."
            komut = self._komut("yurut", "incele", self.calisma, gorev["id"], kabul_id,
                                "<inceleme notu>", "--sonuc", "<gecti|kaldi>")
        elif tur == "yetki":
            istek_id = (istek or {}).get("id")
            baglam["istek_id"] = istek_id
            anahtar = istek_id or "yetki"
            metin = metin or ((istek or {}).get("ayrinti") or "Görevin gerektirdiği yetkiyi inceleyin.")
            komut = self._komut("mimar", "izin", self.calisma, istek_id or "<istek_id>",
                                "<izin metni>", "--karar", "verildi")
        elif tur == "kabul_celiskisi":
            metin = metin or "Kabul ölçütü çelişkisini nasıl çözmek istersiniz?"
            komut = self._komut("mimar", "kabul-degistir", self.calisma, gorev["id"],
                                "--kabul-id", "<kabul_id>", "--beklenen", "<beklenen>",
                                "--onay-olay", "<onay_olay_id>", "<gerekçe>")
        elif tur == "cikti_konumu":
            anahtar = "cikti_konumu"
            metin = (f"{gorev['id']} codex-cloud ile çalıştırılmadan önce çıktı konumu belirlenmeli. "
                     "Görev yeniden planlansın mı?")
            baglam.update(secenekler=["çıktı konumunu belirleyip yeniden planla", "görevi beklet"],
                          risk="Çıktı konumu olmadan bulut işçisinin değişiklikleri teslim alınamaz.")
        elif tur == "uzlastir":
            kimlik = teshis.get("bulut_gorev_id") or "<bulut_gorev_id>"
            durum_komutu = teshis.get("uzlastirma_komutu") or f"codex cloud status {kimlik}"
            acma_komutu = self._komut("yurut", "uzlastir", self.calisma, gorev["id"],
                                      "--sonuc", "<uygulandi|uygulanmadi>")
            metin = metin or (f"{gorev['id']} yürütmesinin sonucu bilinmiyor. Bulut görevini kontrol edip "
                              "sonucu uzlaştırdıktan sonra görevi açmak ister misiniz?")
            komut = durum_komutu + " && " + acma_komutu
            baglam.update(secenekler=["bulut sonucunu doğrula ve görevi aç", "görevi beklet"],
                          risk="Uzlaştırmadan açmak aynı işi ikinci kez çalıştırabilir.")
        elif tur == "yukselt" and "mimar kehanet" in (komut or ""):
            metin = metin or (f"{gorev['id']} kehanet denetiminde durdu ({_kisa(neden, 120)}); işçi koşmadı. "
                              "Kehanet referansla yeniden üretilip görev açılsın mı, yoksa sözleşme mi gözden geçirilsin?")
        elif tur == "yukselt" and "yeniden-planla" in (komut or ""):
            metin = metin or (f"{gorev['id']} için {_kisa(neden, 120)}. Ek deneme bunu çözmez; "
                              "kök bütçe yeniden planlamada artırılsın mı (butce_artir)?")
        else:
            metin = metin or (f"{gorev['id']} kendi kendine ilerleyemiyor ({_kisa(neden, 120)}). "
                              "Ek deneme hakkı verilsin mi, yoksa görev yeniden mi ele alınsın?")
            komut = komut or self._komut("yurut", "ac", self.calisma, gorev["id"],
                                        "--ek-deneme", "1", neden)
        if tur == "orvant_kusuru":
            baglam["oneri"] = "Orvant düzeltmesi"
        elif tur == "yetki":
            baglam.update(secenekler=["verildi", "reddedildi"], risk=_kisa("; ".join(
                str((istek or {})[k]) for k in ("eylem", "kapsam", "ayrinti") if (istek or {}).get(k))) or None)
        elif tur == "yukselt" and "mimar kehanet" in (komut or ""):
            baglam.update(secenekler=["kehaneti referansla yeniden üret ve görevi aç",
                                      "sözleşme/kabul ölçütünü gözden geçir"],
                          risk="Aynı kehanetle ek deneme yine engellenir; yeni kehanet pozitif kontrolden geçmeli.")
        elif tur == "yukselt" and "--ek-deneme" in (komut or ""):
            baglam.update(secenekler=["ek deneme hakkı ver", "görevi yeniden planla"],
                          risk=f"Görevin deneme/token bütçesi artar (mevcut: {gorev['butce']}).")
        elif tur == "yukselt" and "yeniden-planla" in (komut or ""):
            baglam.update(secenekler=["kök bütçeyi yeniden planlamada artır (butce_artir)", "görevi bırak"],
                          risk=f"Kök token sınırı artar ({neden}).")
        elif tur == "geri_alma":
            baglam["risk"] = "kabul geri alınır; bağımlılar yeniden denetlenir"
        oneri = teshis.get("bakim_onerisi") or teshis.get("oneri")
        if oneri and not baglam.get("oneri"):
            baglam["oneri"] = _kisa(oneri)
        # Karar metnini kesme: revizyon karşılaştırması sonundaki değişikliği de görmeli.
        soru = self.sorular.taslak(gorev["id"], tur, anahtar,
                                  metin if tur == "karar" else _kisa(metin), neden, komut, **baglam)
        return self.sorular.baglamla(soru, self.yurutme._plan(), self.mimar.kararlar(), self.mimar.yetkiler())

    def _kok_butce_sorusu(self, gorev, ek=None):
        """Kök bütçe doluysa ek deneme işe yaramaz; artış yalnız yeniden planlamada (G-103, G-148)."""
        from orvant_op.butce import kok_butce_durumu
        butce = kok_butce_durumu(self.calisma, self.yurutme._plan(), gorev["id"])
        if butce["kalan"] > 0:
            return []
        neden = f"Kök token bütçesi doldu ({butce['harcanan']}/{butce['sinir']}, kök {butce['kok']})"
        if ek:
            neden = f"{ek}; {neden}"
        komut = self._komut("mimar", "yeniden-planla", self.calisma,
                            neden + "; butce_artir gerekli", "--gorev", gorev["id"])
        return [self._soru(gorev, "yukselt", neden, komut=komut)]

    def _kehanet_engeli_sorusu(self, gorev):
        """S3 kehanet denetimi engeli makbuz/teşhis bırakmaz; kullanıcıya yol gösteren soru (G-175)."""
        engeller = [e for e in satirlar(self.yurutme.kok / "engeller.jsonl") if e.get("gorev") == gorev["id"]]
        neden = engeller[-1]["neden"] if engeller else None
        if neden not in KEHANET_ENGELLERI:
            return []
        neden = f"S3 {neden}; işçi koşmadı"
        komut = (self._komut("mimar", "kehanet", self.calisma, "--gorev", gorev["id"], "--yeniden", "--referans")
                 + " && " + self._komut("yurut", "ac", self.calisma, gorev["id"], "--ek-deneme", "1",
                                        "Kehanet referansla yeniden üretildi"))
        return [self._soru(gorev, "yukselt", neden, komut=komut)]

    def _hak_var(self, gorev):
        from orvant_op.butce import kok_butce_durumu
        yollar = self.yurutme._makbuzlar(gorev)
        butce = kok_butce_durumu(self.calisma, self.yurutme._plan(), gorev["id"])
        return len(yollar) < gorev["butce"]["deneme"] and butce["kalan"] > 0

    def _teshis_eylemi(self, gorev, kayit, teshisler, uygulanan, plan):
        adim = kayit["eylem"]
        neden = kayit.get("gerekce") or "S4 teşhisi"
        if kayit.get("sinif") == "orvant_kusuru":
            istisna = kayit.get("istisna")
            if istisna and istisna not in neden:
                neden += "; " + istisna
            return {"adim": "soru", "gorev": gorev["id"], "neden": neden, "teshis": kayit,
                    "soru": self._soru(gorev, "orvant_kusuru", neden, teshis=kayit)}
        if adim == "yeniden_dene":
            ayni = lambda k: (k.get("gorev") == gorev["id"] and k.get("imza") == kayit.get("imza")
                              and k.get("kanit_ozeti") == kayit.get("kanit_ozeti"))
            tekrar = any(ayni(k) and k.get("eylem") == "yeniden_dene"
                         and teshis_kimligi(k) != teshis_kimligi(kayit) for k in teshisler)
            tekrar |= any(ayni(k) and k.get("uygulama") == "yeniden_dene" for k in uygulanan)
            if tekrar or not self._hak_var(gorev):
                adim = "yukselt"
                neden += "; yeni kanıt yok veya deneme/token hakkı doldu"
        elif adim == "yeniden_denetle":
            agac = self.yurutme._agac(self.yurutme._depo(plan), gorev["id"])
            zaman = datetime.fromisoformat(kayit["t"].replace("Z", "+00:00")).timestamp()
            if not agac.is_dir() or any(p.stat().st_mtime > zaman
                                       for p in self.yurutme._kapi_makbuzlari(gorev)):
                adim = "yukselt"
                neden += "; mevcut ağaç yok veya bu teşhisten sonra kapı zaten çalıştı"
        elif adim == "yeniden_planla":
            if any(k.get("teshis_ref", {}).get("makbuz") == kayit.get("makbuz")
                   for k in satirlar(self.calisma / "plan/yeniden_planlar.jsonl")):
                adim = "yukselt"
                neden += "; bu makbuz için yeniden planlama zaten yapıldı"
        eylem = {"adim": adim, "gorev": gorev["id"], "neden": neden, "teshis": kayit}
        if adim in ("girdi_bekle", "yetki_bekle", "yukselt", "uzlastir"):
            tur = {"girdi_bekle": "girdi", "yetki_bekle": "yetki", "yukselt": "yukselt",
                   "uzlastir": "uzlastir"}[adim]
            karar = None
            if kayit.get("sinif") == "urun_karari_eksik":
                karar = next((k for k in self.mimar.kararlar()
                              if k["id"] == kayit.get("karar_id")), None)
                if karar:
                    tur = "karar"
            if adim == "yukselt" and kayit.get("sinif") == "kabul_celiskisi":
                tur = "kabul_celiskisi"
            istek = next((y for y in plan["yetki_istekleri"]
                          if y["id"] in gorev["yetki_istek_ids"] and y["durum"] == "acik"), None)
            eylem.update(adim="soru", soru=self._soru(
                gorev, tur, neden, teshis=kayit, karar=karar, istek=istek))
        return eylem

    def _kehanet_gerekli(self, gorev):
        kimlik = gorev["id"]
        return (not kehanet_yolu(self.calisma, kimlik).exists()
                or not sozlesme_yolu(self.calisma, kimlik).exists()
                or kehanet_gecersiz_mi(self.calisma, kimlik))

    def tur_plani(self, *, tum_sorular=False):
        """Dosya, iz, model veya kapı yan etkisi olmadan bir turun aday eylemleri."""
        plan = self.yurutme._plan()
        kararlar = self.mimar.kararlar()
        yetkiler = self.mimar.yetkiler()
        teshisler = satirlar(self.calisma / "yurutme/teshis.jsonl")
        girdiler = {g["gorev"]: g for g in beklenen_girdiler(self.yurutme, plan)}
        uygulanan = satirlar(self.kok / "uygulanan.jsonl")
        islenen = {teshis_kimligi(k) for k in uygulanan}
        sonlar = {k["gorev"]: k for k in teshisler}
        kapanacak = self.sorular.kapanacaklar(plan, kararlar)
        yeniden = s4_kancasi.yeniden_degerlendirilecekler(self.yurutme)
        yeniden_ids = {a["gorev"] for a in yeniden}
        eylemler = [{"adim": "yeniden_degerlendir", "gorev": a["gorev"], "neden": a["gerekce"]}
                    for a in yeniden]
        eylemler += [{"adim": "soru_kapat", "gorev": s["gorev"],
                      "neden": s.get("kapanis_nedeni", "Bekleme koşulu kalktı"), "soru": s}
                     for s in kapanacak]
        eylemler += self._etki_eylemleri()
        isaretler = self.yurutme.denetim_isaretleri()
        karantina = self.yurutme.karantinalar()
        bekletilenler = self.yurutme._bekletilenler(plan)
        for gorev in plan["gorevler"]:
            isaret = isaretler.get(gorev["id"], {})
            if gorev["durum"] == "kabul" and isaret.get("durum") == "geri_alma_adayi":
                soru = self._soru(gorev, "geri_alma", f"Yeniden denetim makbuzu: {isaret['makbuz']}",
                    komut=isaret.get("oneri") or self._komut("yurut", "geri-al", self.calisma,
                        gorev["id"], "Yeniden denetim kapısı kaldı"))
                eylemler.append({"adim": "soru", "gorev": gorev["id"], "neden": soru["neden"], "soru": soru})
        envanter = self.calisma / "plan/envanter.json"
        if not envanter.exists() or time.time() - envanter.stat().st_mtime > 86400:
            eylemler.append({"adim": "envanter", "gorev": None, "neden": "Envanter yok veya 24 saatten eski"})
        for gorev in plan["gorevler"]:
            kimlik = gorev["id"]
            kayit = sonlar.get(kimlik)
            if kimlik in karantina:
                soru = self._soru(gorev, "karantina", karantina[kimlik]["neden"])
                eylemler.append({"adim": "soru", "gorev": kimlik, "neden": soru["neden"], "soru": soru})
                continue
            if kimlik in bekletilenler:
                continue
            if kimlik in yeniden_ids:
                # Eski teşhisin sorusunu yeni sürüm adayıyla aynı turda yeniden açma.
                continue
            ciktilar = self.yurutme._bekleme_ciktilari(plan, gorev)
            yeni_olay = (gorev["durum"] in ("girdi_bekliyor", "yetki_bekliyor")
                         and (self.yurutme._yeni_kullanici_olayi(gorev) is not None or bool(ciktilar)))
            serbest = {"adim": "serbest", "gorev": kimlik, "neden":
                       "İstenen bağımlı çıktı mevcut" if ciktilar else "Yeni kullanıcı olayı var"}
            if (gorev["durum"] in TESHIS_DURUMLARI and kayit
                    and (tum_sorular or teshis_kimligi(kayit) not in islenen)):
                eylem = self._teshis_eylemi(gorev, kayit, teshisler, uygulanan, plan)
                # Beklemeden sonra kullanıcı zaten cevap verdiyse aynı şeyi yeniden sorma; teşhis
                # serbest bırakmayla işlenmiş sayılır (sonraki başarısızlık yeni teşhis üretir).
                if yeni_olay and eylem["adim"] == "soru" and eylem["soru"]["tur"] in ("girdi", "yetki"):
                    serbest["teshis"] = kayit
                elif (eylem["adim"] != "soru" or gorev["durum"] in
                      TUR_DURUMLARI.get(eylem["soru"]["tur"], TESHIS_DURUMLARI)):
                    eylemler.append(eylem)
            if yeni_olay:
                eylemler.append(serbest)
            sorular = []
            if gorev["durum"] == "engelli":
                makbuzlar = self.yurutme._makbuzlar(gorev)
                if makbuzlar and json.loads(makbuzlar[-1].read_text(encoding="utf-8")).get("isci_zaman_asimi"):
                    # Kök bütçe doluyken ek deneme hemen yine engellenir (G-148).
                    sorular += (self._kok_butce_sorusu(gorev, "İşçi zaman aşımı")
                                or [self._soru(gorev, "yukselt", "İşçi zaman aşımı; ek deneme gerekli")])
                if not sorular and not any(e["gorev"] == kimlik and e["adim"] == "soru" for e in eylemler):
                    sorular += self._kok_butce_sorusu(gorev) or self._kehanet_engeli_sorusu(gorev)
            if (gorev["durum"] in ("engelli", "ret")
                    and any(s["gorev"] == kimlik and s.get("kapanis") == "tur_degisti" for s in kapanacak)
                    and not any(e["gorev"] == kimlik and e["adim"] == "soru" for e in eylemler)
                    and not sorular):
                sorular.append(self._soru(gorev, "yukselt", "Görev engelli; yeniden ele alınmalı"))
            if gorev["durum"] == "karar_bekliyor":
                for karar in kararlar:
                    if karar["id"] not in gorev["bekleyen_kararlar"] or karar.get("durum") == "cozuldu":
                        continue
                    if karar.get("sahip") == "kullanici":
                        sorular.append(self._soru(gorev, "karar", "Kullanıcı kararı bekleniyor", karar=karar))
                    elif karar.get("sahip") == "arastirilabilir" or karar.get("arastirilabilir"):
                        eylemler.append({"adim": "not", "gorev": kimlik,
                                         "neden": f"Araştırılabilir karar: {karar['id']}"})
            if gorev["durum"] == "yetki_bekliyor":
                sorular += [self._soru(gorev, "yetki", y["gerekce"], istek=y) for y in yetkiler
                            if y["id"] in gorev["yetki_istek_ids"]]
            if gorev["durum"] == "inceleme_bekliyor":
                sorular += [self._soru(gorev, "inceleme", "İnsan incelemesi bekleniyor",
                                       teshis={"kabul_id": k["id"]})
                            for k in gorev["kabul"] if k["tur"] == "insan_incelemesi"]
            if (gorev["durum"] == "girdi_bekliyor" and not yeni_olay
                    and (not kayit or kayit.get("eylem") != "girdi_bekle")):
                sorular.append(self._soru(gorev, "girdi", girdiler[kimlik]["beklenen"]))
            eylemler += [{"adim": "soru", "gorev": kimlik, "neden": s["neden"], "soru": s} for s in sorular]
        # Kuru planda da yeniden hazırlama sonrası beklenen adımları göster.
        hazirlanacak = {e["gorev"] for e in eylemler if e["adim"] in ("yeniden_dene", "serbest")}
        hazirlanacak.update(a["gorev"] for a in yeniden if a["eylem"] == "yeniden_dene")
        adaylar = [g for g in plan["gorevler"] if g["id"] not in bekletilenler
                   and (g["durum"] == "hazir" or g["id"] in hazirlanacak)]
        try:
            yurutucu_turu = ayarlar.yurutucu_turu(self.calisma)
        except ayarlar.YurutucuSecimHatasi:
            # Ön kontrol yürütücü seçimi değildir. Seçim yoksa/belirsizse yerel
            # akışın önceki davranışını koru; asıl yürütme katmanı gerekirse
            # kendi açık ayar hatasını üretir.
            yurutucu_turu = None
        if yurutucu_turu == "codex-cloud":
            konumsuz = [g for g in adaylar if not g["yazilabilir"]]
            eylemler += [{"adim": "engel", "gorev": g["id"],
                          "sinif": "cikti_konumu_eksik",
                          "neden": "çıktı konumu/yazılabilir yol eksik; codex-cloud görevi başlatılmadı"}
                         for g in konumsuz]
            eylemler += [{"adim": "not", "gorev": g["id"],
                          "neden": ("çıktı konumu/yazılabilir yol eksik; mimar yeniden-planla "
                                    "veya karar ile konum belirleyin")}
                         for g in konumsuz]
            for gorev in konumsuz:
                neden = "çıktı konumu/yazılabilir yol eksik; codex-cloud görevi başlatılmadı"
                komut = self._komut("mimar", "yeniden-planla", self.calisma,
                                    f"{gorev['id']} çıktı konumu: <yazılabilir yol>",
                                    "--gorev", gorev["id"])
                soru = self._soru(gorev, "cikti_konumu", neden, komut=komut)
                eylemler.append({"adim": "soru", "gorev": gorev["id"],
                                 "neden": neden, "soru": soru})
            adaylar = [g for g in adaylar if g["yazilabilir"]]
        kabul_gecersizler = [g for g in plan["gorevler"] if g["durum"] == "kabul"
                            and g["id"] not in bekletilenler and kehanet_gecersiz_mi(self.calisma, g["id"])]
        for gorev in [*adaylar, *kabul_gecersizler]:
            if self._kehanet_gerekli(gorev) and gorev["id"] not in self.kehanet_denendi:
                eylemler.append({"adim": "kehanet", "gorev": gorev["id"], "neden": "Kehanet/sözleşme eksik veya geçersiz"})
        eylemler += [{"adim": "kosu", "gorev": g["id"], "neden": "Hazır görevi bağımsız kapıyla yürüt"} for g in adaylar]
        acik_sorular = {s["kok"]: s for s in self.sorular.acik()
                        if s["id"] not in {k["id"] for k in kapanacak}}
        kokler, sonuc = {}, []
        for e in eylemler:
            if e["adim"] != "soru":
                sonuc.append(e)
                continue
            kok = e["soru"]["kok"]
            if kok in kokler:
                kokler[kok]["soru"] = birlestir(kokler[kok]["soru"], e["soru"])
            else:
                kokler[kok] = e
        for kok, e in kokler.items():
            eski = next((s for s in acik_sorular.values() if kok_eslesir(s, e["soru"])), None)
            if eski:
                e["soru"] = birlestir(eski, e["soru"])
            e["soru"] = self.sorular.etkileri_guncelle([e["soru"]])[0]
            if not tum_sorular and eski and e["soru"] == eski:
                continue
            sonuc.append(e)
        return sonuc

    def _etki_eylemleri(self):
        adaylar = {a["gorev"]: "Kehanet değişti" for a in self.yurutme.kehanet_degisenler()}
        adaylar.update({g: "Etki kümesi yeniden denetim bekliyor"
                       for g in self.yurutme.etki_denetimi_gerekenler()})
        karantina = self.yurutme.karantinalar()
        return [{"adim": "etki_denetimi", "gorev": g, "neden": neden} for g, neden in adaylar.items()
                if g not in karantina]

    def _zaman_asimi(self, kimlik, adim, basla):
        sinir = self.kehanet_zaman_asimi if adim == "kehanet" else self.yurut_zaman_asimi
        veri = {"adim": adim, "sure_sn": time.monotonic() - basla, "sinir_sn": sinir}
        self.yurutme._olay("operator_zaman_asimi", kimlik, **veri)
        self.yurutme._iz("baslatma_izleme", "Operatör zaman aşımı", gorev=kimlik, sonuc="hata", ham=veri)

    def _soru_yaz(self, soru, *, iz=True):
        eski = next((s for s in self.sorular.acik() if kok_eslesir(s, soru)), None)
        kayit, degisti = self.sorular.ekle(soru)
        if degisti and iz:
            if eski and kayit["id"] != eski["id"]:
                kapali = next(s for s in self.sorular.oku() if s["id"] == eski["id"])
                self._soru_kapanis_izi(kapali)
            self._iz(soru_iz_turu(kayit),
                     "soru birleşti" if eski and kayit["id"] == eski["id"] else kayit["soru"], kayit["gorev"])
        return kayit

    def _soru_kapat(self, soru, *, iz=True, **alanlar):
        kayit = self.sorular.kapat(soru["id"], **alanlar)
        if iz:
            self._soru_kapanis_izi(kayit)
        return kayit

    def _soru_kapanis_izi(self, kayit):
        ham = {k: kayit[k] for k in ("kok", "karar_kimligi", "acilis_t", "ilk_acilis_t", "kapanis_t", "sure_sn",
                                    "engellenen_gorevler", "gorev_bekleme_sn", "kapanis")}
        ham["soru_id"] = kayit["id"]
        self._iz(soru_iz_turu(kayit), "Soru kapandı: " + kayit["kapanis"], kayit["gorev"], ham=ham)

    def soru_esitle(self):
        """Yalnız kuyruk dosyaları: teşhis makbuzu, iz ve plan bile yazılmaz."""
        for e in self.tur_plani():
            if e["adim"] == "soru":
                self._soru_yaz(e["soru"], iz=False)
            elif e["adim"] == "soru_kapat":
                self._soru_kapat(e["soru"], iz=False, kapanis=e["soru"].get("kapanis", "durum_degisti"),
                                  kapanis_nedeni=e["soru"].get("kapanis_nedeni"))
        self.sorular.yaz(self.sorular.oku())
        return self.sorular.oku()

    def _soru_onizleme(self, eylemler):
        sorular = {s["kok"]: s for s in self.sorular.acik()}
        for e in eylemler:
            if e["adim"] == "soru_kapat":
                sorular.pop(e["soru"]["kok"], None)
            elif e["adim"] == "soru":
                s = e["soru"]
                eski = next((k for k, v in sorular.items() if kok_eslesir(v, s)), None)
                sorular[s["kok"]] = birlestir(sorular.pop(eski), s) if eski else s
        return self.sorular.etkileri_guncelle(sorular.values())

    def uygula(self, eylem):
        """Tek eylemi uygular; teşhis ve bütçe koşullarını uygulamadan önce yeniden okur."""
        adim, kimlik = eylem["adim"], eylem["gorev"]
        teshis = eylem.get("teshis")
        if teshis:
            # Eski tur planı ikinci kez uygulanamaz; aradaki S4 değişimleri de korunur.
            guncel = next((e for e in self.tur_plani() if e.get("teshis")
                           and teshis_kimligi(e["teshis"]) == teshis_kimligi(teshis)), None)
            if guncel is None:
                return {"atlandi": "Teşhis artık uygulanabilir değil"}
            eylem, adim = guncel, guncel["adim"]
        if adim in ("kehanet", "yeniden_planla", "kosu"):
            self._kota()
        if teshis:
            self._iz("ariza_teshisi", f"{teshis['eylem']} → {adim}: {eylem['neden']}", kimlik)
        basla = time.monotonic()
        onceki_makbuzlar = set((self.calisma / "yurutme/makbuzlar").glob("*.json")) if adim == "kosu" else set()
        try:
            sonuc = self._uygula_adim(eylem)
            if adim == "kosu":
                yeni = set((self.calisma / "yurutme/makbuzlar").glob("*.json")) - onceki_makbuzlar
                for yol in sorted(yeni):
                    makbuz = json.loads(yol.read_text(encoding="utf-8"))
                    if makbuz.get("isci_zaman_asimi"):
                        kimlik = makbuz.get("gorev", kimlik)
                        raise YurutucuZamanAsimi(f"İşçi zaman aşımı: {yol}")
        except (YurutucuZamanAsimi, subprocess.TimeoutExpired):
            self._zaman_asimi(kimlik, adim, basla)
            raise YurutucuZamanAsimi(f"{kimlik} · {adim}: zaman aşımı")
        except KotaSiniri:
            raise
        except Exception as exc:
            if not teshis and adim != "kehanet":
                raise
            self._iz("ariza_teshisi", str(exc), kimlik, sonuc="hata")
            if adim == "kehanet":
                # Yarım üretimden kalan dosyalar başarılı doğrulama sayılamaz.
                plan = self.yurutme._plan()
                gorev = next(g for g in plan["gorevler"] if g["id"] == kimlik)
                if gorev["durum"] == "hazir":
                    gorev["durum"] = "engelli"
                    self.yurutme._kaydet_plan(plan)
                    self.yurutme._olay("operator_kehanet_hatasi", kimlik, neden=str(exc))
            komut = self._komut("mimar", "kehanet", self.calisma, "--gorev", kimlik, "--yeniden") if adim == "kehanet" else None
            self._soru_yaz(self._soru(self._gorev(kimlik), "yukselt", str(exc), teshis=teshis, komut=komut))
            sonuc = {"hata": str(exc)}
        if teshis:
            kayitlar = satirlar(self.kok / "uygulanan.jsonl")
            kayitlar.append({**teshis, "uygulama": adim, "islem_t": simdi(), "sonuc": sonuc})
            atomik_yaz(self.kok / "uygulanan.jsonl", "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in kayitlar))
        return sonuc

    def _uygula_adim(self, eylem):
        adim, kimlik = eylem["adim"], eylem["gorev"]
        if (adim in ("kehanet", "yeniden_dene", "yeniden_planla", "yeniden_denetle", "kosu", "serbest")
                and kimlik in self.yurutme._bekletilenler(self.yurutme._plan())):
            return {"atlandi": "Görev bekletiliyor"}
        if adim == "etki_denetimi":
            self.yurutme.kehanet_degisimi_isaretle(kimlik)
            if kimlik not in self.yurutme.etki_denetimi_gerekenler():
                return {"atlandi": "Yeniden denetim gerekmiyor"}
            return self.yurutme.yeniden_denetle(kimlik)
        if adim == "yeniden_degerlendir":
            return s4_kancasi.yeniden_degerlendir(self.yurutme, kimlik)
        if adim == "envanter":
            self._iz("arastirma", eylem["neden"])
            return self.mimar.envanter(**self.envanter_ayarlari)
        if adim == "soru":
            return self._soru_yaz(eylem["soru"])
        if adim == "soru_kapat":
            return self._soru_kapat(eylem["soru"], kapanis=eylem["soru"].get("kapanis", "durum_degisti"),
                                    kapanis_nedeni=eylem["soru"].get("kapanis_nedeni"))
        elif adim == "yeniden_dene":
            plan = self.yurutme._plan()
            gorev = next(g for g in plan["gorevler"] if g["id"] == kimlik)
            gorev["durum"] = "hazir"
            self.yurutme._bagimlilari_ac(plan)
            self.yurutme._kaydet_plan(plan)
            self.yurutme._olay("operator_yeniden_hazirladi", kimlik, gerekce=eylem["neden"])
            self._iz("yeniden_is_kapsami", eylem["neden"], kimlik)
            return {"durum": gorev["durum"]}
        elif adim == "yeniden_denetle":
            self._iz("dogrulama", "Teşhis sonrası bağımsız kapı", kimlik)
            sonuc = self.yurutme.kapi(kimlik)
            if sonuc.get("hatalar"):
                self._soru_yaz(self._soru(self._gorev(kimlik), "yukselt", "; ".join(sonuc["hatalar"]),
                                         teshis=eylem.get("teshis")))
            return sonuc
        elif adim == "yeniden_planla":
            self._iz("yeniden_is_kapsami", eylem["neden"], kimlik)
            return self.mimar.yeniden_planla(eylem["neden"], gorev=kimlik,
                                            teshis_dosyasi=self.calisma / "yurutme/teshis.jsonl")
        elif adim == "serbest":
            self._iz("yeniden_is_kapsami", eylem["neden"], kimlik)
            return self.yurutme.serbest(kimlik)
        elif adim == "kehanet":
            gorev = self._gorev(kimlik)
            if kimlik in self.kehanet_denendi or not (gorev["durum"] == "hazir" or
                    (gorev["durum"] == "kabul" and kehanet_gecersiz_mi(self.calisma, kimlik))):
                return {"atlandi": "Kehanet denendi veya görev hazır değil"}
            self.kehanet_denendi.add(kimlik)
            self._iz("dogrulama", eylem["neden"], kimlik)
            sonuc = kehanet_hazirla(self.calisma, kimlik,
                                    yeniden=kehanet_yolu(self.calisma, kimlik).exists(),
                                    yurutucu=self.kehanet_yurutucu, iz_yolu=self.iz_yolu,
                                    zaman_asimi=self.kehanet_zaman_asimi)
            if any(s["zayiflik_denetimi"] in ("zayif", "gecersiz") for s in sonuc):
                self._soru_yaz(self._soru(self._gorev(kimlik), "yukselt", "Kehanet zayıf veya geçersiz",
                    komut=self._komut("mimar", "kehanet", self.calisma, "--gorev", kimlik, "--yeniden")))
            return sonuc
        elif adim == "kosu":
            self._iz("baslatma_izleme", "İşçi koşusu başlatma kararı", kimlik)
            return self.yurutme.yurut(en_fazla=1)
        elif adim == "engel":
            self.yurutme._olay("operator_on_kontrol_engeli", kimlik,
                               neden=eylem["neden"], sinif=eylem.get("sinif"))
            return {"durum": "bu_tur_atlandi", "sinif": eylem.get("sinif"), "neden": eylem["neden"]}
        elif adim == "not":
            if eylem["neden"] not in self.uyarilar:
                self.uyarilar.append(eylem["neden"])
            self._iz("baslatma_izleme", eylem["neden"], kimlik)
        else:
            raise ValueError(f"Bilinmeyen operatör adımı: {adim}")

    def _imza(self):
        plan = self.yurutme._plan()
        return (tuple((g["id"], g["durum"]) for g in plan["gorevler"]),
                sum(len(self.yurutme._makbuzlar(g)) + len(self.yurutme._kapi_makbuzlari(g)) for g in plan["gorevler"]),
                len(satirlar(self.calisma / "yurutme/teshis.jsonl")),
                tuple(sorted(self.yurutme._cozulmus_kararlar())),
                json.dumps(self.yurutme.denetim_isaretleri(), sort_keys=True),
                tuple(sorted(s["id"] for s in self.sorular.acik())))

    def _inceleme_freni_kaydi(self):
        """İnceleme freninin açılış/kapanışını kalıcı ve dönem başına tekil kaydet."""
        plan = self.yurutme._plan()
        bekletilen = self.yurutme._inceleme_freni(plan)
        olaylar = satirlar(self.calisma / "yurutme/olaylar.jsonl")
        gecisler = [o for o in olaylar if o.get("tur") in
                    ("uretim_durdu_inceleme", "uretim_freni_kalkti_inceleme")]
        etkin_kayitli = bool(gecisler and gecisler[-1]["tur"] == "uretim_durdu_inceleme")
        if bekletilen:
            # Aynı Yurutme örneğinin izinli düzeltme koşusu bu dönemi yeniden duyurmasın.
            self.yurutme._inceleme_duyurusu = True
            if not etkin_kayitli:
                sinir = ayarlar.inceleme_bekleyen_siniri(self.calisma)
                bekleyen = [g["id"] for g in plan["gorevler"]
                            if g["durum"] == "inceleme_bekliyor"]
                self.yurutme._olay("uretim_durdu_inceleme", None, bekleyen=bekleyen,
                                   sinir=sinir, bekletilen=list(bekletilen))
            return {"bekleyen": [g["id"] for g in plan["gorevler"]
                                  if g["durum"] == "inceleme_bekliyor"],
                    "sinir": ayarlar.inceleme_bekleyen_siniri(self.calisma),
                    "bekletilen": list(bekletilen)}
        self.yurutme._inceleme_duyurusu = None
        if etkin_kayitli:
            self.yurutme._olay("uretim_freni_kalkti_inceleme", None)
        return None

    def surdur(self, *, en_fazla_tur=5, tur_basina_kosu=3, kuru=False):
        from orvant_op import proje
        if proje.load(self.calisma):
            raise ValueError("project-bound sessions use orvant proje surdur; paid runs require orvant proje surdur --execute")
        if en_fazla_tur < 1 or tur_basina_kosu < 1:
            raise ValueError("Tur ve koşu sınırları pozitif olmalı")
        self.kehanet_denendi = set()
        self.uyarilar = []
        plan = self.yurutme._plan()
        checkpointler = [g for g in plan["gorevler"] if g["durum"] == "kota_bekleniyor"]
        if checkpointler:
            for gorev in checkpointler:
                gorev["durum"] = "hazir"
            if not kuru:
                self.yurutme._kaydet_plan(plan)
        if kuru:
            eylemler = self.tur_plani()
            return {"kuru": True, "eylemler": eylemler, "kota": kota_oku(self.oturum_dizini),
                    "kota_esigi": self.kota_esigi, "soru_onizleme": self._soru_onizleme(eylemler)}
        baslangic = simdi()
        turlar = []
        bitis = "tur_siniri"
        for numara in range(1, en_fazla_tur + 1):
            once = self._imza()
            tur = {"tur": numara, "eylemler": [], "durum": None}
            turlar.append(tur)
            self._iz("baslatma_izleme", f"Tur {numara} başladı")
            inceleme_freni = self._inceleme_freni_kaydi()

            def calistir(eylem):
                kayit = {**eylem}
                tur["eylemler"].append(kayit)
                try:
                    kayit["sonuc"] = self.uygula(eylem)
                except KotaSiniri:
                    kayit["sonuc"] = {"atlandi": "Kota eşiği nedeniyle başlatılmadı"}
                    raise
                except Exception as exc:
                    kayit["sonuc"] = {"hata": str(exc)}
                    raise
                return kayit["sonuc"]

            try:
                for kayit in s4_kancasi.yeniden_degerlendir(self.yurutme):
                    tur["eylemler"].append({"adim": "yeniden_degerlendir", "gorev": kayit["gorev"],
                                           "neden": kayit["gerekce"], "sonuc": kayit})
                    self._iz("ariza_teshisi", kayit["gerekce"], kayit["gorev"])
            except Exception as exc:
                tur["hata"] = f"Yeniden değerlendirme hatası: {type(exc).__name__}: {exc}"
                self.uyarilar.append(tur["hata"])

            try:
                for e in self._etki_eylemleri():
                    calistir(e)
                # S4 ve kullanıcı olayları uygulandıktan sonra hazır görevleri yeniden oku.
                for e in self.tur_plani():
                    if e["adim"] not in ("kehanet", "kosu", "soru", "etki_denetimi", "yeniden_degerlendir"):
                        calistir(e)
                for _ in range(tur_basina_kosu):
                    for e in self.tur_plani():
                        if e["adim"] == "kehanet":
                            calistir(e)
                    adaylar = [e for e in self.tur_plani() if e["adim"] == "kosu"]
                    if not adaylar:
                        break
                    # Başarısız üretimin ardından eksik kehanetle işçi başlatma.
                    if self._kehanet_gerekli(self._gorev(adaylar[0]["gorev"])):
                        break
                    calistir(adaylar[0])
                    inceleme_freni = self._inceleme_freni_kaydi()
                    if self._gorev(adaylar[0]["gorev"])["durum"] == "kota_bekleniyor":
                        bitis = "kota_bekleniyor"
                        break
                self._kota()
            except YurutucuZamanAsimi as exc:
                bitis = "zaman_asimi"
                tur["hata"] = str(exc)
            except KotaSiniri:
                bitis = "kota"
            except Exception as exc:
                tur["hata"] = str(exc)
                self._iz("baslatma_izleme", f"Tur hatası: {exc}", sonuc="hata")
            if bitis == "zaman_asimi":
                inceleme_freni = self._inceleme_freni_kaydi()
                for e in self.tur_plani():
                    if e["adim"] == "soru":
                        calistir(e)
                tur["durum"] = dict(self._imza()[0])
                break
            # Son izinli koşuda doğan beklemeleri de aynı rapora al; yeni model
            # adımları gelecek tura kalır. Kota, kullanıcı sorusunu gizlemez.
            for e in self._etki_eylemleri():
                calistir(e)
            for e in self.tur_plani():
                if e["adim"] == "soru":
                    calistir(e)
            inceleme_freni = self._inceleme_freni_kaydi()
            for s in self.sorular.kapanacaklar(self.yurutme._plan(), self.mimar.kararlar()):
                calistir({"adim": "soru_kapat", "gorev": s["gorev"],
                          "neden": s.get("kapanis_nedeni", "Bekleme koşulu kalktı"), "soru": s})
            sonra = self._imza()
            tur["durum"] = dict(sonra[0])
            if (all(d == "kabul" for _, d in sonra[0]) and not self.yurutme.denetim_isaretleri()
                    and not any(kehanet_gecersiz_mi(self.calisma, g) for g, _ in sonra[0])):
                bitis = "tamamlandi"
            elif bitis not in ("kota", "kota_bekleniyor") and (sonra == once or not tur["eylemler"] or
                                        (sonra[:-1] == once[:-1] and not any(
                                            e["adim"] in ("kosu", "yeniden_degerlendir", "yeniden_dene", "yeniden_denetle", "etki_denetimi", "yeniden_planla", "serbest")
                                            for e in tur["eylemler"]))):
                acik = self.sorular.acik()
                bitis = ("orvant_duzeltmesi_bekleniyor" if acik and all(
                    s["tur"] == "orvant_kusuru" and s.get("bilgi") for s in acik) else
                    "kullanici_bekleniyor" if acik else "ilerleme_yok")
            if bitis != "tur_siniri":
                break
        if any(s["tur"] == "karantina" for s in self.sorular.acik()):
            bitis = "kullanici_bekleniyor"
        self._iz("baslatma_izleme", f"Sürdürme bitti: {bitis}")
        sonuc = {"bitis_nedeni": bitis, "turlar": turlar, "gorevler": dict(self._imza()[0]),
                 "acik_sorular": self.sorular.acik(), "kota": self.son_kota, "uyarilar": self.uyarilar,
                 "inceleme_freni": self._inceleme_freni_kaydi(),
                 "kapanan_sorular": [s for s in self.sorular.oku() if s.get("kapanis_t") and s["kapanis_t"] >= baslangic]}
        if bitis == "kota_bekleniyor":
            sonuc.update(durum="kota_bekleniyor",
                         sonraki_adim=f"kota yenilenince orvant operator surdur {self.calisma}")
        atomik_yaz(self.kok / "rapor.md", rapor_metni(sonuc))
        self._iz("raporlama", "Operatör raporu yazıldı")
        return sonuc

    def cevapla(self, soru_id, cevap):
        sorular = {s["id"]: s for s in self.sorular.oku()}
        soru = sorular.get(soru_id)
        if soru is None:
            raise ValueError("Bilinmeyen veya kapalı soru")
        if soru["durum"] != "acik":
            if soru.get("kapanis") not in ("yerine_gecti", "gecersizlesti"):
                raise ValueError("Bilinmeyen veya kapalı soru")
            yeni, gorulen = soru, set()
            while yeni and yeni["durum"] != "acik" and yeni["id"] not in gorulen:
                gorulen.add(yeni["id"])
                yeni = sorular.get(yeni.get("yerine"))
            if yeni and yeni["durum"] != "acik":
                yeni = None
            return self._eski_soru_sonucu(yeni)
        if soru["tur"] == "karantina":
            return {"cikis": 2, "mesaj": "Karantina yalnız hazır komutla kullanıcı tarafından kaldırılır"}
        if soru["tur"] == "orvant_kusuru":
            return {"cikis": 2, "mesaj": "Bu kayıt bilgi amaçlıdır; Orvant düzeltme sürümü bekleniyor."}
        plan = self.yurutme._plan()
        gecerli, _ = gecerlilik_denetle(soru, plan, self.mimar.kararlar(), self.mimar.yetkiler())
        # Açık kaydın tekilleştirmeyle gizlenmediği güncel soru görünümü.
        adaylar = [e["soru"] for e in self.tur_plani(tum_sorular=True) if e["adim"] == "soru"]
        yeni = next((s for s in adaylar if kok_eslesir(soru, s)), None)
        ayni = yeni and oz_ayni(soru, yeni)
        # Revizyon artmadan değişen karar metni de eski cevapla uygulanamaz.
        if (not gecerli or yeni is not None) and not ayni:
            if yeni is None:
                yeni = next((s for s in adaylar if set(s["dogrudan_gorevler"]) & set(soru["dogrudan_gorevler"]) and
                             s["tur"] == soru["tur"]), None)
            self._soru_kapat(soru, kapanis="gecersizlesti", yerine=yeni["id"] if yeni else None)
            if yeni:
                yeni = self._soru_yaz(yeni)
            return self._eski_soru_sonucu(yeni)
        if ayni:
            soru = self._soru_yaz(yeni)
        if soru["tur"] not in ("karar", "girdi"):
            return {"cikis": 2, "mesaj": "Bu komutu siz çalıştırın; soru açık bırakıldı.",
                    "hazir_komut": soru["hazir_komut"]}
        if not cevap.strip():
            raise ValueError("Cevap boş olamaz")
        karar_id = soru.get("karar_id") or soru.get("girdi_karar_id")
        if karar_id:
            self.mimar.karar(karar_id, cevap)
        else:
            durumlar = {g["id"]: g["durum"] for g in self.yurutme._plan()["gorevler"]}
            for kimlik in soru["dogrudan_gorevler"]:
                if durumlar.get(kimlik) == "girdi_bekliyor":
                    self.mimar.girdi(soru["baslik"], cevap, gorev=kimlik)
        self._iz("hedef_netlestirme", "Kullanıcının cevabı uygulandı", soru["gorev"])
        uyarilar = []
        for kimlik in soru["dogrudan_gorevler"]:
            durumlar = {g["id"]: g["durum"] for g in self.yurutme._plan()["gorevler"]}
            if durumlar.get(kimlik) in ("girdi_bekliyor", "yetki_bekliyor"):
                try:
                    self.yurutme.serbest(kimlik)
                    self._iz("yeniden_is_kapsami", "Cevap sonrası görev serbest bırakıldı", kimlik)
                except ValueError as exc:
                    if "yeni girdi yok" not in str(exc):
                        raise
                    uyarilar.append(str(exc))
        self._soru_kapat(soru, cevap=cevap)
        return {"cikis": 0, "mesaj": "Cevap kaydedildi.", "uyari": "; ".join(uyarilar) or None}

    @staticmethod
    def _eski_soru_sonucu(yeni):
        return {"cikis": 3,
                "mesaj": "Soru artık geçerli değil; güncel soru: " + (yeni["id"] if yeni else "yok"),
                "yeni_soru": yeni, "hazir_komut": yeni.get("hazir_komut") if yeni else None}


def rapor_metni(sonuc):
    if sonuc.get("kuru"):
        satir = ["Kuru tur planı:"]
        for bilgi in (False, True):
            grup = [e for e in sonuc["eylemler"] if (
                e["adim"] == "soru" and e["soru"]["tur"] == "orvant_kusuru") == bilgi]
            if bilgi and grup:
                satir += ["", "## Bilgi: Orvant düzeltmesi bekleniyor", ""]
            for e in grup:
                satir.append(f"- {e['adim']} · {e['gorev'] or 'çalışma'}: {e['neden']}")
                soru = e.get("soru")
                if soru:
                    satir.append(f"  {soru['soru']}")
                    if soru.get("kullanici_eylemi"):
                        satir.append(f"  {soru['kullanici_eylemi']}")
                    if soru.get("hazir_komut"):
                        satir.append(f"  {soru['hazir_komut']}")
        if "kota" in sonuc:
            esik = "kapalı" if sonuc["kota_esigi"] is None else sonuc["kota_esigi"]
            satir.append(f"Kota: {sonuc['kota']['used_percent']} (eşik {esik})")
        satir.extend(["", "## Soru paketleri (önizleme)", "",
                      paket_metni(sonuc.get("soru_onizleme", []))])
        return "\n".join(satir)
    satir = ["# Operatör raporu", ""]
    if sonuc.get("inceleme_freni"):
        fren = sonuc["inceleme_freni"]
        satir += ["## İnceleme sınırı", "",
                  f"- Bekleyen: {', '.join(fren['bekleyen'])}; sınır: {fren['sinir']}; "
                  f"bekletilen: {', '.join(fren['bekletilen'])}", ""]
    for tur in sonuc["turlar"]:
        satir += [f"## Tur {tur['tur']}", ""]
        for e in tur["eylemler"]:
            satir.append(f"- {e['adim']} · {e['gorev'] or 'çalışma'}: {e['neden']}")
            if e["adim"] == "etki_denetimi" and isinstance(e.get("sonuc"), dict):
                satir.append(f"  Sonuç: {e['sonuc'].get('sonuc', 'atlandı')} · {e['sonuc'].get('makbuz', '')}")
            if isinstance(e.get("sonuc"), dict) and e["sonuc"].get("hata"):
                satir.append(f"  Hata: {e['sonuc']['hata']}")
            if isinstance(e.get("sonuc"), dict) and e["sonuc"].get("atlandi"):
                satir.append(f"  Atlandı: {e['sonuc']['atlandi']}")
        if tur.get("hata"):
            satir.append(f"Hata: {tur['hata']}")
        satir += [f"Durum: {json.dumps(tur['durum'], ensure_ascii=False)}", ""]
    satir += ["## Bekleyen işler", ""]
    satir += [f"- {g}: {d}" for g, d in sonuc["gorevler"].items()]
    satir += ["", "## Kullanıcıdan istenenler", "", paket_metni(sonuc["acik_sorular"]),
              "## Soru ölçümleri", ""]
    for s in sonuc["acik_sorular"]:
        sn, bekleme = olcum(s)
        satir.append(f"- Açık {s['id']}: {sn:.0f} sn; engellenen görev: {len(s['engellenen_gorevler'])}; "
                     f"görev-bekleme toplamı: {sum(bekleme.values()):.0f} sn")
    for s in sonuc.get("kapanan_sorular", []):
        satir.append(f"- Bu koşuda kapandı {s['id']}: {s['acilis_t']} → {s['kapanis_t']}; "
                     f"{s['sure_sn']:.0f} sn; görev-bekleme: {json.dumps(s['gorev_bekleme_sn'], ensure_ascii=False)}")
    satir += ["", f"Bitiş nedeni: {sonuc['bitis_nedeni']}",
              f"Kota: {sonuc['kota']['used_percent']}", *sonuc["uyarilar"], ""]
    return "\n".join(satir)
