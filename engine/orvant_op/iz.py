"""Karşılama ve işçi olaylarını ortak gelişim izine yazar."""
import hashlib
import json
import math
import sys
import uuid
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

from orvant_gelisim import kayit
from orvant_gelisim.kayit import olay, olay_kokeni_ekle, yaz

_eski_surum_cozucu = kayit.orvant_surumu
KOK = Path(__file__).resolve().parents[1]
# iz → orvant_gelisim.kayit → orvant_gelisim.projeler çalışma zamanı içe aktarımları.
GELISIM_KODU = ("orvant_gelisim/__init__.py", "orvant_gelisim/kayit.py", "orvant_gelisim/projeler.py")
_kod_surumu_nedeni = None
_surum_nedeni = None
_ust_eylemler = {}
_iz_kilidi = threading.Lock()
_insan_suresi = ContextVar("insan_suresi", default=None)
_kosu = uuid.uuid4().hex


def kod_ozeti(kok):
    """Diskteki çalışma kodunu (kimlik, neden) olarak, git kullanmadan özetler."""
    try:
        kok = Path(kok)
        kaynak = kok / "orvant_op"
        if not kaynak.is_dir():
            raise FileNotFoundError(f"çalışma kodu dizini bulunamadı: {kaynak}")

        def okuma_hatasi(hata):
            raise hata

        yollar = []
        for dizin, altlar, dosyalar in os.walk(kaynak, onerror=okuma_hatasi):
            altlar[:] = [ad for ad in altlar if ad != "__pycache__"
                         and not (Path(dizin) == kaynak and ad == "tekrar")]
            for ad in dosyalar:
                if (ad.endswith((".py", ".json"))
                        and not (ad.startswith("test_") and ad.endswith(".py"))):
                    yollar.append((Path(dizin) / ad).relative_to(kok).as_posix())
        # Eski arşivlerde bulunmayan modüller yok sayılır; ekleme/silme özeti değiştirir.
        for ad in GELISIM_KODU:
            try:
                (kok / ad).stat()
            except FileNotFoundError:
                continue
            yollar.append(ad)
        ozet = hashlib.sha256()
        for ad in sorted(yollar):
            # Uzunluklar, yol/içerik sınırlarını içerikten bağımsız ve tek anlamlı tutar.
            for parca in (ad.encode("utf-8"), (kok / ad).read_bytes()):
                ozet.update(len(parca).to_bytes(8, "big"))
                ozet.update(parca)
        return "kod@" + ozet.hexdigest()[:12], None
    except OSError as exc:
        return "bilinmiyor", f"kod özeti okunamadı: {exc}"


@lru_cache(maxsize=1)
def kod_surumu(kok=None):
    """İlk kullanımda çözülen kod kimliği; kök parametresi yalıtılmış denetim içindir."""
    global _kod_surumu_nedeni
    _kod_surumu_nedeni = None
    gecersiz_kilma = os.environ.get("ORVANT_SURUMU")
    if gecersiz_kilma:
        return gecersiz_kilma
    surum, _kod_surumu_nedeni = kod_ozeti(KOK if kok is None else kok)
    return surum


@lru_cache(maxsize=1)
def _orvant_surumu():
    # Çalışan kod süreç başındaki sürümdür; sonraki commit'ler onu değiştirmez.
    global _surum_nedeni
    _surum_nedeni = None
    try:
        # Eski sürüm çözüm yüzeyini değiştiren çağıranlarla uyumu koru.
        if kayit.orvant_surumu is not _eski_surum_cozucu:
            surum = kayit.orvant_surumu()
            if not surum or surum == "bilinmiyor":
                _surum_nedeni = "sürüm çözücü sürüm bildirmedi"
            return surum or "bilinmiyor"
        surum, _surum_nedeni = kayit.surum_coz()
        if not surum or surum == "bilinmiyor":
            kod = kod_surumu()
            return kod if kod != "bilinmiyor" else "bilinmiyor"
        if (not os.environ.get("ORVANT_SURUMU")
                and re.fullmatch(r"(?:cekirdek@[0-9a-fA-F]+\+)?duzenek@[0-9a-fA-F]+", surum)):
            kod = kod_surumu()
            if kod == "bilinmiyor":
                _surum_nedeni = _kod_surumu_nedeni
                return "bilinmiyor"
            return f"{surum}+{kod}"
        return surum
    except Exception as exc:
        _surum_nedeni = f"sürüm çözücü başarısız: {exc}"
        kod = kod_surumu()
        return kod if kod != "bilinmiyor" else "bilinmiyor"


def _ust_eylem_sifirla():
    with _iz_kilidi:
        _ust_eylemler.clear()


def _gorev_bul(ham, kanit):
    for alan in ("gorev", "gorev_id", "task"):
        deger = ham.get(alan)
        if isinstance(deger, str) and deger.strip():
            return deger
    for kaynak in kanit:
        yol = Path(kaynak)
        esleme = re.fullmatch(r"(T\d+[a-z]?)-(?:kapi-)?\d+\.json", yol.name)
        if yol.parent.name == "makbuzlar" and esleme:
            return esleme[1]
    return None



def insan_suresi(dakika, acilis=None):
    """Beyan iste; duvar saati beklemesini aktif süreden ayrı hesapla."""
    if dakika is None and sys.stdin.isatty():
        try:
            print("Harcanan aktif süre (dakika, boş geçilebilir): ", end="", file=sys.stderr, flush=True)
            metin = input().strip()
        except EOFError:
            metin = ""
        if metin:
            try:
                dakika = float(metin)
                if not math.isfinite(dakika) or dakika < 0:
                    raise ValueError("geçersiz dakika")
            except ValueError:
                warnings.warn("Geçersiz dakika beyanı; süre bilinmiyor", RuntimeWarning)
                dakika = None
    if dakika is not None and (isinstance(dakika, bool) or not math.isfinite(dakika) or dakika < 0):
        raise ValueError("dakika sonlu ve negatif olmayan sayı olmalı")
    bekleme = None
    if acilis:
        try:
            an = datetime.fromisoformat(acilis.replace("Z", "+00:00")) if isinstance(acilis, str) else acilis
            if an.tzinfo is not None:
                fark = (datetime.now(timezone.utc) - an).total_seconds() / 60
                if fark >= 0:
                    bekleme = fark
        except (ValueError, TypeError, AttributeError):
            pass
    return dakika, bekleme


@contextmanager
def insan_suresi_baglami(dakika, bekleme, calisma=None, olay_turu=None):
    jeton = _insan_suresi.set((dakika, bekleme, calisma, olay_turu))
    try:
        yield
    finally:
        _insan_suresi.reset(jeton)


def insan_suresi_verisi():
    veri = _insan_suresi.get()
    return {"insan_dakika": veri[0], "bekleme_dakika": veri[1]} if veri else {}


def acilis_zamani(calisma, turler, gorev=None, soru=None):
    """Mevcut olay dosyalarında aynı görev/sorunun son açılışını bul."""
    adaylar = []
    for alt in ("yurutme", "plan", "oturum"):
        yol = Path(calisma) / alt / "olaylar.jsonl"
        if not yol.is_file():
            continue
        for e in kayit.oku(yol):
            veri = e.get("veri") or {}
            if (e.get("tur") in turler and (gorev is None or (e.get("gorev") or veri.get("gorev")) == gorev)
                    and (soru is None or soru in (veri.get("soru_id"), veri.get("istek_id"), veri.get("karar_id")))):
                if e.get("t"):
                    adaylar.append(e["t"])
    return max(adaylar, default=None)


def oturum_iz_yolu(calisma, iz_yolu=None):
    """Oturum sahibi kökü açıkça verir; işçi çalışma dizininden kök çıkarılmaz."""
    return iz_yolu or os.environ.get("ORVANT_IZ") or Path(calisma) / "iz.jsonl"


def calisma_kimligi(calisma, proje):
    """Makine yolunu sızdırmadan kalıcı, rastgele oturum kimliğini döndürür."""
    del proje  # Kimlik proje adından bağımsız ve küresel olarak ayırt edicidir.
    kimlik_yolu = Path(calisma) / ".orvant" / "oturum-kimligi"
    kimlik_yolu.parent.mkdir(parents=True, exist_ok=True)
    try:
        deger = kimlik_yolu.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        deger = uuid.uuid4().hex
        try:
            fd = os.open(kimlik_yolu, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            deger = kimlik_yolu.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(deger + "\n")
    try:
        if uuid.UUID(deger).hex != deger:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(f"geçersiz oturum kimliği: {kimlik_yolu}") from None
    return "oturum:" + deger


def _guvensiz_yol(yol, calisma, yasak_kokler):
    hedef = Path(yol).resolve()
    if any(hedef.parts[i:i+2] == (".orvant", "agac") for i in range(len(hedef.parts)-1)):
        return True
    kokler = list(yasak_kokler)
    if calisma is not None:
        plan = Path(calisma) / "plan/plan.json"
        if plan.is_file():
            try:
                depo = json.loads(plan.read_text(encoding="utf-8")).get("depo", {}).get("yol")
                if depo:
                    kokler.append(Path(calisma) / depo)
            except (OSError, ValueError, AttributeError, TypeError):
                warnings.warn("İz yazılamadı: depo kökü doğrulanamadı", RuntimeWarning)
                return True
    return any(hedef.is_relative_to(Path(kok).resolve()) for kok in kokler)


def kaydet(iz_yolu, proje, is_turu, *, aktor_tur="orvant", kimlik="karsilama",
           sonuc="ok", ozet="", kanit=(), maliyet=None, ham=None, gorev=None,
           onerdi=None, karar_verdi=None, uyguladi=None, mudahale_bolumu=None, calisma=None, kosu=None, yasak_kokler=()):
    sure = _insan_suresi.get()
    calisma = calisma or (sure[2] if sure else None)
    acik_yol = iz_yolu or os.environ.get("ORVANT_IZ")
    yol = acik_yol
    if not yol and calisma is not None:
        yol = Path(calisma) / "iz.jsonl"
    if not yol:
        warnings.warn("İz yazılamadı: proje kökü veya iz yolu bilinmiyor", RuntimeWarning)
        return None
    if not acik_yol and _guvensiz_yol(yol, calisma, yasak_kokler):
        warnings.warn("İz yazılamadı: hedef kullanıcı deposu veya işçi ağacı içinde", RuntimeWarning)
        return None
    ham = dict(ham or {})
    # ``kosu`` süreç kimliğidir; ayrı CLI süreçleri aynı proje oturumunda yeni
    # bir değer alır. Kalıcı oturum kimliğini ham kayıtta açıkça taşı.
    if calisma is not None:
        ham.setdefault("calisma", calisma_kimligi(calisma, proje))
    if sure and sure[3] == "kabul_olcutu_degisti" and ham.get("olay_turu") == sure[3]:
        aktor_tur = "kullanici"
        karar_verdi = "kullanici"
    maliyet = dict(maliyet or {})
    if sure and aktor_tur in {"kullanici", "opus"}:
        maliyet["insan_dakika"] = sure[0]
        ham["bekleme_dakika"] = sure[1]
    kosu = kosu or os.environ.get("ORVANT_KOSU") or _kosu
    kanit = list(kanit or ())
    if gorev is None:
        gorev = _gorev_bul(ham, kanit)
    if gorev is not None:
        ham["gorev"] = gorev
    with _iz_kilidi:
        surum = _orvant_surumu()
        if _surum_nedeni:
            ham["surum_nedeni"] = _surum_nedeni
        item = olay(proje=proje, is_turu=is_turu, aktor_tur=aktor_tur,
                    aktor_kimlik=kimlik, sonuc=sonuc, ozet=ozet, kosu=kosu,
                    kanit=kanit, maliyet=maliyet, ham=ham, orvant_surumu=surum,
                    onerdi=onerdi, karar_verdi=karar_verdi, uyguladi=uyguladi,
                    mudahale_bolumu=mudahale_bolumu)
        item = olay_kokeni_ekle(item, surum)
        # İnsan müdahalesini, yazıldığı anda aynı oturum/görevdeki açık S4
        # teşhisine bağla. Bağın kayıttan sonra türetilmesi üretim izini eksik
        # bırakır; şema değişmeden yalnız ham alanları eklenir.
        if item.get("mudahale"):
            from orvant_op.yurutme.s4_kancasi import mudahale_bagini_ekle
            item = mudahale_bagini_ekle(yol, item, calisma_yolu=calisma)
        anahtar = (str(Path(yol).resolve()), item["proje"])
        operator = aktor_tur == "orvant" and kimlik == "operator"
        if aktor_tur in {"orvant", "codex", "claude"} and kimlik != "operator":
            if anahtar in _ust_eylemler:
                item["ham"].setdefault("ust_olay", _ust_eylemler[anahtar])
        try:
            Path(yol).parent.mkdir(parents=True, exist_ok=True)
            yaz(Path(yol), item)
        except OSError as exc:
            warnings.warn(f"İz yazılamadı: {exc}", RuntimeWarning)
            raise
        # Yalnız başarıyla yazılmış üst olay sonraki alt eylemlere bağlanır.
        # Operatör olayı eylemden SONRA yazılırsa (ör. cevapla), önceki alt
        # olay bu operatör olayına bağlanamaz. Bağ yalnız bu süreçte geçerlidir.
        if operator:
            _ust_eylemler[anahtar] = item["id"]
    return item["id"]
