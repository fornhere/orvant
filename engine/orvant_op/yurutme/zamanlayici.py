"""S3 paralel yürütmesi, yürütme grupları ve kabul edilmiş çıktıların etki kümesi."""
import concurrent.futures
import contextlib
import fnmatch
import hashlib
import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import uyum, uyum_komut
from orvant_op.mimar.kehanet import kehanet_gecersiz_mi, kehanet_yolu
from . import korumali, s4_kancasi
from .akis import _git, _json_yaz, _goreli_mi
from .butce_defteri import DenemeMixin
from .karantina import KarantinaMixin


_KILITLER = {}
_KILITLER_KILIDI = threading.Lock()


class YurutmeKilidi:
    """Aynı çalışma için süreçte tek, iş parçacığında yeniden girilebilir flock."""

    def __new__(cls, calisma):
        anahtar = (os.getpid(), str(Path(calisma).resolve()))
        with _KILITLER_KILIDI:
            if anahtar not in _KILITLER:
                nesne = super().__new__(cls)
                nesne.yol = Path(anahtar[1]) / "yurutme" / ".kilit"
                nesne._yerel = threading.local()
                nesne._rkilit = threading.RLock()
                _KILITLER[anahtar] = nesne
            return _KILITLER[anahtar]

    def __enter__(self):
        self._rkilit.acquire()
        try:
            derinlik = getattr(self._yerel, "derinlik", 0)
            if not derinlik:
                self.yol.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.yol, os.O_RDWR | os.O_CREAT | uyum.O_BINARY, 0o666)
                try:
                    uyum.kilitle(fd)
                except BaseException:
                    os.close(fd)
                    raise
                self._yerel.fd = fd
            self._yerel.derinlik = derinlik + 1
            return self
        except BaseException:
            self._rkilit.release()
            raise

    def __exit__(self, *_):
        try:
            self._yerel.derinlik -= 1
            if not self._yerel.derinlik:
                try:
                    uyum.kilit_birak(self._yerel.fd)
                finally:
                    os.close(self._yerel.fd)
                    del self._yerel.fd
        finally:
            self._rkilit.release()


def kaliplar_cakisir(a_listesi, b_listesi):
    """Kalıpların ayrık olduğu kanıtlanamıyorsa çakışma kabul eder."""
    def cakisir(a, b):
        if any(not p or p.startswith("/") or ".." in p.split("/") for p in (a, b)):
            return True
        if uyum.WINDOWS:
            # Ters eğik çizgi/sürücü harfi ayrıklığı kanıtlanamaz; `Src/A.py` ile `src/a.py` aynı dosyadır.
            if any("\\" in p or p[1:2] == ":" for p in (a, b)):
                return True
            a, b = a.casefold(), b.casefold()
        a, b = (p + "**" if p.endswith("/") else p for p in (a, b))
        for x, y in zip(a.split("/"), b.split("/")):
            if "**" in (x, y):
                return True
            x_joker, y_joker = (any(c in p for c in "*?[") for p in (x, y))
            if not x_joker and not y_joker and x != y:
                return False
            if x_joker and not y_joker and not fnmatch.fnmatchcase(y, x):
                return False
            if y_joker and not x_joker and not fnmatch.fnmatchcase(x, y):
                return False
        return True
    return any(cakisir(a, b) for a in a_listesi for b in b_listesi)


def bagimli_kapanisi(plan, kaynak_id):
    """Kaynağın doğrudan ve geçişli bağımlılarını, kaynağı hariç tutarak bulur."""
    bulunan = {kaynak_id}
    while True:
        yeni = {g["id"] for g in plan["gorevler"] if bulunan.intersection(g["bagimliliklar"])}
        if yeni <= bulunan:
            return bulunan - {kaynak_id}
        bulunan |= yeni


def _gorevler_cakisir(plan, a, a_yollari, b, b_yollari):
    if kaliplar_cakisir(a["yazilabilir"], b["yazilabilir"]):
        return True
    for x in a_yollari:
        for y in b_yollari:
            if _goreli_mi(x, y) or _goreli_mi(y, x):
                return True
    def ag_izni(g):
        return any(y["id"] in g["yetki_istek_ids"] and y["durum"] == "verildi"
                   and y.get("onay_olay_id") and y["eylem"] in ("indirme", "ag_erisimi")
                   for y in plan["yetki_istekleri"])
    return ag_izni(a) and ag_izni(b)


def girdi_bekleme_baglami(calisma, gorev_id):
    """Bekleme başlangıcını ve bu beklemenin en son uygunluk olayını okur."""
    calisma = Path(calisma)
    def oku(yol):
        return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines()
                if s.strip()] if yol.exists() else []
    def zaman(e):
        return datetime.fromisoformat(e["t"].replace("Z", "+00:00"))
    plan_olaylari = oku(calisma / "plan/olaylar.jsonl")
    olaylar = oku(calisma / "yurutme/olaylar.jsonl")
    baslangiclar = [zaman(e) for e in olaylar if e.get("gorev") == gorev_id
                   and e.get("tur") in ("engel", "serbest_birakildi")]
    baslangiclar += [zaman(e) for e in plan_olaylari if e.get("tur") == "plan_olusturuldu"]
    baslangic = max(baslangiclar) if baslangiclar else datetime.fromtimestamp(
        (calisma / "plan/plan.json").stat().st_mtime, timezone.utc)
    uygunluk = [e for e in olaylar if e.get("gorev") == gorev_id
                and e.get("tur") == "girdi_uygun_degil" and zaman(e) >= baslangic]
    return baslangic, (max(uygunluk, key=zaman).get("veri", {}) if uygunluk else {})


class Yurutme(DenemeMixin, KarantinaMixin, korumali.Yurutme):
    def __init__(self, *args, isci_zaman_asimi=3600, **kwargs):
        super().__init__(*args, **kwargs)
        self.isci_zaman_asimi = isci_zaman_asimi
        self._grup = None
        self._sahip = None
        self._denemeler = {}

    def _kilit(self):
        return YurutmeKilidi(self.calisma)

    def _kaydet_plan(self, plan):
        with self._kilit():
            if not self._sahip:
                return super()._kaydet_plan(plan)
            self._jeton_dogrula(self._sahip)
            kendi = next(g for g in plan["gorevler"] if g["id"] == self._sahip)
            birlesik = self._plan()
            birlesik["gorevler"] = [kendi if g["id"] == self._sahip else g
                                    for g in birlesik["gorevler"]]
            if kendi["durum"] == "kabul":
                self._bagimlilari_ac(birlesik)
            super()._kaydet_plan(birlesik)
            plan.clear()
            plan.update(birlesik)

    def _agac_ac(self, depo, gorev):
        with self._kilit():
            return super()._agac_ac(depo, gorev)

    def _istisna_engelle(self, plan, gorev, exc):
        with self._kilit():
            # Sıralı yolun dış hata yakalayıcısı da eski planın tamamını yazmamalı.
            guncel = self._plan()
            hedef = next(g for g in guncel["gorevler"] if g["id"] == gorev["id"])
            sonuc = super()._istisna_engelle(guncel, hedef, exc)
            plan.clear()
            plan.update(guncel)
            return sonuc

    def _kabul(self, plan, gorev, agac, makbuz):
        with self._kilit():
            sonuc = super()._kabul(plan, gorev, agac, makbuz)
            self._isaret_sil(gorev["id"])
            self._etki_isaretle(self._plan(), gorev["id"], "kaynak_yeniden_kabul")
            return sonuc

    def _yeniden_kapi(self, gorev_id):
        with self._kilit():
            self._karantina_dogrula(gorev_id)
            sonuc = super()._yeniden_kapi(gorev_id)
            yol = Path(sonuc["makbuz"])
            veri = json.loads(yol.read_text(encoding="utf-8"))
            veri.update(yurutme_grubu=self._grup, gorev_denemesi=veri.get("deneme"))
            _json_yaz(yol, veri)
            return sonuc

    def serbest(self, gorev_id):
        with self._kilit():
            return super().serbest(gorev_id)

    def ac(self, gorev_id, ek_deneme, gerekce):
        with self._kilit():
            sonuc = super().ac(gorev_id, ek_deneme, gerekce)
            self._jeton_gecersiz_kil({gorev_id}, "Görev yeniden açıldı: " + gerekce)
            return sonuc

    def incele(self, gorev_id, kabul_id, sonuc, not_metni, *, dakika=None):
        with self._kilit():
            self._karantina_dogrula(gorev_id)
            return super().incele(gorev_id, kabul_id, sonuc, not_metni, dakika=dakika)

    def _olay(self, tur, gorev, *, is_turu=None, **veri):
        deneme = getattr(self, "_deneme", None)
        if deneme and gorev == deneme["gorev"]:
            veri.setdefault("jeton", deneme["jeton"])
        elif gorev is not None:
            kayit = self._jetonlar().get(gorev)
            if kayit:
                veri.setdefault("jeton", kayit["jeton"])
        if self._grup:
            veri["yurutme_grubu"] = self._grup
        if gorev is not None:
            if "deneme" in veri:
                self._denemeler[gorev] = veri["deneme"]
            if gorev in self._denemeler:
                veri.setdefault("gorev_denemesi", self._denemeler[gorev])
        return super()._olay(tur, gorev, is_turu=is_turu, **veri)

    def _makbuz(self, gorev, deneme, **alanlar):
        alanlar.update(yurutme_grubu=self._grup, gorev_denemesi=deneme)
        etkin = getattr(self, "_deneme", None)
        if etkin:
            alanlar.setdefault("jeton", etkin["jeton"])
        with self._kilit():
            if etkin and alanlar.get("karar") != "eski_deneme_reddedildi":
                self._jeton_dogrula(gorev["id"])
            yol = super()._makbuz(gorev, deneme, **alanlar)
            # S4 özyinelemesi başlamadan rezervasyonu gerçek harcamayla kapat.
            self._uzlastir()
            return yol

    def yurut(self, *, gorev_id=None, en_fazla=None, paralel=1):
        if paralel < 1:
            raise ValueError("--paralel pozitif olmalı")
        if en_fazla is None:
            en_fazla = 1 if paralel == 1 else paralel
        if en_fazla < 1:
            raise ValueError("--en-fazla pozitif olmalı")
        if gorev_id is not None:
            plan = self._plan()
            neden = self._bekletilenler(plan).get(gorev_id)
            if neden:
                gorev = next(g for g in plan["gorevler"] if g["id"] == gorev_id)
                return [{"gorev": gorev_id, "durum": gorev["durum"], "bekletildi": neden}]
        self._grup = "yg-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self._denemeler.clear()
        basla = time.monotonic()
        sonuclar, bitis = [], {}
        # Grup olayları yalnız en az bir görev denemesi başladığında yazılır; hiçbir görev
        # seçilmeyen çağrı çalışma dizininde iz bırakmaz.
        # Paralel kopyalar sığ kopyadır; durum sözlüğü paylaşılır.
        self._grup_durum = {"yazildi": False,
                            "acilis": dict(paralel=paralel, en_fazla=en_fazla, gorev_filtresi=gorev_id)}
        try:
            if paralel == 1:
                sonuclar = super().yurut(gorev_id=gorev_id, en_fazla=en_fazla)
            else:
                s4_kancasi.yeniden_degerlendir(self, gorev_id)
                self._paralel_yurut(gorev_id, en_fazla, paralel, sonuclar)
            return sonuclar
        except BaseException as exc:
            bitis["istisna"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self._grup_durum["yazildi"]:
                self._olay("yurutme_grubu_bitti", None, sonuclar=sonuclar,
                           sure=time.monotonic() - basla, **bitis)
            self._grup = None

    def _grup_baslat(self):
        with self._kilit():
            durum = getattr(self, "_grup_durum", None)
            if durum and not durum["yazildi"]:
                durum["yazildi"] = True
                self._olay("yurutme_grubu_basladi", None, **durum["acilis"])

    def _paralel_yurut(self, gorev_id, en_fazla, paralel, sonuclar):
        kosan, hatalar = {}, []
        bitenler = queue.SimpleQueue()
        dur = threading.Event()
        baslatilan = 0

        def calistir(kopya, kimlik, yollar):
            try:
                with kopya._kilit():
                    plan = kopya._plan()
                    gorev = next(g for g in plan["gorevler"] if g["id"] == kimlik)
                return kopya._gorev_yurut(plan, gorev, izin_yollari=yollar)
            except Exception as exc:
                dur.set()
                with kopya._kilit():
                    plan = kopya._plan()
                    gorev = next(g for g in plan["gorevler"] if g["id"] == kimlik)
                    if gorev["durum"] != "kabul":
                        kopya._istisna_engelle(plan, gorev, exc)
                raise

        onde = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=paralel) as havuz:
            while True:
                # Tamamlanma kuyruğu sonuçları bitiş sırasıyla tutar.
                while onde or not bitenler.empty():
                    gelecek = onde.pop() if onde else bitenler.get()
                    gorev, _ = kosan.pop(gelecek)
                    try:
                        sonuclar.append(gelecek.result())
                    except Exception as exc:
                        hatalar.append(f"{gorev['id']}: {type(exc).__name__}: {exc}")
                        dur.set()
                if not dur.is_set() and baslatilan < en_fazla:
                    try:
                        with self._kilit():
                            plan = self._plan()
                            if gorev_id is not None and kehanet_gecersiz_mi(self.calisma, gorev_id):
                                raise ValueError("kehanet yeniden üretilmeli: " +
                                                 uyum_komut.orvant_komutu(
                                                     ("mimar", "kehanet", str(self.calisma), "--gorev", gorev_id)))
                            for bekleyen in plan["gorevler"]:
                                if (not kehanet_gecersiz_mi(self.calisma, bekleyen["id"])
                                        and bekleyen["durum"] in ("girdi_bekliyor", "yetki_bekliyor")
                                        and (gorev_id is None or gorev_id == bekleyen["id"])
                                        and (self._yeni_kullanici_olayi(bekleyen)
                                             or self._bekleme_ciktilari(plan, bekleyen))):
                                    self.serbest(bekleyen["id"])
                            plan = self._plan()
                            bekletilenler = self._bekletilenler(plan)
                            if gorev_id in bekletilenler:
                                g = next(g for g in plan["gorevler"] if g["id"] == gorev_id)
                                sonuclar.append({"gorev": gorev_id, "durum": g["durum"],
                                                 "bekletildi": bekletilenler[gorev_id]})
                                break
                            for aday in plan["gorevler"]:
                                if dur.is_set() or len(kosan) >= paralel or baslatilan >= en_fazla:
                                    break
                                if (aday["id"] in bekletilenler or aday["durum"] != "hazir" or
                                        (gorev_id is not None and aday["id"] != gorev_id) or
                                        any(g["id"] == aday["id"] for g, _ in kosan.values()) or
                                        kehanet_gecersiz_mi(self.calisma, aday["id"])):
                                    continue
                                yollar, eksik = self._izinli_yollar(plan, aday)
                                if eksik:
                                    aday["durum"] = "yetki_bekliyor"
                                    self._kaydet_plan(plan)
                                    self._engel(aday, eksik)
                                    sonuclar.append({"gorev": aday["id"], "durum": "yetki_bekliyor", "gerekce": eksik})
                                    continue
                                if any(_gorevler_cakisir(plan, aday, yollar, g, y) for g, y in kosan.values()):
                                    continue
                                kopya = type(self)(self.calisma, yurutucu=self.yurutucu, goals_db=self.goals_db,
                                                   iz_yolu=self.iz_yolu, komut_zaman_asimi=self.komut_zaman_asimi,
                                                   isci_zaman_asimi=self.isci_zaman_asimi)
                                if hasattr(self, "plan_yurutucu"):
                                    kopya.plan_yurutucu = self.plan_yurutucu
                                kopya._grup, kopya._sahip = self._grup, aday["id"]
                                kopya._grup_durum = self._grup_durum
                                gelecek = havuz.submit(calistir, kopya, aday["id"], yollar)
                                kosan[gelecek] = (aday, yollar)
                                gelecek.add_done_callback(bitenler.put)
                                baslatilan += 1
                    except Exception as exc:
                        if not kosan:
                            raise
                        hatalar.append(f"zamanlayıcı: {type(exc).__name__}: {exc}")
                        dur.set()
                if not kosan:
                    break
                # wait() geri çağrıdan önce dönebilir; kuyruktan engelleyici okuma meşgul döngüyü önler.
                onde.append(bitenler.get())
        if hatalar:
            raise RuntimeError("Paralel yürütme başarısız: " + "; ".join(hatalar))

    def _bekletilenler(self, plan):
        bekletilenler = super()._bekletilenler(plan)
        kabuller = {g["id"] for g in plan["gorevler"] if g["durum"] == "kabul"}
        for kimlik, isaret in self.denetim_isaretleri().items():
            if kimlik in kabuller and isaret.get("durum") in ("bekliyor", "geri_alma_adayi"):
                for bagimli in bagimli_kapanisi(plan, kimlik) - kabuller:
                    bekletilenler.setdefault(bagimli, f"{kimlik} yeniden denetim bekliyor")
        return bekletilenler

    def iptal(self, gorev_id, gerekce):
        if not gerekce.strip():
            raise ValueError("iptal gerekçesi gerekli")
        # G-153: birleşik ağaç ve S4 yeniden denetim kapıları kilidi tutarak koşar; kilitten önce
        # istek bırakılır ve kayıtlı grup durdurulur ki kapı bitsin ve deneme birleşmeden reddedilsin.
        on = self._iptal_istegi_birak(gorev_id, gerekce)
        try:
            with self._kilit():
                plan = self._plan()
                gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
                kayit = self._jetonlar().get(gorev_id, {})
                tamamlandi = (on and gorev is not None and gorev["durum"] == "engelli"
                              and kayit.get("jeton") == on["jeton"] and not kayit.get("gecerli")
                              and str(kayit.get("gecersiz_kilma_nedeni", "")).startswith("İptal: "))
                if tamamlandi:
                    pass  # G-154: isteği gören deneme iptali bu kilitten önce tamamladı.
                elif on and kayit and kayit.get("jeton") != on["jeton"]:
                    # G-156: gecikmede istenen deneme bitti ve yenisi başladı; kullanıcı onu iptal etmedi.
                    raise ValueError("iptal edilen deneme artık koşmuyor; yeni deneme başladı")
                elif (gorev is None or gorev["durum"] != "kosuyor"
                      or (on and not kayit)):
                    raise ValueError("yalnız koşan görev iptal edilebilir")
                else:
                    self._jeton_gecersiz_kil({gorev_id}, "İptal: " + gerekce)
                    gorev["durum"] = "engelli"
                    self._kaydet_plan(plan)
                    self._engel(gorev, "İptal: " + gerekce)
                    self._olay("deneme_iptal_edildi", gorev_id, gerekce=gerekce)
        finally:
            if on:
                yol = self._iptal_istegi_yolu(gorev_id)
                with contextlib.suppress(OSError, ValueError):
                    if json.loads(yol.read_text(encoding="utf-8")).get("jeton") == on["jeton"]:
                        yol.unlink()
        # Kilit dışında: durdurulan işçinin süreci reddi yazmak için kilidi alabilmeli.
        jeton = on["jeton"] if on else kayit.get("jeton")
        guncel = self._jetonlar().get(gorev_id, {})
        isci = None
        if guncel.get("jeton") == jeton:
            kayitli = (guncel.get("isci") or {}).get("pgid", (on or {}).get("pgid"))
            durdurulan = (on or {}).get("durdurulan", {})
            isci = durdurulan.get(kayitli) or self._isci_durdur(gorev_id, jeton)
        return {"gorev": gorev_id, "durum": "engelli", "isci": isci}

    def denetim_isaretleri(self):
        """Salt okuma: kuru turda kilit dosyası bile oluşturmaz."""
        yol = self.kok / "yeniden_denetim.json"
        return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}

    def _kehanet_sha(self, gorev_id):
        yol = kehanet_yolu(self.calisma, gorev_id)
        return hashlib.sha256(yol.read_bytes()).hexdigest() if yol.exists() else None

    def _son_denetlenen_sha(self, gorev):
        yollar = [*self._makbuzlar(gorev), *self._kapi_makbuzlari(gorev),
                  *(self.kok / "makbuzlar").glob(f"{gorev['id']}-denetim-*.json")]
        for yol in sorted(yollar, key=lambda p: p.stat().st_mtime_ns, reverse=True):
            veri = json.loads(yol.read_text(encoding="utf-8"))
            if veri.get("geri_alindi") or veri.get("karar") not in ("kabul", "yeniden_denetim_gecti"):
                continue
            sonuc = veri.get("main_ile_kehanet_sonucu", veri.get("kehanet_sonucu")) or {}
            return sonuc.get("sha256")
        return None

    def kehanet_degisenler(self):
        """Kabulün dayandığı son başarılı denetimle güncel kehaneti karşılaştırır."""
        isaretler = self.denetim_isaretleri()
        adaylar = []
        for gorev in self._plan()["gorevler"]:
            kimlik = gorev["id"]
            if (gorev["durum"] != "kabul" or kimlik in isaretler
                    or kehanet_gecersiz_mi(self.calisma, kimlik)):
                continue
            sha = self._kehanet_sha(kimlik)
            onceki = self._son_denetlenen_sha(gorev)
            if sha is not None and sha != onceki:
                adaylar.append({"gorev": kimlik, "onceki_sha": onceki, "guncel_sha": sha})
        return adaylar

    def kehanet_degisimi_isaretle(self, gorev_id):
        with self._kilit():
            aday = next((a for a in self.kehanet_degisenler() if a["gorev"] == gorev_id), None)
            if aday is None:
                return None
            isaretler = self._isaretler()
            veri = {"neden": "kehanet_degisti", "kaynak_gorev": gorev_id,
                    "onceki_sha": aday["onceki_sha"], "guncel_sha": aday["guncel_sha"],
                    "durum": "bekliyor", "t": datetime.now(timezone.utc).isoformat()}
            isaretler[gorev_id] = veri
            _json_yaz(self.kok / "yeniden_denetim.json", isaretler)
            self._olay("yeniden_denetim_isaretlendi", gorev_id, **veri)
            self._iz("dogrulama", "Kehanet değişimi yeniden denetim bekliyor", gorev=gorev_id, ham=veri)
            return veri

    def etki_denetimi_gerekenler(self):
        """Ret makbuzundan beri main veya kehanet değişmediyse kapıyı tekrarlamaz."""
        plan = self._plan()
        isaretler = self.denetim_isaretleri()
        adaylar, ana = [], None
        for gorev in plan["gorevler"]:
            kimlik = gorev["id"]
            isaret = isaretler.get(kimlik, {})
            if gorev["durum"] != "kabul" or kehanet_gecersiz_mi(self.calisma, kimlik):
                continue
            if isaret.get("durum") == "bekliyor":
                adaylar.append(kimlik)
            elif isaret.get("durum") == "geri_alma_adayi":
                yol = Path(isaret["makbuz"])
                veri = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
                if ana is None:
                    ana = _git(self._depo(plan), "rev-parse", "main").stdout.strip()
                if (veri.get("main") != ana or
                        (veri.get("kehanet_sonucu") or {}).get("sha256") != self._kehanet_sha(kimlik)):
                    adaylar.append(kimlik)
        return adaylar

    def _isaretler(self):
        with self._kilit():
            yol = self.kok / "yeniden_denetim.json"
            return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}

    def _isaret_sil(self, gorev_id):
        with self._kilit():
            isaretler = self._isaretler()
            if gorev_id in isaretler:
                del isaretler[gorev_id]
                _json_yaz(self.kok / "yeniden_denetim.json", isaretler)

    def _etki_isaretle(self, plan, kaynak_id, neden):
        with self._kilit():
            bagimlilar = bagimli_kapanisi(plan, kaynak_id)
            isaretler, secilen = self._isaretler(), []
            for g in plan["gorevler"]:
                if g["id"] in bagimlilar and g["durum"] == "kabul":
                    isaretler[g["id"]] = {"neden": neden, "kaynak_gorev": kaynak_id,
                        "t": datetime.now(timezone.utc).isoformat(), "durum": "bekliyor", "yurutme_grubu": self._grup}
                    secilen.append(g["id"])
            if secilen:
                _json_yaz(self.kok / "yeniden_denetim.json", isaretler)
            for kimlik in secilen:
                self._olay("yeniden_denetim_isaretlendi", kimlik, neden=neden, kaynak_gorev=kaynak_id)
                self._iz("dogrulama", "yeniden denetim işaretlendi", gorev=kimlik,
                         ham={"neden": neden, "kaynak_gorev": kaynak_id})
            return secilen

    def geri_al(self, gorev_id, gerekce, *, yanlis_kabul=False):
        with self._kilit():
            isaret = self.denetim_isaretleri().get(gorev_id, {})
            sonuc = super().geri_al(gorev_id, gerekce)
            self._jeton_gecersiz_kil({gorev_id} | bagimli_kapanisi(self._plan(), gorev_id),
                                     "Kaynak geri alındı: " + gerekce)
            if yanlis_kabul or isaret.get("durum") == "geri_alma_adayi":
                self._karantinaya_al(gorev_id, "Yanlış kabul geri alındı: " + gerekce,
                                    "yanlis_kabul", [sonuc["makbuz"], *([isaret["makbuz"]] if isaret.get("makbuz") else [])])
                sonuc["durum"] = "engelli"
            self._isaret_sil(gorev_id)
            sonuc["yeniden_denetim_isaretlenen"] = self._etki_isaretle(
                self._plan(), gorev_id, "kaynak_geri_alindi")
            return sonuc

    def yeniden_denetle(self, gorev_id):
        with self._kilit():
            self._karantina_dogrula(gorev_id)
            plan = self._plan()
            gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
            if gorev is None or gorev["durum"] != "kabul":
                raise ValueError("yeniden denetim için kabul edilmiş görev gerekli")
            depo = self._depo(plan)
            # Aynı kimlik güvenlik denetimini geçmeden worktree yolu üretme.
            agac = depo / ".orvant" / "denetim" / self._agac(depo, gorev_id).name
            isaret = self._isaretler().get(gorev_id, {})
            if agac.exists():
                _git(depo, "worktree", "remove", "--force", str(agac))
            agac.parent.mkdir(parents=True, exist_ok=True)
            numara = len(list((self.kok / "makbuzlar").glob(f"{gorev_id}-denetim-*.json"))) + 1
            makbuz = self.kok / "makbuzlar" / f"{gorev_id}-denetim-{numara}.json"
            try:
                _git(depo, "worktree", "add", "--detach", str(agac), "main")
                ana = _git(agac, "rev-parse", "HEAD").stdout.strip()
                degisen, ihlaller, komutlar, hatalar = self._kapi(agac, gorev)
                _json_yaz(makbuz, {"gorev": gorev_id, "yeniden_denetim": True,
                    "neden": isaret.get("neden"), "kaynak_gorev": isaret.get("kaynak_gorev"),
                    "main": ana, "degisen_dosyalar": degisen, "kapsam_ihlalleri": ihlaller,
                    "komut_sonuclari": komutlar, "hatalar": hatalar,
                    "kehanet": self._son_kehanet_sonucu["kehanet"], "kehanet_sonucu": self._son_kehanet_sonucu,
                    "yol_butunlugu": self._son_yol_butunlugu,
                    "insan_incelemeleri": {"kimlikler": [k["id"] for k in gorev["kabul"] if k["tur"] == "insan_incelemesi"],
                                           "not": "Önceki insan incelemeleri yeniden sorulmadı."},
                    "yurutme_grubu": self._grup, "karar": "ret" if hatalar else "yeniden_denetim_gecti"})
            finally:
                if agac.exists():
                    _git(depo, "worktree", "remove", "--force", str(agac))
            sonuc = {"gorev": gorev_id, "sonuc": "geri_alma_adayi" if hatalar else "gecti",
                     "makbuz": str(makbuz), "hatalar": hatalar}
            if hatalar:
                isaretler = self._isaretler()
                isaretler[gorev_id] = {**isaret, "neden": isaret.get("neden", "kaynak_yeniden_kabul"),
                    "kaynak_gorev": isaret.get("kaynak_gorev", gorev_id),
                    "t": datetime.now(timezone.utc).isoformat(), "yurutme_grubu": self._grup,
                    "durum": "geri_alma_adayi", "hatalar": hatalar, "makbuz": str(makbuz)}
                isaretler[gorev_id]["oneri"] = uyum_komut.birlestir([
                    *uyum_komut.python_argv(), "-m", "orvant_op", "yurut", "geri-al", str(self.calisma),
                    gorev_id, "Yeniden denetim kapısı kaldı"])
                _json_yaz(self.kok / "yeniden_denetim.json", isaretler)
                self._olay("yeniden_denetim_kaldi", gorev_id, hatalar=hatalar, makbuz=str(makbuz))
                self._iz("dogrulama", "yeniden denetim kaldı", sonuc="ret", gorev=gorev_id, kanit=[str(makbuz)])
                sonuc["oneri"] = uyum_komut.birlestir([*uyum_komut.python_argv(), "-m", "orvant_op", "yurut",
                                                       "geri-al", str(self.calisma),
                                                       gorev_id, "Yeniden denetim kapısı kaldı"])
            else:
                self._isaret_sil(gorev_id)
                self._olay("yeniden_denetim_gecti", gorev_id, makbuz=str(makbuz))
            return sonuc

    def yeniden_denetle_isaretliler(self):
        with self._kilit():
            isaretler = self._isaretler()
            return [self.yeniden_denetle(g["id"]) for g in self._plan()["gorevler"]
                    if isaretler.get(g["id"], {}).get("durum") in ("bekliyor", "geri_alma_adayi")]

    def kapi(self, gorev_id):
        with self._kilit():
            self._karantina_dogrula(gorev_id)
            gorev = next((g for g in self._plan()["gorevler"] if g["id"] == gorev_id), None)
            if gorev and gorev["durum"] == "kabul" and gorev_id in self._isaretler():
                return self.yeniden_denetle(gorev_id)
            return super().kapi(gorev_id)

    def durum(self):
        with self._kilit():
            sonuc = super().durum()
            sonuc["yeniden_denetim"] = self._isaretler()
            yol = self.kok / "olaylar.jsonl"
            # Olay yazıcısının flock'u, okuyucunun yarım satır görmesini de önler.
            if yol.exists():
                with yol.open(encoding="utf-8") as fh:
                    with uyum.kilit(fh, paylasimli=True):
                        olaylar = [json.loads(s) for s in fh if s.strip()]
            else:
                olaylar = []
            gruplar = {}
            for olay in olaylar:
                veri, tur = olay["veri"], olay["tur"]
                kimlik = veri.get("yurutme_grubu")
                if not kimlik:
                    continue
                grup = gruplar.setdefault(kimlik, {"kimlik": kimlik, "baslangic": None, "bitis": None,
                                                   "paralel": None, "gorev_denemeleri": {}})
                if tur == "yurutme_grubu_basladi":
                    grup.update(baslangic=olay["t"], paralel=veri["paralel"])
                elif tur == "yurutme_grubu_bitti":
                    grup["bitis"] = olay["t"]
                elif tur in ("gorev_denemesi_basladi", "gorev_denemesi_bitti"):
                    anahtar = (olay["gorev"], veri["gorev_denemesi"])
                    deneme = grup["gorev_denemeleri"].setdefault(anahtar,
                        {"gorev": anahtar[0], "deneme": anahtar[1], "basla": None, "bitis": None, "durum": None})
                    if tur == "gorev_denemesi_basladi":
                        deneme["basla"] = olay["t"]
                    else:
                        deneme.update(bitis=olay["t"], durum=veri.get("durum", "istisna"))
                        if "istisna" in veri:
                            deneme["istisna"] = veri["istisna"]
            for grup in gruplar.values():
                denemeler = list(grup["gorev_denemeleri"].values())
                noktalar = []
                for d in denemeler:
                    if d["basla"]:
                        noktalar.append((d["basla"], 1, d["gorev"]))
                        if d["bitis"]:
                            noktalar.append((d["bitis"], -1, d["gorev"]))
                etkin, azami = {}, 0
                for _, fark, kimlik in sorted(noktalar):
                    etkin[kimlik] = etkin.get(kimlik, 0) + fark
                    # S4 özyinelemesi aynı görevi ikinci bir paralel işçi saymaz.
                    azami = max(azami, sum(n > 0 for n in etkin.values()))
                grup.update(gorev_denemeleri=denemeler, en_fazla_eszamanli=azami)
            sonuc["yurutme_gruplari"] = list(reversed(list(gruplar.values())))[:5]
            return sonuc
