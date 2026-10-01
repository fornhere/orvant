"""Gerçek izden kalibre edilen görev bütçesi tabanı (mimar ve yürütücü ortak kullanır)."""
import json
import os
import re
from pathlib import Path


# Planlayıcının token bütçesi gerçek maliyetin altında kalabiliyor (S3 ilk koşu: 3000 bütçe,
# işçi dosya yazamadan budget_limited). Taban, gelişim izindeki başarılı işçi koşularının
# önbelleksiz token (goal tokens_used ile aynı ölçü) p90'ından hesaplanır.
VARSAYILAN_TABAN = 60000

# G-097: bekleme/çıktı okuma da goal token tüketir. Ölçülmüş süre tahmini değil,
# deterministik emniyet payı: ağır işte token tabanı ve süre sınırı iki katıdır.
AGIR_HESAP_CARPANI = 2
_AGIR_HESAP = re.compile(
    r"\b(?:transkript\w*|transkripsiyon\w*|asr|render\w*|(?:faster[-_])?whisper\w*)\b"
    r"|\bbüyük\s+(?:dosya\s+)?dönüştür\w*")

_ENVANTER_GENEL_KELIMELER = frozenset({
    "yoksa", "veya", "için", "olan", "olarak", "gerçek", "model", "modeli",
    "yerel", "kaynak", "üretmek", "bildirmek", "eksikliği", "içerik", "dosya", "erişimi",
})


def ilgili_envanter(gorev, envanter):
    """Görevle en az iki anlamlı sözcüğü eşleşen mevcut envanter kayıtları.

    Bütçe, istem ve süre aynı seçimi kullanır; ilgisiz bir araç tüm görevleri
    ağır saymaz. Türkçe ekler için beş harflik ortak sözcük başı yeterlidir.
    """
    kayitlar = envanter.get("kayitlar", []) if isinstance(envanter, dict) else envanter
    metin = " ".join(str(gorev.get(k) or "") for k in ("baslik", "amac"))
    metin += " " + " ".join(gorev.get("yazilabilir") or [])
    metin += " " + " ".join(str(kabul.get(k) or "")
                             for kabul in gorev.get("kabul", [])
                             for k in ("komut", "beklenen", "rubrik"))
    anahtarlar = {x.casefold() for x in re.findall(r"[^\W_]{3,}", metin, re.UNICODE)}
    anahtarlar -= _ENVANTER_GENEL_KELIMELER
    sonuc = []
    for kayit in kayitlar:
        if kayit.get("mevcut") is False:
            continue
        alanlar = " ".join(str(kayit.get(k) or "") for k in ("ad", "aciklama", "komut_ornegi"))
        kelimeler = {x.casefold() for x in re.findall(r"[^\W_]{3,}", alanlar, re.UNICODE)}
        puan = sum(any(a == b or (min(len(a), len(b)) >= 5 and
                                   (a.startswith(b) or b.startswith(a))) for b in kelimeler)
                   for a in anahtarlar)
        if puan >= 2:
            sonuc.append(kayit)
    return sonuc


def gorev_envanteri(calisma, gorev):
    """Kaydedilmiş envanterden yalnız görevin ilgili mevcut kayıtlarını okur."""
    yol = Path(calisma) / "plan" / "envanter.json"
    return ilgili_envanter(gorev, json.loads(yol.read_text(encoding="utf-8"))) if yol.exists() else []


def agir_hesap(gorev=None, envanter=()):
    """Başlık/amaç/yol/kabul veya verilen ilgili envanterde ağır iş varsa ×2.

    Türkçe büyük İ normalleştirilir; sözcük sınırı sayesinde `renderer` ağır,
    `prerendered`/`basra` değildir. Envanter yalnız ilgili mevcut kayıtları
    içermelidir; `mevcut: false` kayıtları sayılmaz. Bu sezgi, işi gerçekleştiren
    model veya donanım hakkında çıkarım yapmaz; anahtar sözcük yoksa ×1 döner.
    """
    gorev = gorev or {}
    veri = {k: gorev.get(k) for k in ("baslik", "amac", "yazilabilir", "kabul")}
    kayitlar = envanter.get("kayitlar", []) if isinstance(envanter, dict) else envanter
    veri["envanter"] = [{k: e.get(k) for k in ("ad", "aciklama", "komut_ornegi", "yol")}
                        for e in kayitlar if e.get("mevcut") is not False]
    metin = json.dumps(veri, ensure_ascii=False).casefold().replace("i\u0307", "i")
    return AGIR_HESAP_CARPANI if _AGIR_HESAP.search(metin) else 1


def hesap_zaman_asimi(gorev, zaman_asimi=3600, *, envanter=()):
    """İşçi süre bütçesi önerisi; çağıran yürütücü bu değeri timeout'a geçirmeli."""
    return zaman_asimi * agir_hesap(gorev, envanter)


def butce_tabani(iz_dizini=None, *, gorev=None, envanter=()):
    """İz p90 tabanı × ağır hesap çarpanı; parametresiz eski davranış korunur."""
    dizin = Path(iz_dizini or os.environ.get("ORVANT_IZ_DIZINI")
                 or Path(__file__).resolve().parents[1] / "orvant_gelisim" / "iz")
    degerler = []
    for yol in sorted(dizin.glob("*.jsonl")) if dizin.is_dir() else ():
        for satir in yol.read_text(encoding="utf-8").splitlines():
            try:
                olay = json.loads(satir)
            except json.JSONDecodeError:
                continue
            m = olay.get("maliyet") or {}
            if (olay.get("is_turu") == "isci_kosusu" and olay.get("sonuc") == "ok"
                    and not olay.get("miras") and m.get("girdi_token")):
                degerler.append(m["girdi_token"] - (m.get("onbellek_token") or 0)
                                + (m.get("cikti_token") or 0))
    if len(degerler) < 10:
        return VARSAYILAN_TABAN * agir_hesap(gorev, envanter)
    degerler.sort()
    return max(VARSAYILAN_TABAN, degerler[int(len(degerler) * 0.9)]) * agir_hesap(gorev, envanter)


def etkin_toplam_butce(gorev, taban=None, *, envanter=()):
    """Görevin yetkili kümülatif bütçesi: deneme başına etkin bütçe (plan ile taban'ın büyüğü) × deneme hakkı.

    Planlayıcının ham bütçesi tabanın altında kalabilir (S3: 1500 token); kümülatif denetimler bu fonksiyonu kullanır.
    """
    # Taban argümanı çarpan uygulanmamış temel tabandır; plan bütçesi tekrar çarpılmaz.
    tb = (butce_tabani(gorev=gorev, envanter=envanter) if taban is None
          else taban * agir_hesap(gorev, envanter))
    return max(gorev["butce"]["token"], tb) * max(1, gorev["butce"]["deneme"])


def satirlar(yol):
    """Yan kayıtları değiştirmeden okur; yazıcıyla eşzamanlı okuma kilit sahibinindir."""
    return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines()
            if s.strip()] if yol.exists() else []


def gorev_kokleri(calisma, plan):
    ebeveyn = {}
    for kayit in satirlar(Path(calisma) / "plan/yeniden_planlar.jsonl"):
        kaynak = (kayit.get("teshis_ref") or {}).get("gorev")
        if kaynak:
            for islem in kayit.get("islemler", []):
                if islem["islem"] == "gorev_ekle":
                    ebeveyn[islem["yeni_gorev"]["id"]] = kaynak
    def kok(kimlik):
        gorulen = set()
        while kimlik in ebeveyn:
            if kimlik in gorulen:
                raise ValueError("kök bütçe zinciri döngülü")
            gorulen.add(kimlik)
            kimlik = ebeveyn[kimlik]
        return kimlik
    return {g["id"]: kok(g["id"]) for g in plan["gorevler"]}


def harcamalar(calisma):
    toplam = {}
    for yol in (Path(calisma) / "yurutme/makbuzlar").glob("*.json"):
        veri = json.loads(yol.read_text(encoding="utf-8"))
        kimlik = veri.get("gorev")
        if kimlik and re.fullmatch(re.escape(kimlik) + r"-\d+\.json", yol.name):
            toplam[kimlik] = toplam.get(kimlik, 0) + max(
                0, (veri.get("goal") or {}).get("tokens_used") or 0)
    return toplam


def aktif_rezervasyonlar(calisma):
    """Ölü yerel PID rezervasyonu tutmaz; yaşayan PID için temkinli davranır.

    PID yeniden kullanılmışsa rezervasyon yaşayan sürece ait sayılır. Süre aşımıyla
    canlı işçinin bütçesini serbest bırakmak çift harcamaya yol açardı.
    """
    acik = {}
    for kayit in satirlar(Path(calisma) / "yurutme/butce_defteri.jsonl"):
        if kayit["tur"] == "rezervasyon":
            acik[kayit["id"]] = kayit
        elif kayit["tur"] == "uzlastirma":
            acik.pop(kayit["id"], None)
    sonuc = []
    for kayit in acik.values():
        pid = kayit.get("pid", 0)
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        sonuc.append(kayit)
    return sonuc


def kok_butce_durumu(calisma, plan, gorev_id):
    """Salt okuma; ilk rezervasyondan önce eski etkin bütçe davranışını korur.

    Açılıştan sonraki her butce_artir işlemi bir kez sayılır:
    ek_token + ek_deneme × kökün açılıştaki deneme başına etkin bütçesi.
    Böylece ac/serbest/geri_al veya değişen taban artış hesabını büyütemez.
    """
    calisma = Path(calisma)
    kokler = gorev_kokleri(calisma, plan)
    kok = kokler[gorev_id]
    gorev = next(g for g in plan["gorevler"] if g["id"] == kok)
    acilis = next((k for k in satirlar(calisma / "yurutme/butce_defteri.jsonl")
                   if k["tur"] == "kok_acildi" and k["kok"] == kok), None)
    sinir = acilis["token"] if acilis else etkin_toplam_butce(
        gorev, envanter=gorev_envanteri(calisma, gorev))
    if acilis:
        for kayit in satirlar(calisma / "plan/yeniden_planlar.jsonl")[acilis["plan_kaydi"]:]:
            for islem in kayit.get("islemler", []):
                if islem["islem"] == "butce_artir" and kokler.get(islem["gorev"]) == kok:
                    sinir += (islem.get("token") or 0) + (islem.get("deneme") or 0) * acilis["birim"]
    harcanan = sum(n for g, n in harcamalar(calisma).items() if kokler.get(g) == kok)
    aktif = sum(k["token"] for k in aktif_rezervasyonlar(calisma) if k["kok"] == kok)
    return {"kok": kok, "sinir": sinir, "harcanan": harcanan,
            "aktif_rezervasyon": aktif, "kalan": sinir - harcanan - aktif}
