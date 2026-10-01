"""Kapı bütünlüğü durdurucusu; onarım kendi karantinasını kaldıramaz."""
import hashlib
import json
from datetime import datetime, timezone

from orvant_op.mimar.kehanet import kehanet_yolu, sozlesme_yolu
from orvant_op.mimar.kusurlu import negatif_kontrol
from .akis import _json_yaz


BUTUNLUK_HATASI = "Kapı bütünlüğü şüphesi: kehanet/sözleşme koşu sırasında değişti"


def kayitlar(calisma):
    """Salt okuma; kuru tur kilit veya yan kayıt oluşturmaz."""
    yol = calisma / "yurutme/karantina.json"
    return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}


class KarantinaMixin:
    def karantinalar(self):
        return {g: k for g, k in kayitlar(self.calisma).items() if k["durum"] == "aktif"}

    def _karantina_dogrula(self, gorev_id):
        if gorev_id in self.karantinalar():
            raise ValueError("görev karantinada; kaldırmak için: python3 -m orvant_op yurut "
                             'karantina-kaldir <calisma> <id> "<gerekçe>"')

    def _kapi_tabani(self, gorev_id):
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                for p in (kehanet_yolu(self.calisma, gorev_id), sozlesme_yolu(self.calisma, gorev_id))}

    def _karantinaya_al(self, gorev_id, neden, tetik, kanit):
        with self._kilit():
            deneme = getattr(self, "_deneme", None)
            if deneme and deneme["gorev"] == gorev_id:
                self._jeton_dogrula(gorev_id)
            kayit = kayitlar(self.calisma)
            if kayit.get(gorev_id, {}).get("durum") == "aktif":
                return kayit[gorev_id]
            yeni = {"durum": "aktif", "neden": neden, "tetik": tetik,
                    "kanit": kanit, "t": datetime.now(timezone.utc).isoformat()}
            # Kaldırma kaydı yeniden tetiklenince de yan kayıtta korunur.
            if gorev_id in kayit:
                eski = dict(kayit[gorev_id])
                gecmis = eski.pop("gecmis", [])
                yeni["gecmis"] = [*gecmis, eski]
            kayit[gorev_id] = yeni
            _json_yaz(self.kok / "karantina.json", kayit)
            plan = self._plan()
            next(g for g in plan["gorevler"] if g["id"] == gorev_id)["durum"] = "engelli"
            self._kaydet_plan(plan)
            self._olay("karantinaya_alindi", gorev_id, **yeni)
            self._iz("dogrulama", neden, sonuc="ret", gorev=gorev_id, kanit=kanit)
            return yeni

    def karantina_kaldir(self, gorev_id, gerekce):
        if not gerekce.strip():
            raise ValueError("karantina kaldırma gerekçesi gerekli")
        with self._kilit():
            kayit = kayitlar(self.calisma)
            if kayit.get(gorev_id, {}).get("durum") != "aktif":
                raise ValueError("aktif karantina kaydı yok")
            kayit[gorev_id].update(durum="kaldirildi", kaldiran="kullanici",
                kaldirma_gerekcesi=gerekce, kaldirma_t=datetime.now(timezone.utc).isoformat())
            _json_yaz(self.kok / "karantina.json", kayit)
            plan = self._plan()
            gorev = next(g for g in plan["gorevler"] if g["id"] == gorev_id)
            if gorev["durum"] == "engelli" and len(self._makbuzlar(gorev)) < gorev["butce"]["deneme"]:
                gorev["durum"] = "hazir"
                self._bagimlilari_ac(plan)
                self._kaydet_plan(plan)
            self._olay("karantina_kaldirildi", gorev_id, aktor="kullanici", gerekce=gerekce)
            self._iz("yeniden_is_kapsami", gerekce, aktor="kullanici", gorev=gorev_id,
                     kanit=[str(self.kok / "karantina.json")])
            return {"gorev": gorev_id, "durum": gorev["durum"], "karantina": "kaldirildi"}

    def _bekletilenler(self, plan):
        from .zamanlayici import bagimli_kapanisi
        sonuc = super()._bekletilenler(plan)
        kabuller = {g["id"] for g in plan["gorevler"] if g["durum"] == "kabul"}
        for kaynak in sorted(self.karantinalar()):
            for kimlik in {kaynak} | (bagimli_kapanisi(plan, kaynak) - kabuller):
                sonuc[kimlik] = f"karantina: {kaynak}"
        return sonuc

    def _butunluk_denetle(self, gorev):
        deneme = getattr(self, "_deneme", None)
        if deneme and deneme["gorev"] == gorev["id"] and deneme["kapi_tabani"] != self._kapi_tabani(gorev["id"]):
            self._karantinaya_al(gorev["id"], BUTUNLUK_HATASI, "kapi_degisti",
                                [json.dumps({"once": deneme["kapi_tabani"],
                                             "sonra": self._kapi_tabani(gorev["id"])}, ensure_ascii=False)])
        kayit = self.karantinalar().get(gorev["id"])
        if kayit:
            gorev["durum"] = "engelli"
            return kayit["neden"]

    def _kapi(self, agac, gorev):
        with self._kilit():
            neden = self._butunluk_denetle(gorev)
            if neden:
                self._son_kehanet_sonucu = {"kehanet": str(kehanet_yolu(self.calisma, gorev["id"])),
                                           "gecti": False, "hata": neden}
                return [], [], [], [neden]
        try:
            degisen, ihlaller, komutlar, hatalar = super()._kapi(agac, gorev)
        except Exception:
            # Kapı okunurken silinmiş/değişmiş dosya da aynı bütünlük ihlalidir.
            with self._kilit():
                neden = self._butunluk_denetle(gorev)
                if not neden:
                    raise
                self._son_kehanet_sonucu = {"kehanet": str(kehanet_yolu(self.calisma, gorev["id"])),
                                           "gecti": False, "hata": neden}
                return [], [], [], [neden]
        with self._kilit():
            neden = self._butunluk_denetle(gorev)
            sonuc = self._son_kehanet_sonucu or {}
            # Yalnız işçi denemesi: eski kabul denetimi ve onarım kapısı bu tetik değildir.
            deneme = getattr(self, "_deneme", None)
            if (not neden and deneme and deneme["gorev"] == gorev["id"] and sonuc.get("hata")
                    and not sonuc.get("zaman_asimi")
                    and sonuc["hata"] != "kehanet yeniden üretilmeli"
                    and negatif_kontrol(sonuc)["durum"] == "kehanet_hatasi"):
                self._karantinaya_al(gorev["id"], "Kehanet çalıştırıcısı hatası: " + sonuc["hata"],
                                    "kehanet_hatasi", negatif_kontrol(sonuc)["kanit"])
            neden = self._butunluk_denetle(gorev)
            if neden and neden not in hatalar:
                hatalar.append(neden)
        return degisen, ihlaller, komutlar, hatalar

    def _makbuz(self, gorev, deneme, **alanlar):
        etkin = getattr(self, "_deneme", None)
        if etkin:
            alanlar["kapi_tabani"] = etkin["kapi_tabani"]
        return super()._makbuz(gorev, deneme, **alanlar)

    def _karantina_makbuzu(self, gorev, makbuz):
        kayit = self.karantinalar().get(gorev["id"])
        if kayit:
            veri = json.loads(makbuz.read_text(encoding="utf-8"))
            veri["karar"] = "ret"
            veri["hatalar"] = list(dict.fromkeys([*veri.get("hatalar", []), kayit["neden"]]))
            _json_yaz(makbuz, veri)
            gorev["durum"] = "engelli"

    def _kabul(self, plan, gorev, agac, makbuz):
        self._karantina_dogrula(gorev["id"])
        try:
            neden = self._butunluk_denetle(gorev)
            if neden:
                raise RuntimeError(neden)
            return super()._kabul(plan, gorev, agac, makbuz)
        finally:
            self._karantina_makbuzu(gorev, makbuz)

    def _birlestirme_oncesi(self, gorev, makbuz):
        super()._birlestirme_oncesi(gorev, makbuz)
        neden = self._butunluk_denetle(gorev)
        if neden:
            self._karantina_makbuzu(gorev, makbuz)
            raise RuntimeError(neden)

    def durum(self):
        return {**super().durum(), "karantina": self.karantinalar()}
