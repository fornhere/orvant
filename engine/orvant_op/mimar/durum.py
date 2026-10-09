"""Plan kalıcılığı, yerel depo kapısı ve yetki kararları."""
import copy
import hashlib
import inspect
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from orvant_op.iz import kaydet
from orvant_op.karsilama.roller import veri_dogrula
from .kehanet import kehanet_yolu, kabul_degisiklikleri
from .dogrulama import (dogrula, durumlari_hesapla, kabul_bagimliliklari,
                       kabul_bagimliliklarini_duzelt)
from . import arac_yetki, girdi_bagi
from .roller import plan_cikar, kaynak_denetle, iddia_bulgulari
from .kapsam import kapsam_matrisi
from orvant_op.karsilama.kaynaklar import depo_kaynaklari
from .yeniden_planlama import rol_cagir as plan_duzelt_cagir, uygula as plan_duzelt_uygula
from orvant_op.butce import agir_hesap, butce_tabani, ilgili_envanter
from orvant_op.yer_tutucu import denetle as yer_tutucu_denetle


def _oku(yol):
    return json.loads(Path(yol).read_text(encoding="utf-8"))


def _yaz(yol, veri):
    yol = Path(yol)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=yol.parent, delete=False) as fh:
        json.dump(veri, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        gecici = fh.name
    os.replace(gecici, yol)


def _git(*args, cwd):
    sonuc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if sonuc.returncode:
        raise ValueError(f"git {' '.join(args)}: {(sonuc.stderr or sonuc.stdout).strip()}")
    return sonuc.stdout.strip()


def _depo_kontrol(yol, *, calisma=None):
    yol = Path(yol).expanduser().resolve()
    if yol.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("depo yolu Orvant deposunun içinde olamaz")
    if calisma is not None and yol.is_relative_to(Path(calisma).resolve()):
        raise ValueError("depo yolu çalışma dizininin içinde olamaz")
    if yol.exists():
        if not yol.is_dir():
            raise ValueError("depo yolu dizin değil")
        try:
            kok = Path(_git("rev-parse", "--show-toplevel", cwd=yol)).resolve()
        except ValueError as exc:
            raise ValueError("mevcut dizin bir git deposu değil") from exc
        if kok != yol:
            raise ValueError("depo yolu başka bir git deposunun içinde; git kökü değil")
        # Salt denetimde git'in indeks stat önbelleğini yenilemesini de engelle.
        if _git("--no-optional-locks", "status", "--porcelain", "--untracked-files=all", cwd=yol):
            raise ValueError("mevcut depo kirli")
        return yol, False
    ust = yol.parent
    while not ust.exists():
        ust = ust.parent
    if not ust.is_dir():
        raise ValueError("depo üst yolu dizin değil")
    proc = subprocess.run(["git", "-C", str(ust), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        raise ValueError("depo yolu başka bir git deposunun içinde")
    if not os.access(ust, os.W_OK):
        raise ValueError(f"depo üst dizin yazılamıyor: {ust}")
    if not yol.parent.is_dir():
        raise ValueError("depo üst dizini bulunamadı")
    return yol, True


def izin_yolu_dogrula(yol, depo=None):
    """Dış yazma iznini ev altındaki dar, güvenli bir yola sınırlar."""
    ham = Path(yol)
    ev = Path.home().resolve()
    if not ham.is_absolute():
        raise ValueError("izin yolu mutlak olmalı")
    hedef = ham.resolve()
    orvant = Path(__file__).resolve().parents[2]
    yasak_kokler = (ev / ".ssh", ev / ".gnupg", orvant)
    if (not hedef.is_relative_to(ev) or hedef == ev or
            any(hedef.is_relative_to(kok) for kok in yasak_kokler) or
            hedef == ev / ".config" or
            (depo is not None and hedef.is_relative_to(Path(depo).resolve()))):
        raise ValueError("izin yolu güvenli kapsam dışında")
    return str(hedef)


class Mimar:
    def __init__(self, calisma, *, yurutucu=None, iz_yolu=None):
        self.calisma = Path(calisma).resolve()
        self.kok = self.calisma / "plan"
        self.yurutucu = yurutucu
        from orvant_op.iz import oturum_iz_yolu
        self.iz_yolu = oturum_iz_yolu(self.calisma, iz_yolu)

    def _olay(self, tur, *, metin=None, veri=None, is_turu="is_bolme", sonuc="ok",
              aktor="orvant", dakika=None):
        self.kok.mkdir(parents=True, exist_ok=True)
        entry = {"id": uuid.uuid4().hex, "t": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "tur": tur, "aktor": aktor, "metin": metin, "veri": veri or {}}
        with (self.kok / "olaylar.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        kaydet(self.iz_yolu, self.calisma.name, is_turu, calisma=self.calisma, aktor_tur=aktor,
               kimlik="mimar" if aktor == "orvant" else "kullanici", sonuc=sonuc,
               ozet=tur, maliyet={"insan_dakika": dakika}, ham={"olay_id": entry["id"], "olay_turu": entry["tur"]},
               gorev=(veri or {}).get("gorev"))
        return entry

    def oku(self):
        return _oku(self.kok / "plan.json")

    def kabul_degistir(self, gorev_id, kabul_id, *, beklenen, komut=None,
                      onay_olay_id, gerekce):
        """Güncel kullanıcı onayıyla tek ölçütü değiştirir; S3/S4 bu yolu kullanamaz."""
        cerceve = inspect.currentframe()
        try:
            while cerceve is not None:
                modul = cerceve.f_globals.get("__name__", "")
                if any(modul == kok or modul.startswith(kok + ".")
                       for kok in ("orvant_op.yurutme", "orvant_op.teshis")):
                    raise PermissionError("S3/S4 kullanıcı kabul ölçütünü değiştiremez")
                cerceve = cerceve.f_back
        finally:
            del cerceve

        plan = self.oku()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is None:
            raise ValueError("bilinmeyen görev")
        if gorev["durum"] in ("kabul", "kosuyor", "inceleme_bekliyor"):
            raise ValueError("bu görev durumunda kabul ölçütü değiştirilemez")
        kabul = next((k for k in gorev["kabul"] if k["id"] == kabul_id), None)
        if kabul is None:
            raise ValueError("bilinmeyen kabul id")
        for ad, metin in (("beklenen", beklenen), ("gerekçe", gerekce)):
            if not isinstance(metin, str) or not metin.strip():
                raise ValueError(f"{ad} boş olamaz")
            yer_tutucu_denetle(metin)
        if komut is not None:
            if kabul["tur"] != "komut":
                raise ValueError("komut yalnız komut türündeki kabulde verilebilir")
            if not isinstance(komut, str) or not komut.strip():
                raise ValueError("komut boş olamaz")
        eski = copy.deepcopy(kabul)
        kabul["beklenen"] = beklenen
        if komut is not None:
            kabul["komut"] = komut
        if kabul == eski:
            raise ValueError("değişiklik yok")

        if not isinstance(onay_olay_id, str) or not onay_olay_id.strip():
            raise ValueError("onay olayı gerekli")
        kararlar = self.kararlar()
        # Sözleşmeden devralınan kararlar onay kaydı yerine geçmez.
        onay_kararlari = _oku(self.kok / "kararlar.json") if (self.kok / "kararlar.json").exists() else []
        guncel = [k for k in onay_kararlari if k.get("kaynak_olay_id") == onay_olay_id]
        if not guncel and any(v.get("kaynak_olay_id") == onay_olay_id
                              for k in onay_kararlari for v in k.get("onceki_degerler", [])):
            raise ValueError("onay olayı güncel değil")
        karar = next((k for k in guncel if k.get("kaynak") == "kullanici"
                      and k.get("durum") == "cozuldu"), None)
        olay_yolu = self.kok / "olaylar.jsonl"
        olaylar = [json.loads(s) for s in olay_yolu.read_text(encoding="utf-8").splitlines()
                   if s.strip()] if olay_yolu.exists() else []
        eslesen = [e for e in olaylar if e.get("id") == onay_olay_id]
        if any(e.get("aktor") != "kullanici" for e in eslesen):
            raise ValueError("onay olayı kullanıcıya ait değil")
        olay = next((e for e in eslesen if e.get("tur") in
                     ("kullanici_karar_cevabi", "kullanici_yeni_girdi")), None)
        if olay is None and karar is None:
            raise ValueError("geçerli kullanıcı onay olayı bulunamadı")
        onay_metni = (olay.get("metin") if olay else None) or (karar.get("deger") if karar else None)
        if not isinstance(onay_metni, str) or not onay_metni.strip():
            raise ValueError("onay metni bulunamadı")
        onay_karar_id = (karar["id"] if karar else
                         (olay.get("veri") or {}).get("karar_id"))

        eski_durum = gorev["durum"]
        eski_deneme = gorev["butce"]["deneme"]
        if gorev["durum"] in ("girdi_bekliyor", "karar_bekliyor", "bekliyor", "hazir", "engelli", "ret"):
            gorev["durum"] = "hazir"
            hesap = durumlari_hesapla(copy.deepcopy(plan),
                                     {k["id"] for k in kararlar if k.get("durum") == "cozuldu"})
            gorev["durum"] = next(g["durum"] for g in hesap["gorevler"] if g["id"] == gorev_id)
        veri_dogrula(plan, _oku(Path(__file__).resolve().parents[1] / "plan_sema.json"))
        kayitlar = kabul_degisiklikleri(self.calisma)
        kd = "KD-" + str(max((int(k["id"].removeprefix("KD-")) for k in kayitlar), default=0) + 1)
        onceki = next((k["onceki_kabuller"] for k in reversed(kayitlar)
                       if k["gorev"] == gorev_id and k["kabul_id"] == kabul_id), [])
        surum_yolu = self.kok / "plan_surumu.json"
        surum = (_oku(surum_yolu)["surum"] if surum_yolu.exists() else 1) + 1
        yol = kehanet_yolu(self.calisma, gorev_id)
        arsiv = yol.parent / "gecersiz" / f"{gorev_id}-{kd}.py" if yol.exists() else None
        if arsiv is not None and arsiv.exists():
            raise ValueError("kehanet arşivi zaten var")
        sha = hashlib.sha256(yol.read_bytes()).hexdigest() if arsiv is not None else None
        t = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        kayit = {"id": kd, "t": t, "gorev": gorev_id, "kabul_id": kabul_id,
                 "onceki_kabuller": copy.deepcopy(onceki) + [eski],
                 "yeni": {"beklenen": kabul["beklenen"], "komut": kabul["komut"]},
                 "onay_olay_id": onay_olay_id, "onay_karar_id": onay_karar_id,
                 "onay_metni": onay_metni, "gerekce": gerekce, "plan_surumu": surum,
                 "kehanet": {"onceki_yol": str(yol) if arsiv is not None else None,
                             "arsiv_yol": str(arsiv) if arsiv is not None else None,
                             "sha256": sha, "durum": "yeniden_uretilmeli"}}
        isaret_yolu = self.kok / "kehanet_durumu.json"
        isaretler = _oku(isaret_yolu) if isaret_yolu.exists() else {}
        isaretler[gorev_id] = {"durum": "yeniden_uretilmeli", "kabul_degisikligi": kd, "t": t}

        # Yerel import mimar ↔ yürütme paket başlangıç döngüsünü önler.
        from orvant_op.yurutme.gecis import gecis_uygula
        gecis = gecis_uygula("kabul_celiskisi", "kabul_degisikligi", onay_olay_id,
                             deneme=eski_deneme, kullanilan_deneme=eski_deneme,
                             islenmis_kanitlar=(k["onay_olay_id"] for k in kayitlar))
        gorev["butce"]["deneme"] = gecis["deneme"]
        kayit["deneme"] = {"eski": eski_deneme, "yeni": gorev["butce"]["deneme"]}

        # Bütün doğrulamalar yukarıda: reddedilen istek hiçbir kalıcı kaydı değiştirmez.
        # Önce işaret: sonraki bir I/O hatasında S3 kehanetsiz devam edemez.
        _yaz(isaret_yolu, isaretler)
        if arsiv is not None:
            arsiv.parent.mkdir(parents=True, exist_ok=True)
            yol.rename(arsiv)
        _yaz(self.kok / "plan.json", plan)
        _yaz(surum_yolu, {"surum": surum})
        with (self.kok / "kabul_degisiklikleri.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        self._olay("deneme_hakki_yenilendi", veri={
            "gorev": gorev_id, "kabul_degisikligi": kd,
            "eski_deneme": eski_deneme, "yeni_deneme": gorev["butce"]["deneme"],
            "eski_durum": eski_durum, "yeni_durum": gorev["durum"]},
            is_turu="yeniden_is_kapsami")
        self._olay("kabul_olcutu_degisti", veri={
            "gorev": gorev_id, "kabul_id": kabul_id, "kabul_degisikligi": kd,
            "onay_olay_id": onay_olay_id, "onay_karar_id": onay_karar_id,
            "plan_surumu": surum, "yeni": kayit["yeni"], "gerekce": gerekce},
            is_turu="karar_belgesi", aktor="orvant")
        return kayit
    def envanter(self, *, skill_dizinleri=None, path=None, host=None):
        from .envanter import envanter_cikar

        sonuc = envanter_cikar(skill_dizinleri=skill_dizinleri, path=path, host=host)
        self.kok.mkdir(parents=True, exist_ok=True)
        _yaz(self.kok / "envanter.json", sonuc)
        self._olay("envanter_cikarildi", veri={
            "skill_sayisi": sum(k["tur"] == "skill" for k in sonuc["kayitlar"]),
            "arac_sayisi": sum(k["tur"] == "arac" for k in sonuc["kayitlar"]),
            "mevcut_arac_sayisi": sum(k.get("mevcut", False) for k in sonuc["kayitlar"]),
            "hata_sayisi": sum("hata" in k for k in sonuc["kayitlar"])}, is_turu="arastirma")
        return sonuc

    def _arac_yetki_denetle(self, plan, envanter, *, kaynak):
        """Host gereksinimlerini açık isteğe ve plan dışı denetim kayıtlarına taşır."""
        sonuc = arac_yetki.yetki_denetimi(plan, envanter)
        self.kok.mkdir(parents=True, exist_ok=True)
        yol = self.kok / "yetki_onerilen_yollar.json"
        oneriler = _oku(yol) if yol.exists() else {}
        for ek in sonuc["eklenen"]:
            kimlik = ek["istek"]["id"]
            oneriler[kimlik] = list(dict.fromkeys(
                oneriler.get(kimlik, []) + ek["onerilen_yollar"]))
            self._olay("arac_yetkisi_eklendi", is_turu="yetki_karari", veri={
                "istek_id": kimlik, "gorev": ek["gorev"],
                "envanter_id": ek["envanter_id"], "onerilen_yollar": ek["onerilen_yollar"]})
        for uyari in sonuc["uyarilar"]:
            self._olay("yetki_gerekebilir", veri=uyari, is_turu="dogrulama", sonuc="ok")
        _yaz(yol, oneriler)
        kayit = {"t": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "kaynak": kaynak, "eklenen": sonuc["eklenen"], "uyarilar": sonuc["uyarilar"]}
        with (self.kok / "arac_yetki_denetimi.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        return sonuc

    def _girdi_bagi_olayi(self, gid, kayit, kaynak):
        self._olay("girdi_bagi_yazildi", veri={"gorev": gid, "kararlar": kayit["kararlar"],
                                              "kaynak": kaynak}, is_turu="dogrulama")

    @staticmethod
    def _gorev_istekleri(plan, gid):
        gorev = next((g for g in plan["gorevler"] if g["id"] == gid), {})
        return [i for i in plan["yetki_istekleri"] if i["id"] in gorev.get("yetki_istek_ids", [])
                and i["durum"] != "reddedildi"]

    def _yinelenen_yetkileri_suz(self, plan, islemler):
        kalan, dusen = [], []
        for islem in islemler:
            ids = []
            hedef = next((g for g in plan["gorevler"] if g["id"] == islem.get("gorev")), None)
            if islem.get("islem") == "yetki_istegi_ekle" and hedef and hedef["durum"] != "kabul":
                istek = islem.get("yetki_istegi") or {}
                g = arac_yetki.istek_gereksinimi(istek)
                ids = arac_yetki.kapsayan_istek_ids(self._gorev_istekleri(plan, hedef["id"]), g)
            if ids:
                dusen.append({"islem": islem, "neden": "mevcut yetki isteği kapsamı karşılıyor",
                              "kapsayan_istek_ids": ids})
            else:
                kalan.append(islem)
        return kalan, dusen

    def yeniden_planla(self, neden, *, gorev=None, teshis_dosyasi=None):
        # G-104: okuma–model çağrısı–yazma yürütme kilidi altında; eşzamanlı yürütmenin plan yazımı kaybolmaz.
        # Yürütme hiç başlamamışsa (yurutme/ yok) yarışacak yazıcı yoktur; kilit dosyası da oluşturulmaz.
        from orvant_op.yurutme.zamanlayici import YurutmeKilidi
        if not (self.calisma / "yurutme").is_dir():
            return self._yeniden_planla(neden, gorev=gorev, teshis_dosyasi=teshis_dosyasi)
        with YurutmeKilidi(self.calisma):
            return self._yeniden_planla(neden, gorev=gorev, teshis_dosyasi=teshis_dosyasi)

    def _yeniden_planla(self, neden, *, gorev=None, teshis_dosyasi=None):
        if not isinstance(neden, str) or not neden.strip():
            raise ValueError("yeniden planlama nedeni gerekli")
        plan = self.oku()
        if gorev is not None and gorev not in {g["id"] for g in plan["gorevler"]}:
            raise ValueError("bilinmeyen görev")
        teshisler = []
        if teshis_dosyasi is not None:
            yol = Path(teshis_dosyasi).resolve()
            if not yol.is_relative_to(self.calisma) or not yol.is_file():
                raise ValueError("teşhis dosyası çalışma içindeki mevcut dosya olmalı")
            teshisler = [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines() if s.strip()]
        else:
            yol = self.calisma / "yurutme" / "teshis.jsonl"
            if yol.exists():
                teshisler = [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines() if s.strip()]
        if gorev is not None:
            teshisler = [t for t in teshisler if t.get("gorev") == gorev]
        for teshis in teshisler:
            oneri = teshis.get("oneri") or {}
            if "mevcut_istek_ids" in oneri:
                mevcut = [i for i in self._gorev_istekleri(plan, teshis.get("gorev") or gorev)
                          if i["id"] in oneri["mevcut_istek_ids"]]
                # Aday başına kapsama (S4 ile aynı kural); eski teşhislerde birleşik gereksinim.
                gerekler = oneri.get("aday_gereksinimleri") or [oneri.get("gereksinim") or {}]
                oneri["kapsayan_istek_ids"] = next(
                    (ids for g in gerekler if (ids := arac_yetki.kapsayan_istek_ids(mevcut, g))), [])
        ref = {"dosya": str(yol), "gorev": gorev,
               "makbuz": teshisler[-1].get("makbuz") if teshisler else None}
        kayit_yolu = self.kok / "yeniden_planlar.jsonl"
        if ref["makbuz"] and kayit_yolu.exists():
            for satir in kayit_yolu.read_text(encoding="utf-8").splitlines():
                if json.loads(satir).get("teshis_ref", {}).get("makbuz") == ref["makbuz"]:
                    raise ValueError("bu olay için yeniden planlama zaten yapıldı")
        girdi = {"plan": plan, "gorev_durumlari": {g["id"]: g["durum"] for g in plan["gorevler"]},
                 "teshisler": teshisler[-3:], "neden": neden, "gorev": gorev,
                 "kararlar": self.kararlar()}
        envanter_yolu = self.kok / "envanter.json"
        envanter = _oku(envanter_yolu) if envanter_yolu.exists() else None
        if envanter is not None:
            girdi["envanter"] = arac_yetki.envanter_ozeti(envanter)
        islemler = plan_duzelt_cagir(girdi, calisma=self.calisma, iz_yolu=self.iz_yolu,
                                     yurutucu=self.yurutucu)
        islemler, dusen = self._yinelenen_yetkileri_suz(plan, islemler)
        # Tümü kapsanmışsa boş işlem kapısına girmeden aynı durum kuralları sürer.
        yeni = (plan_duzelt_uygula(plan, islemler,
                                  cozulmus_kararlar={k["id"] for k in self.kararlar()
                                                       if k.get("durum") == "cozuldu"})
                if islemler or not dusen else copy.deepcopy(plan))
        yeni, bulgular = kabul_bagimliliklarini_duzelt(yeni)
        if gorev is not None:
            hedef = next(g for g in yeni["gorevler"] if g["id"] == gorev)
            if hedef["durum"] not in ("kabul", "inceleme_bekliyor"):
                hedef["durum"] = "hazir"
        yetki_ekleri = []
        if envanter is not None:
            denetim = self._arac_yetki_denetle(yeni, envanter, kaynak="yeniden_planla")
            yeni, yetki_ekleri = denetim["plan"], denetim["eklenen"]
        durumlari_hesapla(yeni, {k["id"] for k in self.kararlar()
                                 if k.get("durum") == "cozuldu"})
        surum_yolu = self.kok / "plan_surumu.json"
        eski_surum = _oku(surum_yolu)["surum"] if surum_yolu.exists() else 1
        kayit = {"t": datetime.now(timezone.utc).isoformat(), "surum": eski_surum + 1,
                 "neden": neden, "teshis_ref": ref, "islemler": islemler, "dusen_islemler": dusen,
                 "deterministik_islemler": [dict(ek, islem="bagimlilik_ekle",
                                                  kaynak="kabul_komutu_denetimi")
                                            for ek in bulgular["eklenecek"]] + [
                     {"islem": "yetki_istegi_ekle", "gorev": ek["gorev"],
                      "yetki_istegi": ek["istek"], "envanter_id": ek["envanter_id"],
                      "kaynak": "envanter_denetimi"} for ek in yetki_ekleri]}
        _yaz(self.kok / "plan.json", yeni)
        ekler = girdi_bagi.yeni_gorevleri_ekle(self.calisma, yeni, self.kararlar(),
                                               {g["id"] for g in plan["gorevler"]})
        for gid, bag in ekler.items():
            self._girdi_bagi_olayi(gid, bag, "yeniden_planlama")
        _yaz(surum_yolu, {"surum": eski_surum + 1})
        with kayit_yolu.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
        for dusen_islem in dusen:
            self._olay("yetki_istegi_yinelenmedi", veri=dusen_islem, is_turu="yetki_karari")
        for uyari in bulgular["uyarilar"]:
            self._olay("kabul_komutu_uyarisi", veri=uyari, is_turu="dogrulama")
        for ek in bulgular["eklenecek"]:
            self._olay("plan_bagimlilik_eklendi", veri=ek, is_turu="dogrulama")
        eski_yetki_ids = {i["id"] for i in plan["yetki_istekleri"]}
        for istek in yeni["yetki_istekleri"]:
            if istek["id"] not in eski_yetki_ids:
                self._olay("yetki_istendi", veri={k: istek[k] for k in
                           ("eylem", "ayrinti", "gerekce")} | {"istek_id": istek["id"]},
                           is_turu="yetki_karari")
        self._olay("yeniden_planlandi", veri={"surum": eski_surum + 1, "teshis_ref": ref},
                   is_turu="yeniden_is_kapsami")
        return {"plan": yeni, "surum": eski_surum + 1, "islemler": islemler}

    def kararlar(self):
        yol = self.kok / "kararlar.json"
        if yol.exists():
            return _oku(yol)
        return copy.deepcopy(_oku(self.calisma / "karsilama" / "sozlesme.json").get("kararlar", []))

    def _dakika_dogrula(self, dakika):
        if dakika is not None and dakika < 0:
            raise ValueError("aktif dakika negatif olamaz")

    def karar(self, karar_id, metin, *, dakika=None, yer_tutucu_kabul=False):
        self._dakika_dogrula(dakika)
        if not metin.strip():
            raise ValueError("karar cevabı boş olamaz")
        yer_tutucu_denetle(metin, kabul=yer_tutucu_kabul)
        depo_karari = self.kok / "depo_karari.json"
        if not (self.kok / "plan.json").exists() and depo_karari.exists():
            acik = _oku(depo_karari)
            if acik["id"] == karar_id and acik["durum"] == "acik":
                return self.planla(depo=metin)
        plan = self.oku()
        kararlar = self.kararlar()
        karar = next((k for k in kararlar if k["id"] == karar_id), None)
        if karar is None:
            raise ValueError("bilinmeyen karar id")
        if karar.get("durum") == "cozuldu":
            karar.setdefault("onceki_degerler", []).append({
                "deger": karar.get("deger"), "kaynak": karar.get("kaynak"),
                "kaynak_olay_id": karar.get("kaynak_olay_id")})
        entry = self._olay("kullanici_karar_cevabi", metin=metin,
                           veri={"karar_id": karar_id, "insan_dakika": dakika},
                           is_turu="hedef_netlestirme", aktor="kullanici", dakika=dakika)
        karar.update(durum="cozuldu", deger=metin, kaynak="kullanici", kaynak_olay_id=entry["id"])
        _yaz(self.kok / "kararlar.json", kararlar)
        durumlari_hesapla(plan, {k["id"] for k in kararlar if k.get("durum") == "cozuldu"})
        _yaz(self.kok / "plan.json", plan)
        return {"karar": karar, "plan": plan}

    def girdi(self, baslik, deger, *, dakika=None, yer_tutucu_kabul=False, gorev=None):
        self._dakika_dogrula(dakika)
        if not baslik.strip() or not deger.strip():
            raise ValueError("başlık ve değer boş olamaz")
        yer_tutucu_denetle(baslik, kabul=yer_tutucu_kabul)
        yer_tutucu_denetle(deger, kabul=yer_tutucu_kabul)
        self.oku()
        kararlar = self.kararlar()
        sayi = 1
        ids = {k["id"] for k in kararlar}
        while f"K-ornek-{sayi}" in ids:
            sayi += 1
        karar_id = f"K-ornek-{sayi}"
        entry = self._olay("kullanici_yeni_girdi", metin=deger,
                           veri={"karar_id": karar_id, "baslik": baslik, "gorev": gorev,
                                 "insan_dakika": dakika},
                           is_turu="hedef_netlestirme", aktor="kullanici", dakika=dakika)
        karar = {"id": karar_id, "baslik": baslik, "durum": "cozuldu", "deger": deger,
                 "kaynak": "kullanici", "kaynak_olay_id": entry["id"]}
        kararlar.append(karar)
        _yaz(self.kok / "kararlar.json", kararlar)
        if gorev is not None:
            bag = girdi_bagi.bag_ekle(self.calisma, gorev, [karar_id], "kullanici_girdi")
            self._girdi_bagi_olayi(gorev, bag, "kullanici_girdi")
        return karar

    def _depo_sorusu(self, yol, hata, taslak_yolu):
        karar_yolu = self.kok / "depo_karari.json"
        onceki = []
        if karar_yolu.exists():
            eski = _oku(karar_yolu)
            onceki = eski.pop("onceki", []) + [eski]
        sayi = max((int(k["id"].removeprefix("K-depo-")) for k in onceki), default=0) + 1
        ev = Path.home().resolve()
        adaylar = ([ev / "Projects" / yol.name] if (ev / "Projects").is_dir() else [])
        adaylar.append(ev / yol.name)
        secenekler = []
        for aday in adaylar:
            try:
                aday, _ = _depo_kontrol(aday, calisma=self.calisma)
            except ValueError:
                continue
            if aday == yol or str(aday) in secenekler:
                continue
            secenekler.append(str(aday))
        ilk = secenekler[0] if secenekler else None
        karar = {"id": f"K-depo-{sayi}", "baslik": "Depo yeri", "kategori": "kisitlar",
                 "sahip": "kullanici", "etki": "yuksek", "belirsizlik": "orta",
                 "geri_alinabilir": True, "onerilen_varsayim": ilk,
                 "soru": {"metin": f"'{yol}' depo için kullanılamıyor: {hata}. "
                                   "Depo hangi yerde oluşturulsun?",
                          "neden_onemli": "Görev dosyalarının yazılacağı güvenli git deposunu belirler.",
                          "secenekler": secenekler, "onerilen": ilk},
                 "durum": "acik", "denenen_yol": str(yol), "hata": str(hata),
                 "taslak": str(taslak_yolu), "onceki": onceki,
                 "cevap_komutu": f"python3 -m orvant_op mimar plan {self.calisma} --depo <yol>"}
        _yaz(karar_yolu, karar)
        self._olay("depo_karari_istendi", veri={"karar_id": karar["id"], "yol": str(yol),
                   "neden": str(hata), "taslak": str(taslak_yolu)}, is_turu="hedef_netlestirme")
        return {"durum": "karar_bekliyor", "karar": karar, "taslak": str(taslak_yolu)}

    def _kapsam_engellerini_bagla(self, plan, matris, kararlar):
        """Yalnız kişisel veri kapsamındaki açık kararları üretime bağlar."""
        acik = matris["acik_kararlar"]
        veri_satirlari = [s for s in matris["satirlar"]
                         if s["veri_yasam_dongusu"]["kisisel_veri"]]
        veri_kararlari = {kid for s in veri_satirlari for kid in s["karar_ids"]}
        veri_kararlari.update(k["id"] for k in acik)
        engeller = [kid for kid in matris["engeller"] if kid in veri_kararlari]
        if not engeller:
            return
        harita = {k["id"]: k for k in kararlar}
        # Saklama/silme politikası tek kullanıcı sorusu; tüm ilgili işler aynı cevabı bekler.
        ortak = next((k for k in acik if k["id"] in harita), acik[0] if acik else None)
        ortak_id = ortak["id"] if ortak else None
        if ortak and ortak_id not in harita:
            kararlar.append(dict(ortak, deger=None, kaynak=None,
                                baslik="Kişisel veriler ne kadar saklanmalı ve nasıl silinmeli?"))
        kapsamlar = []
        for kid in engeller:
            gereksinimler = {k["gereksinim_id"] for k in acik if k["id"] == kid}
            karar_id = ortak_id if gereksinimler and kid not in harita else kid
            satirlar = [s for s in veri_satirlari
                        if (gereksinimler and s["gereksinim_id"] in gereksinimler)
                        or (not gereksinimler and kid in s["karar_ids"])]
            gids = {g for s in satirlar for g in s["plan_gorev_ids"]}
            proje_duzeyi = not satirlar or any(not s["plan_gorev_ids"] for s in satirlar)
            if proje_duzeyi:
                gids = {g["id"] for g in plan["gorevler"]}
            for gorev in plan["gorevler"]:
                if gorev["id"] in gids and karar_id not in gorev["bekleyen_kararlar"]:
                    gorev["bekleyen_kararlar"].append(karar_id)
            kapsamlar.append({"karar_id": karar_id, "gorev_ids": sorted(gids),
                              "kapsam": "proje" if proje_duzeyi else "gorev"})
        matris["engel_kapsamlari"] = kapsamlar
        if any(k["kapsam"] == "proje" for k in kapsamlar):
            uyari = "Proje düzeyi karar bekliyor: doğrudan görev bağı yok; yeni üretim bekletildi."
            matris["uyarilar"].append(uyari)
            self._olay("kaynak_denetimi_uyarisi", veri={"uyari": uyari}, is_turu="dogrulama")

    def planla(self, *, depo=None, yeni_taslak=False):
        if (self.kok / "plan.json").exists():
            raise ValueError("plan zaten var; mevcut yetki kararları korunmalı")
        sozlesme = _oku(self.calisma / "karsilama" / "sozlesme.json")
        onay = sozlesme.get("onay")
        if (not isinstance(onay, dict) or onay.get("durum") not in
                ("onaylandi", "fizibilite_onayli") or
                onay.get("revizyon") != sozlesme.get("revizyon") or
                not onay.get("olay_id")):
            raise ValueError("onaylı sözleşme revizyonu gerekli")
        iddialar = _oku(self.calisma / "karsilama" / "iddialar.json")
        kararlar = sozlesme.get("kararlar", [])
        girdi = {"sozlesme": sozlesme, "iddialar": iddialar,
                 "ertelenen_kararlar": [k for k in kararlar if k.get("durum") == "ertelendi"],
                 "depo_tercihi": str(depo) if depo is not None else None}
        kaynak_yolu = self.calisma / "karsilama" / "kaynak_icerikleri.json"
        kaynaklar = _oku(kaynak_yolu) if kaynak_yolu.exists() else []
        kaynaklar += depo_kaynaklari(depo)
        if kaynaklar:
            girdi["kaynak_icerikleri"] = kaynaklar
        envanter_yolu = self.kok / "envanter.json"
        envanter = _oku(envanter_yolu) if envanter_yolu.exists() else None
        if envanter is not None:
            girdi["envanter"] = arac_yetki.envanter_ozeti(envanter)
        girdi_sha = hashlib.sha256(json.dumps(
            girdi,
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        taslak_koku = self.kok / "taslaklar"
        taslak_koku.mkdir(parents=True, exist_ok=True)
        kimlik = {"sozlesme_revizyon": sozlesme["revizyon"], "onay_olay_id": onay["olay_id"],
                  "girdi_sha256": girdi_sha}
        plan, taslak, taslak_yolu = None, None, None
        if not yeni_taslak:
            for aday in sorted(taslak_koku.glob("*.json"), reverse=True):
                kayit = _oku(aday)
                if (kayit.get("durum") in ("dogrulandi", "depo_reddi") and
                        all(kayit.get(k) == v for k, v in kimlik.items())):
                    # Eski denetim/sürüm kaydı bilinmeyen atıf için güvence değildir.
                    bulgular = iddia_bulgulari(girdi, kayit["plan"])
                    if bulgular:
                        denetim = copy.deepcopy(kayit.get("kaynak_denetimi") or
                                               {"incelenen_kaynaklar": [], "bulgular": [],
                                                "sinirlar": []})
                        denetim["bulgular"].extend(b for b in bulgular if b not in denetim["bulgular"])
                        kayit.update(durum="dogrulama_hatasi", kaynak_denetimi=denetim,
                                     hata="plan kaynak tutarsızlığı: " + "; ".join(bulgular))
                        _yaz(aday, kayit)
                        self._olay("plan_taslagi_reddedildi", veri={"taslak": str(aday),
                                   "bulgular": bulgular}, is_turu="dogrulama", sonuc="ret")
                        continue
                    if ((iddialar and kayit.get("iddia_denetimi_surumu") != 1) or
                            (girdi.get("kaynak_icerikleri") and kayit.get("kaynak_denetimi") is None)):
                        continue
                    plan = copy.deepcopy(kayit["plan"])
                    dogrula(plan, sozlesme, kararlar)
                    taslak, taslak_yolu = kayit, aday
                    self._olay("plan_taslagi_yeniden_kullanildi", veri={"taslak": str(aday)},
                               is_turu="dogrulama")
                    break
        hata = None
        for deneme in (() if plan is not None else (1, 2)):
            t = datetime.now(timezone.utc)
            taslak_yolu = taslak_koku / f"{t.strftime('%Y%m%dT%H%M%S%fZ')}-d{deneme}.json"
            taslak = {"t": t.isoformat().replace("+00:00", "Z"), "deneme": deneme, **kimlik,
                      "ham": None, "plan": None, "durum": "dogrulama_hatasi",
                      "hata": None, "bulgular": None}
            try:
                istem = girdi if deneme == 1 else {**girdi, "yeniden_istek": True,
                                                   "dogrulama_hatasi": str(hata)}
                taban = butce_tabani()
                plan = plan_cikar(istem, calisma=self.calisma, iz_yolu=self.iz_yolu,
                                  yurutucu=self.yurutucu, butce_tabani=taban)
                taslak["ham"] = copy.deepcopy(plan)
                _yaz(taslak_yolu, taslak)
                for gorev in plan["gorevler"]:
                    eski = gorev["butce"]["token"]
                    gorev_tabani = taban * agir_hesap(gorev, ilgili_envanter(gorev, envanter or {}))
                    if eski < gorev_tabani:
                        gorev["butce"]["token"] = gorev_tabani
                        self._olay("butce_tabanina_yukseltildi", veri={"gorev": gorev["id"],
                                    "eski": eski, "yeni": gorev_tabani}, is_turu="dogrulama")
                taslak["bulgular"] = kabul_bagimliliklari(plan)
                plan, bulgular = kabul_bagimliliklarini_duzelt(plan)
                for uyari in bulgular["uyarilar"]:
                    self._olay("kabul_komutu_uyarisi", veri=uyari, is_turu="dogrulama")
                for ek in bulgular["eklenecek"]:
                    self._olay("plan_bagimlilik_eklendi", veri=ek, is_turu="dogrulama")
                dogrula(plan, sozlesme, kararlar)
                denetim = kaynak_denetle(girdi, plan, calisma=self.calisma,
                    iz_yolu=self.iz_yolu, yurutucu=self.yurutucu)
                if denetim is not None:
                    for sinir in denetim.pop("sinir_bulguya_tasinanlar", []):
                        self._olay("sinir_bulguya_tasindi", veri={"sinir": sinir},
                                   is_turu="dogrulama", sonuc="ret")
                    taslak["kaynak_denetimi"] = denetim
                    taslak["iddia_denetimi_surumu"] = 1
                    for uyari in denetim.get("uyarilar", []):
                        self._olay("kaynak_denetimi_uyarisi", veri={"uyari": uyari},
                                   is_turu="dogrulama")
                if denetim and denetim["bulgular"]:
                    raise ValueError("plan kaynak tutarsızlığı: " + "; ".join(denetim["bulgular"]))
                taslak.update(plan=copy.deepcopy(plan), durum="dogrulandi")
                _yaz(taslak_yolu, taslak)
                break
            except ValueError as exc:
                hata = exc
                plan = None
                taslak.update(durum="dogrulama_hatasi", hata=str(exc))
                _yaz(taslak_yolu, taslak)
                self._olay("plan_dogrulama_hatasi", veri={"deneme": deneme, "hata": str(exc)},
                           is_turu="dogrulama", sonuc="ret")
        if plan is None:
            self._olay("plan_reddi", veri={"hata": str(hata)}, is_turu="is_bolme", sonuc="ret")
            raise ValueError(f"plan iki denemede doğrulanamadı: {hata}; taslaklar: {taslak_koku}") from hata
        yol = Path(depo or plan["depo"]["yol"]).expanduser().resolve()
        try:
            if depo is None and not yol.is_relative_to(Path.home().resolve()):
                raise ValueError("önerilen depo yolu kullanıcı ev dizininde olmalı")
            yol, yeni = _depo_kontrol(yol, calisma=self.calisma)
        except ValueError as exc:
            taslak.update(durum="depo_reddi", hata=str(exc))
            _yaz(taslak_yolu, taslak)
            return self._depo_sorusu(yol, exc, taslak_yolu)
        # Depo sorusunda yan kayıt/olay bırakmamak için denetim geçerli depodan sonra koşar.
        if envanter is not None:
            plan = self._arac_yetki_denetle(plan, envanter, kaynak="planla")["plan"]
        if yeni:
            yol.mkdir()
            _git("init", "-b", "main", cwd=yol)
            _git("-c", "user.name=Orvant", "-c", "user.email=orvant@local.invalid",
                 "commit", "--allow-empty", "-m", "Initial empty commit", cwd=yol)
        plan["depo"]["yol"] = str(yol)
        # Eski taslaklar da güncel kapsam denetiminden geçer; önbellek engeli atlayamaz.
        matris = kapsam_matrisi(sozlesme, plan, kararlar)
        kararlar = copy.deepcopy(kararlar)
        self._kapsam_engellerini_bagla(plan, matris, kararlar)
        taslak.setdefault("kaynak_denetimi", {})["kapsam"] = matris
        durumlari_hesapla(plan, {k["id"] for k in kararlar if k.get("durum") == "cozuldu"})
        self.kok.mkdir(parents=True, exist_ok=True)
        _yaz(self.kok / "plan.json", plan)
        _yaz(self.kok / "plan_surumu.json", {"surum": 1})
        kararlar = [dict(k, kaynak_olay_id=k.get("kaynak_olay_id") or onay["olay_id"])
                    for k in kararlar]
        karar_yolu = self.kok / "depo_karari.json"
        if depo is not None and karar_yolu.exists():
            karar = _oku(karar_yolu)
            if karar["durum"] == "acik":
                cevap = self._olay("kullanici_karar_cevabi", metin=str(depo),
                                   veri={"karar_id": karar["id"]}, aktor="kullanici",
                                   is_turu="hedef_netlestirme")
                karar.update(durum="cozuldu", deger=str(yol), kaynak="kullanici",
                             kaynak_olay_id=cevap["id"])
                kararlar.append(karar)
                _yaz(karar_yolu, karar)
        _yaz(self.kok / "kararlar.json", kararlar)
        baglar = girdi_bagi.baglari_turet(plan, kararlar)
        girdi_bagi.baglari_yaz(self.calisma, baglar)
        for gid, bag in baglar["gorevler"].items():
            self._girdi_bagi_olayi(gid, bag, "plan")
        taslak.update(durum="kullanildi", hata=None)
        _yaz(taslak_yolu, taslak)
        self._olay("plan_olusturuldu", veri={"sozlesme_revizyon": sozlesme["revizyon"],
                                          "depo": str(yol), "depo_yeni": yeni}, is_turu="is_bolme")
        self._olay("plan_dogrulandi", veri={"gorev_sayisi": len(plan["gorevler"])}, is_turu="dogrulama")
        for istek in plan["yetki_istekleri"]:
            self._olay("yetki_istendi", veri={"istek_id": istek["id"], "eylem": istek["eylem"],
                                            "ayrinti": istek["ayrinti"], "gerekce": istek["gerekce"]},
                       is_turu="yetki_karari")
        return plan

    def yetkiler(self):
        return [copy.deepcopy(i) for i in self.oku()["yetki_istekleri"] if i["durum"] == "acik"]

    def _izin_yollari(self):
        yol = self.kok / "izin_yollari.json"
        return _oku(yol) if yol.exists() else {}

    def izin(self, istek_id, metin, *, karar, dakika=None, yollar=()):
        if karar not in ("verildi", "reddedildi"):
            raise ValueError("karar verildi veya reddedildi olmalı")
        if not metin.strip():
            raise ValueError("yetki cevap metni boş olamaz")
        if dakika is not None and dakika < 0:
            raise ValueError("aktif dakika negatif olamaz")
        plan = self.oku()
        istek = next((i for i in plan["yetki_istekleri"] if i["id"] == istek_id), None)
        if istek is None or istek["durum"] != "acik":
            raise ValueError("açık yetki isteği bulunamadı")
        if karar != "verildi" and yollar:
            raise ValueError("reddedilen izne yol eklenemez")
        yollar = list(dict.fromkeys(izin_yolu_dogrula(y, plan["depo"]["yol"]) for y in yollar))
        entry = self._olay("kullanici_yetki_cevabi", metin=metin,
                           veri={"istek_id": istek_id, "karar": karar, "insan_dakika": dakika,
                                 "yollar": yollar},
                           is_turu="yetki_karari", aktor="kullanici", dakika=dakika)
        istek["durum"] = karar
        istek["onay_olay_id"] = entry["id"]
        if yollar:
            kayit = self._izin_yollari()
            kayit[istek_id] = [{"yol": yol, "onay_olay_id": entry["id"]} for yol in yollar]
            _yaz(self.kok / "izin_yollari.json", kayit)
        durumlari_hesapla(plan)
        _yaz(self.kok / "plan.json", plan)
        self._olay("yetki_karari_uygulandi", veri={"istek_id": istek_id, "karar": karar,
                                                   "cevap_olay_id": entry["id"]},
                   is_turu="yetki_karari")
        return plan

    def izin_yol(self, istek_id, yol, metin):
        if not metin.strip():
            raise ValueError("kullanıcı notu boş olamaz")
        plan = self.oku()
        istek = next((i for i in plan["yetki_istekleri"] if i["id"] == istek_id), None)
        if not istek or istek["durum"] != "verildi" or not istek.get("onay_olay_id"):
            raise ValueError("verilmiş yetki gerekli")
        yol = izin_yolu_dogrula(yol, plan["depo"]["yol"])
        kayit = self._izin_yollari()
        if any(x["yol"] == yol for x in kayit.get(istek_id, [])):
            raise ValueError("izin yolu zaten kayıtlı")
        entry = self._olay("kullanici_izin_yolu", metin=metin,
                           veri={"istek_id": istek_id, "yol": yol},
                           is_turu="yetki_karari", aktor="kullanici")
        kayit.setdefault(istek_id, []).append({"yol": yol, "onay_olay_id": entry["id"]})
        _yaz(self.kok / "izin_yollari.json", kayit)
        durumlari_hesapla(plan, {k["id"] for k in self.kararlar() if k.get("durum") == "cozuldu"})
        _yaz(self.kok / "plan.json", plan)
        return kayit

    def karar_kanit(self, karar_id, gorev_ids, ozet):
        if not ozet.strip() or not gorev_ids:
            raise ValueError("özet ve görev gerekli")
        plan = self.oku()
        kararlar = self.kararlar()
        karar = next((k for k in kararlar if k["id"] == karar_id), None)
        if not karar:
            raise ValueError("bilinmeyen karar id")
        if karar.get("sahip") != "arastirilabilir" or karar.get("durum") == "cozuldu":
            raise ValueError("yalnız açık araştırılabilir karar görev kanıtıyla çözülebilir")
        makbuzlar = []
        gorevler = {g["id"]: g for g in plan["gorevler"]}
        for gid in dict.fromkeys(gorev_ids):
            if gid not in gorevler or gorevler[gid]["durum"] != "kabul":
                raise ValueError(f"kabul edilmiş görev gerekli: {gid}")
            yollar = sorted((self.calisma / "yurutme" / "makbuzlar").glob(f"{gid}-*.json"))
            bulunan = [p for p in yollar if p.stem.startswith(gid + "-") and
                       _oku(p).get("gorev") == gid and _oku(p).get("karar") == "kabul"]
            if not bulunan:
                raise ValueError(f"kabul makbuzu gerekli: {gid}")
            makbuzlar.append(str(bulunan[-1]))
        entry = self._olay("karar_gorev_kanitiyla_cozuldu", metin=ozet,
                           veri={"karar_id": karar_id, "gorev_ids": list(dict.fromkeys(gorev_ids)),
                                 "makbuzlar": makbuzlar}, is_turu="dogrulama")
        karar.update(durum="cozuldu", deger=ozet, kaynak="gorev_kaniti",
                     kaynak_olay_id=entry["id"], kanit=makbuzlar)
        _yaz(self.kok / "kararlar.json", kararlar)
        durumlari_hesapla(plan, {k["id"] for k in kararlar if k.get("durum") == "cozuldu"})
        _yaz(self.kok / "plan.json", plan)
        return {"karar": karar, "plan": plan}
