"""Karar köküne göre birleşen kuyruk ve yan etkisiz paket görünümü."""

import copy
import hashlib
import json
import os
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from orvant_op.yurutme import s4_kancasi
from orvant_op.bagimli_ciktilar import bekleme_ciktilari, istenen_ciktilar
from orvant_op.yurutme.zamanlayici import bagimli_kapanisi
from orvant_op.yurutme.karantina import kayitlar as karantina_kayitlari

BEKLEYEN = {"engelli", "ret", "girdi_bekliyor", "yetki_bekliyor", "karar_bekliyor",
            "inceleme_bekliyor"}


TUR_DURUMLARI = {
    # Karar sorusu görev durumundan bağımsızdır: karar açık kaldıkça cevabı değerlidir.
    "yetki": {"yetki_bekliyor"}, "girdi": {"girdi_bekliyor"},
    "yukselt": {"engelli", "ret"}, "kabul_celiskisi": {"engelli", "ret"},
    "orvant_kusuru": {"engelli", "ret"}, "geri_alma": {"kabul"},
    "inceleme": {"inceleme_bekliyor"},
}


def simdi():
    return datetime.now(timezone.utc).isoformat()


def satirlar(yol):
    return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines()
            if s.strip()] if yol.exists() else []


def atomik_yaz(yol, metin):
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=yol.parent,
                                         delete=False) as dosya:
            gecici = Path(dosya.name)
            dosya.write(metin)
        os.replace(gecici, yol)
    finally:
        if gecici is not None:
            gecici.unlink(missing_ok=True)


def kok_bul(s):
    tur = s["tur"]
    alan = {"karar": "karar_id", "yetki": "istek_id", "girdi": "girdi_karar_id",
            "orvant_kusuru": "teshis_imza"}.get(tur)
    if alan:
        anahtar = s.get(alan)
        if not anahtar:
            anahtar = " ".join(str(s.get("beklenen") or s.get("baslik") or s["soru"]).casefold().split())
        return f"{tur}:{anahtar}"
    anahtar = s.get("anahtar") or ("geri_alma" if tur == "geri_alma" else
              (s.get("teshis_imza") or "bekleme") + "|" + s.get("kanit_ozeti", ""))
    return f"{tur}:{s['gorev']}:{anahtar}"


def sure(acilis, bitis):
    if not acilis or not bitis:
        return 0.0
    def tarih(t):
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    return max(0.0, (tarih(bitis) - tarih(acilis)).total_seconds())


def kok_eslesir(eski, yeni):
    if eski["kok"] == yeni["kok"]:
        return True
    # Eski kayıtta kanıt özeti/beklenen metni yoksa kök tam türetilemez.
    # Eski hash'in girdisini güncel taslaktan doğrula; hash'i tahmin etme.
    if "anahtar" not in eski and "anahtar" in yeni:
        onceki = f"{yeni['gorev']}|{yeni['tur']}|{yeni['anahtar']}"
        return eski["id"] == "S-" + hashlib.sha256(onceki.encode()).hexdigest()[:10]
    return False


def tamamla(soru):
    """Eski kayıtları yalnız bellekte tamamlar; kimliği ve ilk zamanı değiştirmez."""
    s = copy.deepcopy(soru)
    s.setdefault("kok", kok_bul(s))
    s.setdefault("karar_kimligi", s.get("karar_id") or s.get("istek_id") or s.get("girdi_karar_id") or s["kok"])
    s.setdefault("dogrudan_gorevler", [s["gorev"]])
    s.setdefault("engellenen_gorevler", list(s["dogrudan_gorevler"]))
    s.setdefault("acilis_t", s.get("t"))
    s.setdefault("ilk_acilis_t", s["acilis_t"])
    s.setdefault("t", s["acilis_t"])
    s.setdefault("gorev_eklenme_t", {g: s["acilis_t"] for g in s["engellenen_gorevler"]})
    for alan in ("sozlesme_revizyon", "plan_surum", "oneri", "risk", "cevap", "cevap_t", "kapanis", "kapanis_t", "sure_sn"):
        s.setdefault(alan, None)
    s.setdefault("secenekler", [])
    s.setdefault("bilgi", s["tur"] == "orvant_kusuru")
    s.setdefault("yetkili", "orvant" if s["bilgi"] else "kullanici")
    s.setdefault("gecerlilik", None)
    s.setdefault("gorev_bekleme_sn", {})
    if s["durum"] != "acik":
        s["kapanis_t"] = s["kapanis_t"] or s["cevap_t"]
        s["sure_sn"] = sure(s["acilis_t"], s["kapanis_t"])
        s["gorev_bekleme_sn"] = {g: sure(t, s["kapanis_t"]) for g, t in s["gorev_eklenme_t"].items()}
    return s


def oz_ayni(eski, yeni):
    """Eski boş seçeneklerin zenginleştirilmesi sorunun özünü değiştirmez."""
    eski, yeni = tamamla(eski), tamamla(yeni)
    eski_kimlik = eski["karar_kimligi"]
    if eski_kimlik == eski["kok"] and kok_eslesir(eski, yeni):
        eski_kimlik = yeni["kok"]
    return (eski["tur"] == yeni["tur"] and eski_kimlik == yeni["karar_kimligi"]
            and eski["soru"] == yeni["soru"]
            and (not eski["secenekler"] or eski["secenekler"] == yeni["secenekler"]))


def birlestir(eski, yeni, zaman=None):
    """Aynı özde kimliği korur; farklı özde önceki kayda bağlı yeni taslak üretir."""
    eski, yeni = tamamla(eski), tamamla(yeni)
    sonuc = {**eski, **yeni}
    for k in ("id", "gorev", "t", "acilis_t", "ilk_acilis_t", "gorev_eklenme_t"):
        sonuc[k] = copy.deepcopy(eski[k])
    for k in ("dogrudan_gorevler", "engellenen_gorevler"):
        sonuc[k] = sorted(set(eski[k]) | set(yeni[k]))
    if sonuc["gecerlilik"] is not None:
        sonuc["gecerlilik"]["gorev_durumlari"] = {
            **(eski["gecerlilik"] or {}).get("gorev_durumlari", {}),
            **(yeni["gecerlilik"] or {}).get("gorev_durumlari", {})}
    for g in sonuc["engellenen_gorevler"]:
        sonuc["gorev_eklenme_t"].setdefault(
            g, yeni["gorev_eklenme_t"].get(g) or zaman or yeni["acilis_t"] or simdi())
    if not oz_ayni(eski, yeni):
        oz = json.dumps([yeni[k] for k in ("tur", "karar_kimligi", "soru", "secenekler")],
                        ensure_ascii=False, sort_keys=True)
        # Önceki kimlik, A → B → A dönüşünde kapalı bir kimliğin dirilmesini önler.
        anahtar = yeni["kok"] + "|" + oz + "|" + eski["id"]
        sonuc.update(id="S-" + hashlib.sha256(anahtar.encode()).hexdigest()[:10],
                     onceki_id=eski["id"], t=zaman, acilis_t=zaman)
    elif eski.get("onceki_id"):
        sonuc["onceki_id"] = eski["onceki_id"]
    if sonuc.get("hazir_komut") and sonuc["id"] != yeni["id"]:
        sonuc["hazir_komut"] = sonuc["hazir_komut"].replace(yeni["id"], sonuc["id"])
    return sonuc


def etki_guncelle(soru, plan):
    """Yalnız güncel etkiyi hesaplar; bilinmeyen revizyonu/geçerliliği doldurmaz."""
    s = tamamla(soru)
    if plan is None or s["durum"] != "acik":
        return s
    gs = {g["id"]: g for g in plan["gorevler"]}
    engellenen = set(s["dogrudan_gorevler"])
    for g in s["dogrudan_gorevler"]:
        engellenen.update(i for i in bagimli_kapanisi(plan, g) if gs[i]["durum"] != "kabul")
    s["engellenen_gorevler"] = sorted(engellenen)
    for g in engellenen:
        s["gorev_eklenme_t"].setdefault(g, s["ilk_acilis_t"])
    return s


def kaydi_kapat(soru, *, cevap=None, kapanis="kullanici_cevabi", yerine=None, kapanis_nedeni=None):
    soru = tamamla(soru)
    zaman = simdi()
    soru.update(durum="cevaplandi", cevap=cevap, cevap_t=zaman, kapanis_t=zaman, kapanis=kapanis)
    soru["sure_sn"], soru["gorev_bekleme_sn"] = olcum(soru, zaman)
    if kapanis_nedeni:
        soru["kapanis_nedeni"] = kapanis_nedeni
    if yerine:
        soru["yerine"] = yerine
    return soru


def gecerlik_baglami(s, plan, kararlar, yetkiler):
    durumlar = {g["id"]: g["durum"] for g in plan["gorevler"]}
    g = {"sozlesme_revizyon": plan.get("sozlesme_revizyon"),
         "gorev_durumlari": {i: durumlar.get(i) for i in s["dogrudan_gorevler"]}}
    karar = next((k for k in kararlar if k["id"] == (s.get("karar_id") or s.get("girdi_karar_id"))), None)
    if karar:
        g.update(karar_durumu=karar.get("durum"), karar_degeri=karar.get("deger"))
    if s.get("istek_id"):
        g["istek_durumu"] = next((y.get("durum") for y in yetkiler if y["id"] == s["istek_id"]), None)
    if s["tur"] == "orvant_kusuru":
        g["orvant_surumu"] = s.get("orvant_surumu")
    return g


def gecerli_bekleme(s, plan, kararlar, yetkiler):
    """Revizyondan bağımsız kök koşulu; birleşik kayıt tüm bekleyenleri kapsar."""
    gs = {g["id"]: g for g in plan["gorevler"]}
    dogrudan = [gs[i] for i in s["dogrudan_gorevler"] if i in gs]
    tur = s["tur"]
    if tur == "orvant_kusuru" and plan.get("orvant_surumu", s.get("orvant_surumu")) != s.get("orvant_surumu"):
        return False, "Orvant sürümü değişti"
    if tur == "karantina":
        etkin = [plan.get("karantina", {}).get(g["id"], {}) for g in dogrudan]
        return any(k.get("durum") == "aktif" and
                   (not s.get("anahtar") or s["anahtar"] == k["tetik"] + "|" + k["t"])
                   for k in etkin), "Karantina kaldırıldı"
    if tur == "geri_alma":
        return any(g["durum"] == "kabul" for g in dogrudan), "Kabul geri alma koşulu"
    if tur == "cikti_konumu":
        return any(not g.get("yazilabilir") for g in dogrudan), "Çıktı konumu belirlendi"
    if not any(g["durum"] in BEKLEYEN for g in dogrudan):
        return False, "Doğrudan görevlerin hiçbiri artık beklemiyor"
    uyumlu = TUR_DURUMLARI.get(tur)
    if uyumlu and not any(g["durum"] in uyumlu for g in dogrudan):
        return False, f"Bekleme türü değişti: {tur} → " + ", ".join(sorted({g["durum"] for g in dogrudan}))
    if tur == "karar":
        k = next((k for k in kararlar if k["id"] == s.get("karar_id")), None)
        if not k or k.get("durum") == "cozuldu":
            return False, "Karar çözüldü veya kaldırıldı"
    if tur == "yetki" and s.get("istek_id"):
        if not any(y["id"] == s["istek_id"] and y.get("durum") == "acik" for y in yetkiler):
            return False, "Yetki isteği artık açık değil"
    if tur == "girdi" and s.get("girdi_karar_id"):
        k = next((k for k in kararlar if k["id"] == s["girdi_karar_id"]), None)
        if not k:
            return False, "Girdi kararı kaldırıldı"
        # Uygunsuz dosya için zaten çözülmüş kararın yeni değeri beklenebilir.
        once = s["gecerlilik"] or {}
        if k.get("durum") == "cozuldu" and (("karar_durumu" in once and once["karar_durumu"] != "cozuldu") or
                ("karar_degeri" in once and once["karar_degeri"] != k.get("deger"))):
            return False, "Girdi kararı çözüldü"
    return True, "Kök belirsizlik sürüyor"


def gecerlilik_denetle(soru, plan, kararlar, yetkiler):
    """Saf denetim: güncel sürüm gerekiyorsa çağıran plan bağlamına ekler."""
    s = tamamla(soru)
    uygun, neden = gecerli_bekleme(s, plan, kararlar, yetkiler)
    if not uygun:
        return uygun, neden
    if s["sozlesme_revizyon"] is None or s["sozlesme_revizyon"] != plan.get("sozlesme_revizyon"):
        return False, "Sözleşme revizyonu yeniden doğrulanmalı"
    durumlar = {g["id"]: g["durum"] for g in plan["gorevler"]}
    if s["gecerlilik"] is None:
        return False, "Geçerlilik yeniden doğrulanmalı"
    if any(durumlar.get(g) != d for g, d in s["gecerlilik"].get("gorev_durumlari", {}).items()):
        return False, "Görev durumları yeniden doğrulanmalı"
    guncel = gecerlik_baglami(s, plan, kararlar, yetkiler)
    if any(s["gecerlilik"].get(k) != guncel.get(k) for k in
           ("karar_durumu", "istek_durumu", "karar_degeri")):
        return False, "Kök durumu yeniden doğrulanmalı"
    return True, neden


def paketle(sorular):
    acik = [tamamla(s) for s in sorular if s["durum"] == "acik"]
    kullanici = [s for s in acik if not s["bilgi"]]
    sira = lambda s: (-len(s["engellenen_gorevler"]), s["acilis_t"] or "", s["id"])
    bagimsiz, bagimli = [], []
    for s in sorted(kullanici, key=sira):
        once = sorted({b["id"] for b in kullanici if b["kok"] != s["kok"] and
                       set(s["dogrudan_gorevler"]) & (set(b["engellenen_gorevler"]) - set(b["dogrudan_gorevler"]))})
        if once:
            bagimli.append((s, once))
        else:
            bagimsiz.append(s)
    return [bagimsiz[i:i + 5] for i in range(0, len(bagimsiz), 5)], bagimli, sorted(
        [s for s in acik if s["bilgi"]], key=sira)


def olcum(s, zaman=None):
    bitis = s.get("kapanis_t") or zaman or simdi()
    return sure(s.get("acilis_t"), bitis), {g: sure(t, bitis) for g, t in s["gorev_eklenme_t"].items()}


def paket_metni(sorular, *, baslik="Kullanıcı soruları", zaman=None):
    zaman = zaman or simdi()
    sorular = [tamamla(s) for s in sorular]
    paketler, bagimli, bilgi = paketle(sorular)
    bagimsiz = sum(map(len, paketler))
    satir = [f"# {baslik} — açık kullanıcı sorusu: {bagimsiz + len(bagimli)} · bağımsız: {bagimsiz} · bağımlı: {len(bagimli)} · bilgi kaydı: {len(bilgi)}", "",
             "Sessizlik onay değildir; cevaplanmayan soru varsayılanla uygulanmaz.", ""]
    def blok(s, once=()):
        sn, _ = olcum(s, zaman)
        satir.extend([f"### {s['id']} · {s['tur']} · {s['karar_kimligi']}", "",
                      f"Engellediği görevler ({len(s['engellenen_gorevler'])}): {', '.join(s['engellenen_gorevler'])}",
                      f"Açık kalma süresi: {sn:.0f} sn", "", s["soru"]])
        if once:
            satir.append("Öncelik: önce " + ", ".join(once))
        if s["secenekler"]:
            satir.extend(["", "Seçenekler:", "", *[f"- {x}" for x in s["secenekler"]]])
        satir.extend(["", f"Öneri: {s['oneri'] or '—'}", f"Risk: {s['risk'] or '—'}",
                      f"Geçerlilik (sözleşme rev.): {s['sozlesme_revizyon']} · {json.dumps(s['gecerlilik'], ensure_ascii=False)}"])
        if s.get("kullanici_eylemi"):
            satir.append(s["kullanici_eylemi"])
        if s.get("hazir_komut"):
            satir.extend(["", s["hazir_komut"]])
        satir.append("")
    for i, paket in enumerate(paketler, 1):
        satir.extend([f"## Paket {i}", ""])
        for s in paket:
            blok(s)
    if bagimli:
        satir.extend(["## Sonra sorulacaklar (bağımlı)", ""])
        for s, once in bagimli:
            blok(s, once)
    if bilgi:
        satir.extend(["## Bilgi", "", "Orvant düzeltmesi bekleniyor", ""])
        for s in bilgi:
            blok(s)
    satir.extend(["## Kapanan sorular", ""])
    for s in sorted([s for s in sorular if s["durum"] != "acik"], key=lambda s: s["kapanis_t"] or "", reverse=True)[:10]:
        sn, bekleme = olcum(s, zaman)
        satir.append(f"- {s['id']}: {s['acilis_t']} → {s['kapanis_t']}; {sn:.0f} sn; {s['kapanis']}; görev-bekleme toplamı: {sum(bekleme.values()):.0f} sn")
    return "\n".join(satir) + "\n"


class SoruKuyrugu:
    def __init__(self, calisma):
        self.calisma = Path(calisma).resolve()
        self.yol = self.calisma / "operator/sorular.jsonl"

    def oku(self):
        return self.etkileri_guncelle(satirlar(self.yol))

    def etkileri_guncelle(self, sorular):
        yol = self.calisma / "plan/plan.json"
        plan = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else None
        return [etki_guncelle(s, plan) for s in sorular]

    def acik(self):
        return [s for s in self.oku() if s["durum"] == "acik"]

    def taslak(self, gorev, tur, anahtar, soru, neden, hazir_komut=None, **baglam):
        s = {"tur": tur, "gorev": gorev, "anahtar": anahtar, "soru": soru, "neden": neden,
             "hazir_komut": hazir_komut, "durum": "acik", **baglam}
        s["kok"] = kok_bul(s)
        s["id"] = "S-" + hashlib.sha256(s["kok"].encode()).hexdigest()[:10]
        if tur in ("karar", "girdi"):
            s["hazir_komut"] = (f"python3 -m orvant_op.operator cevapla {shlex.quote(str(self.calisma))} "
                                 f'{s["id"]} "<cevabınız>"')
        return tamamla(s)

    def baglamla(self, soru, plan, kararlar, yetkiler):
        s = etki_guncelle(soru, plan)
        s.update(sozlesme_revizyon=plan.get("sozlesme_revizyon"),
                 plan_surum=plan.get("surum"))
        s["gecerlilik"] = gecerlik_baglami(s, plan, kararlar, yetkiler)
        return s

    def yaz(self, sorular):
        sorular = self.etkileri_guncelle(sorular)
        atomik_yaz(self.yol, "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sorular))
        atomik_yaz(self.yol.with_suffix(".md"), paket_metni(sorular))

    def ekle(self, soru):
        soru = tamamla(soru)
        sorular = self.oku()
        eski = next((s for s in sorular if kok_eslesir(s, soru) and s["durum"] == "acik"), None)
        if eski:
            yeni = birlestir(eski, soru)
            yeni = self.etkileri_guncelle([yeni])[0]
            if eski == yeni:
                return eski, False
            if yeni["id"] != eski["id"]:
                sorular[sorular.index(eski)] = kaydi_kapat(eski, kapanis="yerine_gecti", yerine=yeni["id"])
                sorular.append(yeni)
            else:
                sorular[sorular.index(eski)] = yeni
        else:
            yeni = tamamla(soru)
            kapali = next((s for s in reversed(sorular) if kok_eslesir(s, yeni)
                           and s.get("kapanis") == "tur_degisti"), None)
            if kapali:
                # Tür geri dönünce geçmişte kapanmış kimliği yeniden kullanma.
                eski_id = yeni["id"]
                yeni.update(onceki_id=kapali["id"], id="S-" + hashlib.sha256(
                    (kapali["id"] + "|" + kapali["kapanis_t"]).encode()).hexdigest()[:10])
                if yeni.get("hazir_komut"):
                    yeni["hazir_komut"] = yeni["hazir_komut"].replace(eski_id, yeni["id"])
            sorular.append(yeni)
        if not yeni["acilis_t"]:
            yeni["t"] = yeni["acilis_t"] = yeni["acilis_t"] or simdi()
        yeni["ilk_acilis_t"] = yeni["ilk_acilis_t"] or yeni["acilis_t"]
        for g in yeni["engellenen_gorevler"]:
            yeni["gorev_eklenme_t"][g] = yeni["gorev_eklenme_t"].get(g) or yeni["acilis_t"]
        self.yaz(sorular)
        return yeni, True

    def kapat(self, kimlik, *, cevap=None, kapanis="kullanici_cevabi", yerine=None, kapanis_nedeni=None):
        sorular = self.oku()
        soru = next(s for s in sorular if s["id"] == kimlik and s["durum"] == "acik")
        yeni = kaydi_kapat(soru, cevap=cevap, kapanis=kapanis, yerine=yerine, kapanis_nedeni=kapanis_nedeni)
        sorular[sorular.index(soru)] = yeni
        self.yaz(sorular)
        return yeni

    def kapanacaklar(self, plan, kararlar):
        plan = {**plan, "orvant_surumu": s4_kancasi.orvant_surumu(),
                "karantina": karantina_kayitlari(self.calisma)}
        yol = self.calisma / "yurutme/yeniden_denetim.json"
        isaretler = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
        kapanacak = []
        gorevler = {g["id"]: g for g in plan["gorevler"]}
        for s in self.acik():
            uygun, neden = gecerli_bekleme(s, plan, kararlar, plan.get("yetki_istekleri", []))
            if uygun and s["tur"] == "girdi" and all(
                    g in gorevler and bekleme_ciktilari(self.calisma, plan, gorevler[g])
                    and istenen_ciktilar(self.calisma, plan, gorevler[g], s.get("beklenen") or s["soru"])
                    for g in s["dogrudan_gorevler"]):
                uygun, neden = False, "İstenen girdi kabul edilmiş bağımlı çıktı olarak mevcut"
            if s["tur"] == "geri_alma" and isaretler.get(s["gorev"], {}).get("durum") != "geri_alma_adayi":
                uygun, neden = False, "Geri alma koşulu kalktı"
            if not uygun:
                kapanacak.append({**s, "kapanis": ("surum_degisti" if neden == "Orvant sürümü değişti" else
                                   "karantina_kaldirildi" if s["tur"] == "karantina" else
                                   "tur_degisti" if neden.startswith("Bekleme türü değişti:") else "durum_degisti"),
                                   "kapanis_nedeni": neden})
        return kapanacak
