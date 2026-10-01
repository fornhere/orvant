"""Kilit altında deneme rezervasyonu, uzlaştırma ve sahiplik koruması."""
import json
import os
import uuid
from datetime import datetime, timezone

from orvant_op.butce import (aktif_rezervasyonlar, butce_tabani, gorev_envanteri, harcamalar,
                             kok_butce_durumu, satirlar)
from .akis import _ekle, _json_yaz


class EskiDeneme(Exception):
    """S4 hatası değildir; denemenin yazma yetkisi geri alınmıştır."""


class DenemeMixin:
    def _jetonlar(self):
        yol = self.kok / "jetonlar.json"
        return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}

    def _jeton_gecersiz_kil(self, kimlikler, neden):
        with self._kilit():
            jetonlar = self._jetonlar()
            for kimlik in sorted(kimlikler):
                kayit = jetonlar.get(kimlik)
                if kayit and kayit["gecerli"]:
                    kayit.update(gecerli=False, gecersiz_kilma_nedeni=neden)
                    self._olay("jeton_gecersiz_kilindi", kimlik, jeton=kayit["jeton"], neden=neden)
            if jetonlar:
                _json_yaz(self.kok / "jetonlar.json", jetonlar)

    def _jeton_dogrula(self, gorev_id, jeton=None):
        with self._kilit():
            deneme = getattr(self, "_deneme", None)
            jeton = jeton or (deneme["jeton"] if deneme else None)
            if jeton is None:  # Eski, jetonsuz makbuzlarla kapı geriye uyumludur.
                return
            kayit = self._jetonlar().get(gorev_id, {})
            if kayit.get("jeton") != jeton or not kayit.get("gecerli"):
                raise EskiDeneme(kayit.get("gecersiz_kilma_nedeni") or "Yeni deneme sahipliği devraldı")

    def _deftere_yaz(self, tur, **veri):
        _ekle(self.kok / "butce_defteri.jsonl", {
            "tur": tur, "pid": os.getpid(), "t": datetime.now(timezone.utc).isoformat(), **veri})

    def _deneme_hazirla(self, plan, gorev):
        with self._kilit():
            # Seçim ile başlatma arasındaki başka süreç yazımını yeniden oku.
            yeni = self._plan()
            guncel = next(g for g in yeni["gorevler"] if g["id"] == gorev["id"])
            neden = self._bekletilenler(yeni).get(gorev["id"])
            if neden or guncel["durum"] != "hazir":
                return {"gorev": gorev["id"], "durum": guncel["durum"],
                        "bekletildi": neden or "Görev artık hazır değil"}
            if any(r["gorev"] == gorev["id"] for r in aktif_rezervasyonlar(self.calisma)):
                # İptal edilen canlı işçi aynı worktree'ye hâlâ yazıyor olabilir.
                return {"gorev": gorev["id"], "durum": guncel["durum"],
                        "bekletildi": "Önceki işçinin rezervasyonu kapanmadı"}
            gorev.clear()
            gorev.update(guncel)
            yeni["gorevler"] = [gorev if g["id"] == gorev["id"] else g for g in yeni["gorevler"]]
            plan.clear()
            plan.update(yeni)
            deneme = len(self._makbuzlar(gorev)) + 1
            if deneme > gorev["butce"]["deneme"]:
                raise RuntimeError("deneme bütçesi geçersiz")
            durum = kok_butce_durumu(self.calisma, plan, gorev["id"])
            defter = satirlar(self.kok / "butce_defteri.jsonl")
            if not any(k["tur"] == "kok_acildi" and k["kok"] == durum["kok"] for k in defter):
                kok = next(g for g in plan["gorevler"] if g["id"] == durum["kok"])
                self._deftere_yaz("kok_acildi", id=uuid.uuid4().hex, gorev=kok["id"],
                    kok=kok["id"], token=durum["sinir"], jeton=None,
                    birim=durum["sinir"] // max(1, kok["butce"]["deneme"]),
                    plan_kaydi=len(satirlar(self.calisma / "plan/yeniden_planlar.jsonl")))
            if durum["kalan"] <= 0:
                neden = (f"Kök token bütçesi doldu ({durum['harcanan']}/{durum['sinir']}); "
                         "bütçe yalnız yeniden planlamada butce_artir ile artar")
                gorev["durum"] = "engelli"
                self._kaydet_plan(plan)
                self._engel(gorev, neden)
                self._olay("kok_butce_doldu", gorev["id"], **durum)
                return {"gorev": gorev["id"], "durum": "engelli", "gerekce": neden}
            token = min(max(gorev["butce"]["token"], butce_tabani(
                gorev=gorev, envanter=gorev_envanteri(self.calisma, gorev))), durum["kalan"])
            genel_yol = self.kok / "butce_siniri.json"
            if genel_yol.exists():
                genel = json.loads(genel_yol.read_text(encoding="utf-8"))["toplam_token"]
                kalan = genel - sum(harcamalar(self.calisma).values()) - sum(
                    r["token"] for r in aktif_rezervasyonlar(self.calisma))
                if kalan <= 0:
                    self._olay("genel_butce_doldu", gorev["id"], sinir=genel)
                    return {"gorev": gorev["id"], "durum": "hazir", "bekletildi": "genel_butce_doldu"}
                token = min(token, kalan)
            jeton = uuid.uuid4().hex
            self._deneme = {"id": uuid.uuid4().hex, "gorev": gorev["id"], "kok": durum["kok"],
                            "token": token, "jeton": jeton, "deneme": deneme}
            self._deneme["kapi_tabani"] = self._kapi_tabani(gorev["id"])
            jetonlar = self._jetonlar()
            jetonlar[gorev["id"]] = {"jeton": jeton, "deneme": deneme,
                                    "t": datetime.now(timezone.utc).isoformat(), "gecerli": True}
            _json_yaz(self.kok / "jetonlar.json", jetonlar)
            self._deftere_yaz("rezervasyon", **self._deneme)
            gorev["durum"] = "kosuyor"
            self._kaydet_plan(plan)
            self._grup_baslat()
            self._olay("gorev_denemesi_basladi", gorev["id"], deneme=deneme, rezervasyon=token)
        return None

    def _deneme_istemi(self, istem):
        deneme = getattr(self, "_deneme", None)
        if deneme:
            import re
            istem = re.sub(r"Token bütçesi: \d+", f"Token bütçesi: {deneme['token']}", istem)
        return istem

    def _isci_sonrasi(self, gorev, deneme, kosu, goal):
        self._deneme["isci"] = {"thread_id": kosu["thread_id"], "goal": goal,
                                "isci_ozeti": kosu["son_mesaj"]}
        self._jeton_dogrula(gorev["id"])

    def _birlestirme_oncesi(self, gorev, makbuz):
        veri = json.loads(makbuz.read_text(encoding="utf-8"))
        self._jeton_dogrula(gorev["id"], veri.get("jeton"))
        super()._birlestirme_oncesi(gorev, makbuz)

    def _uzlastir(self):
        with self._kilit():
            deneme = getattr(self, "_deneme", None)
            if not deneme or deneme.get("kapandi"):
                return
            yol = self.kok / "makbuzlar" / f"{deneme['gorev']}-{deneme['deneme']}.json"
            veri = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
            self._deftere_yaz("uzlastirma", **{k: deneme[k] for k in ("id", "gorev", "kok", "jeton")},
                             token=max(0, (veri.get("goal") or {}).get("tokens_used") or 0))
            deneme["kapandi"] = True

    def _eski_deneme_reddet(self, gorev, neden):
        deneme = self._deneme
        yol = self.kok / "makbuzlar" / f"{gorev['id']}-{deneme['deneme']}.json"
        alanlar = {"karar": "eski_deneme_reddedildi", "jeton": deneme["jeton"],
                   "gecersiz_kilma_nedeni": str(neden)}
        if yol.exists():
            veri = json.loads(yol.read_text(encoding="utf-8"))
            _json_yaz(yol, {**veri, **alanlar})
        else:
            self._makbuz(gorev, deneme["deneme"], **deneme.get("isci", {}), **alanlar)
        self._olay("eski_deneme_reddedildi", gorev["id"], makbuz=str(yol), neden=str(neden))
        self._iz("dogrulama", str(neden), sonuc="ret", gorev=gorev["id"], kanit=[str(yol)])
        return {"gorev": gorev["id"], "durum": "eski_deneme_reddedildi", "makbuz": str(yol)}

    def _gorev_yurut(self, plan, gorev, *, izin_yollari=()):
        eski_sahip, eski_deneme = self._sahip, getattr(self, "_deneme", None)
        self._sahip, self._deneme = gorev["id"], None
        bitis = {}
        try:
            try:
                sonuc = super()._gorev_yurut(plan, gorev, izin_yollari=izin_yollari)
            except EskiDeneme as exc:
                sonuc = self._eski_deneme_reddet(gorev, exc)
            except Exception:
                # İptal edilen işçi hata atsa da iptal/geri alma durumunu ezemez.
                if not self._deneme:
                    raise
                try:
                    self._jeton_dogrula(gorev["id"])
                except EskiDeneme as exc:
                    sonuc = self._eski_deneme_reddet(gorev, exc)
                else:
                    raise
            bitis["durum"] = sonuc.get("durum", gorev["durum"])
            return sonuc
        except BaseException as exc:
            bitis["istisna"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self._deneme:
                self._uzlastir()
                self._olay("gorev_denemesi_bitti", gorev["id"],
                           gorev_denemesi=self._deneme["deneme"], **bitis)
            self._sahip, self._deneme = eski_sahip, eski_deneme
