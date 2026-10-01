"""Kanonik proje kimliği: ``orvant_gelisim/projeler.json`` takma ad tablosu (G-053)."""
from functools import lru_cache
import json
from pathlib import Path

TABLO = Path(__file__).resolve().with_name("projeler.json")


@lru_cache(maxsize=None)
def _eslemeler(yol=TABLO):
    try:
        veri = json.loads(Path(yol).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    esleme = {}
    for kanonik, kayit in (veri.get("projeler") or {}).items():
        esleme[kanonik] = kanonik
        for ad in kayit.get("takma_adlar") or ():
            if ad in esleme and esleme[ad] != kanonik:
                raise ValueError(f"takma ad iki projeye eşlenmiş: {ad}")
            esleme[ad] = kanonik
    return esleme


def kanonik(ad, yol=TABLO):
    """Bilinen takma adı kanonik ada çevir; tabloda olmayan ad olduğu gibi döner."""
    return _eslemeler(yol).get(ad, ad)


def adlar(ad, yol=TABLO):
    """Kanonik adı ve ona bağlı takma adları döndür."""
    asil = kanonik(ad, yol)
    return tuple(dict.fromkeys((asil, *(k for k, v in _eslemeler(yol).items() if v == asil))))


def iz_yolu(ad, yol=TABLO):
    """Tabloda açık iz yolu varsa döndür; göreli yolları çağıran köke bağlar."""
    try:
        veri = json.loads(Path(yol).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return veri.get("projeler", {}).get(kanonik(ad, yol), {}).get("iz")
