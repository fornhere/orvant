"""Karşılama ve işçi olaylarını ortak gelişim izine yazar."""
import hashlib
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

from orvant_gelisim import kayit
from orvant_gelisim.kayit import olay, yaz

_eski_surum_cozucu = kayit.orvant_surumu
KOK = Path(__file__).resolve().parents[1]
# iz → orvant_gelisim.kayit → orvant_gelisim.projeler çalışma zamanı içe aktarımları.
GELISIM_KODU = ("orvant_gelisim/__init__.py", "orvant_gelisim/kayit.py", "orvant_gelisim/projeler.py")
_kod_surumu_nedeni = None
_surum_nedeni = None
_ust_eylemler = {}
_iz_kilidi = threading.Lock()


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
        return "bilinmiyor"


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


def kaydet(iz_yolu, proje, is_turu, *, aktor_tur="orvant", kimlik="karsilama",
           sonuc="ok", ozet="", kanit=(), maliyet=None, ham=None, gorev=None,
           onerdi=None, karar_verdi=None, uyguladi=None, mudahale_bolumu=None):
    yol = iz_yolu or os.environ.get("ORVANT_IZ")
    if not yol:
        return None
    ham = dict(ham or {})
    kanit = list(kanit or ())
    if gorev is None:
        gorev = _gorev_bul(ham, kanit)
    if gorev is not None:
        ham["gorev"] = gorev
    with _iz_kilidi:
        surum = _orvant_surumu()
        if surum == "bilinmiyor":
            ham["surum_nedeni"] = _surum_nedeni
        item = olay(proje=proje, is_turu=is_turu, aktor_tur=aktor_tur,
                    aktor_kimlik=kimlik, sonuc=sonuc, ozet=ozet,
                    kanit=kanit, maliyet=maliyet, ham=ham, orvant_surumu=surum,
                    onerdi=onerdi, karar_verdi=karar_verdi, uyguladi=uyguladi,
                    mudahale_bolumu=mudahale_bolumu)
        anahtar = (str(Path(yol).resolve()), item["proje"])
        operator = aktor_tur == "orvant" and kimlik == "operator"
        if aktor_tur in {"orvant", "codex", "claude"} and kimlik != "operator":
            if anahtar in _ust_eylemler:
                item["ham"].setdefault("ust_olay", _ust_eylemler[anahtar])
        yaz(Path(yol), item)
        # Yalnız başarıyla yazılmış üst olay sonraki alt eylemlere bağlanır.
        # Operatör olayı eylemden SONRA yazılırsa (ör. cevapla), önceki alt
        # olay bu operatör olayına bağlanamaz. Bağ yalnız bu süreçte geçerlidir.
        if operator:
            _ust_eylemler[anahtar] = item["id"]
    return item["id"]
