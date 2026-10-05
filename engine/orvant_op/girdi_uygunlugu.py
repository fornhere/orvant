"""Kehanetten bağımsız, yerel medya girdisi uygunluğu ve ölçüm önbelleği."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import uyum
from orvant_op.mimar.kehanet import karar_yollari, SESSIZLIK_ESIGI_DB
from orvant_op.mimar.girdi_bagi import gorev_baglari, sezgi_girdileri, MEDYA_UZANTILARI
_KILIT = threading.RLock()


def _kimlik(yol):
    yol = Path(yol).expanduser().resolve()
    try:
        st = yol.stat()
        return str(yol), st.st_size, st.st_mtime_ns
    except OSError:
        return str(yol), None, None


def _hacim(metin, ad):
    eslesme = re.search(r"\b" + ad + r"_volume:\s*(-inf|[-+]?\d+(?:\.\d+)?)\s*dB", metin, re.I)
    if not eslesme:
        return None
    # JSON'da standart dışı Infinity yazmamak için sonsuzluk metin olarak saklanır.
    return "-inf" if eslesme[1].lower() == "-inf" else float(eslesme[1])


def olc(yol, *, zaman_asimi=120):
    """Ölçüm sesin gerekli olup olmadığına karar vermez; bunu görev belirler."""
    gercek, boyut, mtime = _kimlik(yol)
    sonuc = dict(yol=gercek, boyut=boyut, mtime_ns=mtime, durum="uygun",
                 ses_izi=None, sure_sn=None, max_volume_db=None, mean_volume_db=None,
                 nedenler=[], arac="ffprobe/ffmpeg")
    def bitir(durum, neden):
        sonuc.update(durum=durum, nedenler=[neden])
        return sonuc
    if Path(gercek).is_dir() or Path(gercek).suffix.lower().lstrip(".") not in MEDYA_UZANTILARI:
        return bitir("medya_degil", "medya_degil")
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        return bitir("atlandi", "ffmpeg/ffprobe yok")
    if boyut is None or boyut == 0:
        return bitir("uygun_degil", "okunamadı/bozuk: dosya yok veya boş")
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type", "-of", "json", gercek],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=zaman_asimi)
        if p.returncode:
            return bitir("uygun_degil", "okunamadı/bozuk: ffprobe başarısız")
        try:
            veri = json.loads(p.stdout)
            sonuc["ses_izi"] = any(s.get("codec_type") == "audio" for s in veri.get("streams", []))
            sonuc["sure_sn"] = float(veri.get("format", {}).get("duration", 0))
        except (ValueError, TypeError, AttributeError):
            return bitir("uygun_degil", "okunamadı/bozuk: süre yok")
        if not math.isfinite(sonuc["sure_sn"]) or sonuc["sure_sn"] <= 0:
            sonuc["sure_sn"] = None
            return bitir("uygun_degil", "süre yok")
        if sonuc["ses_izi"]:
            p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", gercek,
                                "-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=zaman_asimi)
            if p.returncode:
                return bitir("uygun_degil", "okunamadı/bozuk: ses çözülemedi")
            for ad in ("max", "mean"):
                sonuc[ad + "_volume_db"] = _hacim(p.stderr, ad)
            if sonuc["max_volume_db"] is None or sonuc["mean_volume_db"] is None:
                return bitir("hata", "ses ölçümü ayrıştırılamadı")
        return sonuc
    except subprocess.TimeoutExpired:
        return bitir("hata", "girdi ölçümü zaman aşımı")
    except FileNotFoundError:
        return bitir("atlandi", "ffmpeg/ffprobe yok")
    except OSError as exc:
        return bitir("hata", f"girdi ölçüm aracı çalışmadı: {exc}")


def _kararlar(calisma):
    yol = Path(calisma) / "plan/kararlar.json"
    return json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else []


def _gorev_metni(gorev, *, komut=False):
    return " ".join(str(x or "") for x in [gorev.get("baslik"), gorev.get("amac"),
        *(k.get(alan) for k in gorev.get("kabul", [])
          for alan in (("beklenen", "komut") if komut else ("beklenen",)))])


def gorev_girdileri(calisma, gorev):
    """Görevin açıkça atıf yaptığı çözülmüş karar yollarını döndürür."""
    baglar = gorev_baglari(calisma, gorev["id"])
    kararlar = _kararlar(calisma)
    if baglar is not None:
        return [(k["id"], k.get("baslik", ""), yol) for k in kararlar
                if k["id"] in baglar and k.get("durum") == "cozuldu"
                for yol in karar_yollari(calisma, k)]
    return sezgi_girdileri(calisma, gorev, kararlar)


def ses_gerekli(gorev, karar):
    metin = " ".join((_gorev_metni(gorev), str(karar.get("baslik", "")), str(karar.get("deger", ""))))
    metin = metin.casefold().replace("i\u0307", "i").replace("ı", "i")
    return bool(re.search(r"konuş|\bses(?:\b|li|i|siz|lendir)|transkript|altyaz|audio|speech|voice|asr|whisper|mikrofon", metin))


def denetle(calisma, gorev):
    """Ölçümü paylaşır; görev uygunluğunu her çağrıda yeniden değerlendirir."""
    with _KILIT:
        kayit = Path(calisma).resolve() / "plan/girdi_uygunlugu.json"
        veri = json.loads(kayit.read_text(encoding="utf-8")) if kayit.exists() else {"girdiler": {}, "gorevler": {}}
        veri["esik_db"] = SESSIZLIK_ESIGI_DB
        kararlar = {k["id"]: k for k in _kararlar(calisma)}
        girdiler = []
        for kid, baslik, yol in gorev_girdileri(calisma, gorev):
            kimlik = _kimlik(yol)
            onceki = veri["girdiler"].get(yol, {})
            # Geçici araç hataları önbellekten kalıcı hale gelmesin.
            if (tuple(onceki.get(k) for k in ("yol", "boyut", "mtime_ns")) == kimlik
                    and onceki.get("durum") not in ("hata", "atlandi")):
                olcum = onceki
            else:
                olcum = olc(yol)
            veri["girdiler"][yol] = olcum
            uygun = olcum["durum"] not in ("uygun_degil", "hata", "atlandi")
            nedenler = list(olcum["nedenler"])
            if olcum["durum"] == "uygun":
                gerekli = ses_gerekli(gorev, kararlar[kid])
                if not olcum["ses_izi"] and gerekli:
                    uygun = False
                    nedenler.append("ses izi yok")
                hacim = olcum["max_volume_db"]
                if hacim is not None and float(hacim) <= SESSIZLIK_ESIGI_DB:
                    nedenler.append(f"sessiz: max_volume={hacim} dB ≤ {SESSIZLIK_ESIGI_DB:g} dB")
                    if gerekli:
                        uygun = False
            girdiler.append(dict(karar_id=kid, baslik=baslik, yol=yol,
                                 uygun=None if olcum["durum"] in ("hata", "atlandi") else uygun,
                                 nedenler=nedenler, olcum=olcum))
        durum = ("girdi_yok" if not girdiler else
                 "uygun_degil" if any(g["uygun"] is False for g in girdiler) else
                 "atlandi" if any(g["uygun"] is None for g in girdiler) else "uygun")
        sonuc = dict(gorev=gorev["id"], durum=durum, girdiler=girdiler)
        if durum == "girdi_yok" and not kayit.exists():
            return sonuc
        veri["gorevler"][gorev["id"]] = {**sonuc, "t": datetime.now(timezone.utc).isoformat()}
        kayit.parent.mkdir(parents=True, exist_ok=True)
        gecici = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=kayit.parent,
                                             prefix=".girdi-", delete=False) as fh:
                gecici = Path(fh.name)
                json.dump(veri, fh, ensure_ascii=False, indent=2, allow_nan=False)
                fh.write("\n")
            uyum.degistir(gecici, kayit)
        finally:
            if gecici is not None:
                gecici.unlink(missing_ok=True)
        return sonuc
