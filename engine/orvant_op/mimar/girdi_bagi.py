"""Plan şemasından bağımsız, açık görev–girdi bağları ve eski plan geçişi."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import uyum
from .kehanet import karar_yollari, kehanet_yolu, sozlesme_yolu, kabul_degisiklikleri

MEDYA_UZANTILARI = frozenset("mp4 mov mkv webm avi m4v mts m2ts mpg mpeg mp3 wav flac aac m4a ogg opus wma aiff aif ogv ts".split())


def karar_id_gecer(metin, karar_id):
    return bool(re.search(r"(?<![\w-])" + re.escape(karar_id) + r"(?![\w-])", metin))


def _oku(calisma):
    yol = Path(calisma) / "plan/girdi_baglari.json"
    return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {"surum": 1, "gorevler": {}}


def baglari_yaz(calisma, veri):
    yol = Path(calisma) / "plan/girdi_baglari.json"
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=yol.parent, delete=False) as fh:
            gecici = Path(fh.name)
            json.dump(veri, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        uyum.degistir(gecici, yol)
    finally:
        if gecici is not None:
            gecici.unlink(missing_ok=True)


def gorev_baglari(calisma, gorev_id):
    kayit = _oku(calisma)["gorevler"].get(gorev_id)
    return list(kayit["kararlar"]) if kayit is not None else None


def _metin(gorev):
    return " ".join(str(x or "") for x in [gorev.get("baslik"), gorev.get("amac"),
        *(k.get(a) for k in gorev.get("kabul", []) for a in ("beklenen", "komut", "rubrik"))])


def _kayit(kaynaklar):
    return {"kararlar": list(kaynaklar), "kaynaklar": kaynaklar,
            "t": datetime.now(timezone.utc).isoformat()}


def baglari_turet(plan, kararlar):
    gorevler = {}
    for g in plan["gorevler"]:
        kaynaklar = dict.fromkeys(g.get("bekleyen_kararlar", []), "plan_bekleyen_karar")
        for k in kararlar:
            if karar_id_gecer(_metin(g), k["id"]):
                kaynaklar.setdefault(k["id"], "plan_metni")
        gorevler[g["id"]] = _kayit(kaynaklar)
    return {"surum": 1, "gorevler": gorevler}


def sezgi_girdileri(calisma, gorev, kararlar):
    """Kayıtsız eski görevlerin medya dahil mevcut sezgisini tek yerde tutar."""
    betik, sozlesme = kehanet_yolu(calisma, gorev["id"]), sozlesme_yolu(calisma, gorev["id"])
    betik_metni = betik.read_text(encoding="utf-8") if betik.exists() else ""
    metin = " ".join((_metin(gorev), betik_metni,
                      sozlesme.read_text(encoding="utf-8") if sozlesme.exists() else ""))
    sonuc = []
    for k in kararlar:
        for yol in karar_yollari(calisma, k):
            medya = Path(yol).suffix.lower().lstrip(".") in MEDYA_UZANTILARI and not Path(yol).is_dir()
            if (k["id"] in gorev.get("bekleyen_kararlar", []) or karar_id_gecer(metin, k["id"])
                    or any(s in metin for s in (yol, Path(yol).name))
                    or (medya and "ORVANT_GIRDILER" in betik_metni)):
                sonuc.append((k["id"], k.get("baslik", ""), yol))
    return sonuc


def bag_ekle(calisma, gorev_id, karar_ids, kaynak):
    veri = _oku(calisma)
    if gorev_id not in veri["gorevler"]:
        kok = Path(calisma) / "plan"
        plan = json.loads((kok / "plan.json").read_text(encoding="utf-8"))
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        kararlar = json.loads((kok / "kararlar.json").read_text(encoding="utf-8"))
        # Girdi komutu planda henüz bulunmayan görev kimliğini de taşıyabilir.
        girdiler = sezgi_girdileri(calisma, gorev, kararlar) if gorev is not None else []
        tohum = dict.fromkeys((k for k, _, _ in girdiler), "sezgi_tohumu")
        veri["gorevler"][gorev_id] = _kayit(tohum)
    kaynaklar = dict(veri["gorevler"][gorev_id]["kaynaklar"])
    kaynaklar.update(dict.fromkeys(karar_ids, kaynak))
    veri["gorevler"][gorev_id] = _kayit(kaynaklar)
    baglari_yaz(calisma, veri)
    return veri["gorevler"][gorev_id]


def yeni_gorevleri_ekle(calisma, plan, kararlar, eski_gorev_ids):
    veri = _oku(calisma)
    ekler = {}
    for gid, kayit in baglari_turet(plan, kararlar)["gorevler"].items():
        if gid not in eski_gorev_ids and gid not in veri["gorevler"]:
            ekler[gid] = _kayit(dict.fromkeys(kayit["kararlar"], "yeniden_planlama"))
    if ekler:
        veri["gorevler"].update(ekler)
        baglari_yaz(calisma, veri)
    return ekler


def dayanak_kararlari(calisma, gorev, metin, cozulmus_kararlar):
    baglar = gorev_baglari(calisma, gorev["id"])
    sonuc = set(gorev.get("bekleyen_kararlar", []))
    if baglar is not None:
        sonuc.update(baglar)
        sonuc.update(k["onay_karar_id"] for k in kabul_degisiklikleri(calisma)
                     if k.get("gorev") == gorev["id"] and k.get("onay_karar_id"))
        if gorev.get("onay_karar_id"):
            sonuc.add(gorev["onay_karar_id"])
    else:
        for karar in cozulmus_kararlar:
            if (karar_id_gecer(metin, karar["id"]) or any(
                    yol in metin or Path(yol).name in metin for yol in karar_yollari(calisma, karar))):
                sonuc.add(karar["id"])
    return sonuc


def yeni_baglar(calisma, gorev_id, dayanak_kararlari):
    return [k for k in gorev_baglari(calisma, gorev_id) or [] if k not in dayanak_kararlari]
