"""Kabulü gerekçeyle geri alır; main geçmişini ve kabul edilmiş bağımlıları korur."""
import json
from datetime import datetime, timezone

from .akis import _ekle, _json_yaz


class GeriAlMixin:
    def geri_al(self, gorev_id, gerekce):
        if not gerekce.strip():
            raise ValueError("geri alma gerekçesi gerekli")
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is None or gorev["durum"] != "kabul":
            raise ValueError("yalnız kabul edilmiş görev geri alınabilir")
        adaylar = [p for p in [*self._makbuzlar(gorev), *self._kapi_makbuzlari(gorev)]
                    if (m := json.loads(p.read_text(encoding="utf-8"))).get("karar") == "kabul"
                    and not m.get("geri_alindi")]
        if not adaylar:
            raise ValueError("geri alınacak kabul makbuzu bulunamadı")
        makbuz = max(adaylar, key=lambda p: p.stat().st_mtime_ns)
        kabul_veri = json.loads(makbuz.read_text(encoding="utf-8"))
        kabul_veri.update(geri_alindi=True, geri_alma_gerekcesi=gerekce)
        _json_yaz(makbuz, kabul_veri)
        for kabul in gorev["kabul"]:
            if kabul["tur"] == "insan_incelemesi":
                _ekle(self.kok / "incelemeler.jsonl", {
                    "gorev": gorev_id, "kabul_id": kabul["id"], "sonuc": "geri_alindi",
                    "not": gerekce, "t": datetime.now(timezone.utc).isoformat()})
        # Kabul makbuzu tarihte kalır; sonraki deneme yeni makbuz ve merge üretir.
        gorev["butce"]["deneme"] = max(gorev["butce"]["deneme"], len(self._makbuzlar(gorev)) + 1)
        gorev["durum"] = "hazir"
        etkilenmis = {gorev_id}
        while True:
            yeni = {g["id"] for g in plan["gorevler"] if set(g["bagimliliklar"]) & etkilenmis}
            if yeni <= etkilenmis:
                break
            etkilenmis |= yeni
        uyarilar = []
        for bagimli in plan["gorevler"]:
            if bagimli["id"] == gorev_id or bagimli["id"] not in etkilenmis:
                continue
            if bagimli["durum"] == "kabul":
                uyarilar.append(bagimli["id"])
                self._olay("kabul_edilmis_bagimli_uyarisi", bagimli["id"],
                           bagimlilik=gorev_id, gerekce=gerekce)
            elif bagimli["durum"] in ("hazir", "bekliyor", "karar_bekliyor", "yetki_bekliyor", "kosuyor"):
                bagimli["durum"] = "hazir"
        self._bagimlilari_ac(plan)
        self._kaydet_plan(plan)
        self._olay("kabul_geri_alindi", gorev_id, is_turu="dogrulama",
                   gerekce=gerekce, makbuz=str(makbuz), kabul_edilmis_bagimlilar=uyarilar)
        self._iz("dogrulama", "kabul geri alındı: " + gerekce, sonuc="ret",
                 ham={"gorev": gorev_id, "kabul_edilmis_bagimlilar": uyarilar}, kanit=[str(makbuz)], gorev=gorev_id)
        return {"gorev": gorev_id, "durum": gorev["durum"], "makbuz": str(makbuz),
                "kabul_edilmis_bagimlilar": uyarilar}
