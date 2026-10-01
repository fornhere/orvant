"""S3'ün S4 için gereken durum, bütçe ve doğrulayıcı korumaları."""

import json
import re
from pathlib import Path

from .s4_kancasi import beklenen_girdiler, durumlari_hesapla_korunmus
from . import s4_kancasi

from .akis import Yurutme as _TemelYurutme
from .geri_al import GeriAlMixin
from orvant_op.mimar.kehanet import kehanet_yolu
from orvant_op.butce import kok_butce_durumu
from orvant_op.girdi_uygunlugu import denetle


class Yurutme(GeriAlMixin, _TemelYurutme):
    def _girdi_denetle(self, gorev):
        sonuc = denetle(self.calisma, gorev)
        self._son_girdi_uygunlugu = sonuc
        if any(g["olcum"]["durum"] in ("atlandi", "hata") for g in sonuc["girdiler"]):
            self._uyari("girdi_uygunlugu_atlandi", gorev["id"], sonuc=sonuc)
        return sonuc

    @staticmethod
    def _girdi_hatalari(sonuc):
        return [f"Girdi uygun değil: {g['yol']}: {'; '.join(g['nedenler'])}"
                for g in sonuc["girdiler"] if g["uygun"] is False]

    def _gorev_yurut(self, plan, gorev, *, izin_yollari=()):
        baslangic = len(self._makbuzlar(gorev))
        self._son_istisna = None
        try:
            return self._girdiyle_gorev_yurut(plan, gorev, izin_yollari=izin_yollari)
        except Exception:
            self._son_istisna = {"gorev": gorev["id"],
                                 "isci_kostu": len(self._makbuzlar(gorev)) > baslangic}
            raise

    def _istisna_engelle(self, plan, gorev, exc):
        baglam = getattr(self, "_son_istisna", None)
        self._son_istisna = None
        super()._istisna_engelle(plan, gorev, exc)
        if baglam and baglam["gorev"] == gorev["id"]:
            s4_kancasi.istisnayi_isle(self, plan, gorev, exc, isci_kostu=baglam["isci_kostu"])

    def yurut(self, *, gorev_id=None, **kwargs):
        s4_kancasi.yeniden_degerlendir(self, gorev_id)
        return super().yurut(gorev_id=gorev_id, **kwargs)

    def _girdiyle_gorev_yurut(self, plan, gorev, *, izin_yollari=()):
        sonuc = self._girdi_denetle(gorev)
        if sonuc["durum"] == "uygun_degil":
            gerekce = "; ".join(self._girdi_hatalari(sonuc)) + ". Uygun yeni dosya yolu gerekli."
            gorev["durum"] = "girdi_bekliyor"
            self._kaydet_plan(plan)
            self._engel(gorev, gerekce)
            kotu = [g for g in sonuc["girdiler"] if g["uygun"] is False]
            kayit = str((self.calisma / "plan/girdi_uygunlugu.json").resolve())
            self._olay("girdi_uygun_degil", gorev["id"], is_turu="dogrulama",
                       karar_ids=list(dict.fromkeys(g["karar_id"] for g in kotu)),
                       basliklar=[g["baslik"] for g in kotu], yollar=[g["yol"] for g in kotu],
                       nedenler=[n for g in kotu for n in g["nedenler"]], kayit=kayit)
            self._iz("dogrulama", gerekce, sonuc="ret", ham=sonuc, kanit=[kayit], gorev=gorev["id"])
            return {"gorev": gorev["id"], "durum": "girdi_bekliyor", "gerekce": gerekce,
                    "girdi_uygunlugu": sonuc}
        return super()._gorev_yurut(plan, gorev, izin_yollari=izin_yollari)

    def _makbuz(self, gorev, deneme, **alanlar):
        alanlar["girdi_uygunlugu"] = getattr(self, "_son_girdi_uygunlugu", None)
        return super()._makbuz(gorev, deneme, **alanlar)

    def _bagimlilari_ac(self, plan):
        durumlari_hesapla_korunmus(plan, self._cozulmus_kararlar())

    def _kapi(self, agac, gorev):
        degisen, ihlaller, komutlar, hatalar = super()._kapi(agac, gorev)
        hatalar += self._girdi_hatalari(self._girdi_denetle(gorev))
        hatalar += [f"{x['id']}: doğrulayıcı test toplamadı veya modül yükleyemedi: {x['cikti_kuyrugu'][-400:]}"
                   for x in komutlar if x["exit_code"] == 0 and re.search(
                       r"(?im)^(?:ran 0 tests?\b|no tests?\b|collected 0 items\b)|modulenotfounderror",
                       x["cikti_kuyrugu"])]
        return degisen, ihlaller, komutlar, hatalar

    def ac(self, gorev_id, ek_deneme, gerekce):
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is not None:
            butce = kok_butce_durumu(self.calisma, plan, gorev_id)
            if butce["harcanan"] >= butce["sinir"]:
                raise ValueError("kümülatif token bütçesi doldu; deneme artırımı bütçeyi sıfırlamaz")
        bekleyen = {g["id"] for g in plan["gorevler"] if g["durum"] == "girdi_bekliyor"}
        sonuc = super().ac(gorev_id, ek_deneme, gerekce)
        if bekleyen:
            plan = self._plan()
            for g in plan["gorevler"]:
                if g["id"] in bekleyen and (g["id"] != gorev_id or sonuc["durum"] == "girdi_bekliyor"):
                    g["durum"] = "girdi_bekliyor"
            self._kaydet_plan(plan)
        return sonuc

    def durum(self):
        sonuc = super().durum()
        sonuc["sorular"].extend(beklenen_girdiler(self, self._plan()))
        sonuc["uyarilar"] = [{"gorev": g["id"], "uyari": "kehanet: yok"}
                             for g in self._plan()["gorevler"]
                             if not kehanet_yolu(self.calisma, g["id"]).exists()]
        return sonuc

    def _izinli_yollar(self, plan, gorev):
        yollar, eksik = super()._izinli_yollar(plan, gorev)
        kehanetler = self.calisma / "plan" / "kehanetler"
        for yol in yollar:
            if kehanetler.is_relative_to(Path(yol)) or Path(yol).is_relative_to(kehanetler):
                raise ValueError("kehanet dizini işçi yazma kapsamına alınamaz")
        return yollar, eksik
