"""Kabul edilmiş doğrudan bağımlılıkların salt okunur, depo içi dosyaları.

Çıktı beyanı yetki değildir. Mevcut dosya ve güvenli yol birlikte doğrulanır;
işçi metni yalnız hangi girdinin istendiğini seçer, dosya erişimi açmaz.
"""
import json
import re
from pathlib import Path, PureWindowsPath


def guvenli_dosya(depo, ad):
    if (not isinstance(ad, str) or not ad or Path(ad).is_absolute()
            or PureWindowsPath(ad).drive or "\\" in ad
            or any(c in ad for c in "\x00\n\r*?[]") or ".." in Path(ad).parts):
        return False
    try:
        kok = Path(depo).resolve()
        yol = kok / ad
        return yol.resolve().is_relative_to(kok) and yol.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


def bagimli_ciktilar(calisma, plan, gorev):
    """Yalnız mevcut kabul edilmiş dosyaların sözleşmelerini, içerik okumadan döndür."""
    from orvant_op.mimar.kehanet import sozlesme_yolu

    depo = plan.get("depo", {}).get("yol")
    if not depo or calisma is None:
        return []
    sonuc = []
    kok = Path(calisma).resolve()
    depo = Path(depo).expanduser()
    depo = depo if depo.is_absolute() else kok / depo
    for bagli in plan.get("gorevler", []):
        if bagli["id"] not in gorev.get("bagimliliklar", []) or bagli["durum"] != "kabul":
            continue
        try:
            yol = sozlesme_yolu(kok, bagli["id"])
            if not yol.resolve().is_relative_to(kok):
                continue
            sozlesme = json.loads(yol.read_text(encoding="utf-8"))
            dosyalar = [d for d in sozlesme.get("dosyalar", [])
                        if isinstance(d, dict) and guvenli_dosya(depo, d.get("yol"))]
        except (OSError, ValueError, RuntimeError, AttributeError, TypeError):
            continue
        if dosyalar:
            sonuc.append({"gorev": bagli["id"], "dosyalar": dosyalar})
    return sonuc


_ISTEK = re.compile(
    r"gerek|bekli|sağla|sagla|paylaş|paylas|input required|provide|"
    r"okuma izni|okunmasına izin|girdiler arasında değil|eksik|okunam|erişilem|missing|cannot read", re.I)
_YOL = re.compile(r"(?:[\w.~:/\\-]+/)?[\w.~-]+\.[A-Za-z0-9]{1,12}\b")


def istenen_ciktilar(calisma, plan, gorev, metin):
    """Açık dosya isteğini mevcut bağımlı çıktı ile eşleştir; belirsizde bekle.

    Eski T13 özeti yol vermeden 'mevcut transkript dosyasına' der. Bu dar
    ifade yalnız tek bir transkript çıktısı varsa çözümlenir. Başka bir dış
    dosya isteyen cümle varsa kısmi eşleşme beklemeyi kaldırmaz.
    """
    dosyalar = {d["yol"]: b["gorev"] for b in bagimli_ciktilar(calisma, plan, gorev)
                for d in b["dosyalar"]}
    bulunan = set()
    eski_transkript_istegi = False
    metin = str(metin or "").removeprefix("İşçi dış girdi beklediğini bildirdi: ")
    # Mevcut bir dosyadan söz edilmesi insan kararını karşılamaz.
    if re.search(r"\b(?:karar|seçim|onay|tercih|cevap)\w*\s+[^.;\n]{0,60}(?:gerek|bekl)", metin, re.I):
        return []
    for cumle in re.split(r"[;,\n]|(?<=[.!?])\s+|\b(?:ama|fakat|ancak)\b", metin, flags=re.I):
        if not _ISTEK.search(cumle):
            continue
        yollar = _YOL.findall(cumle)
        if yollar:
            # Tam depo göreli ad şartı: basename, mutlak yol ve ../ eşleşmez.
            if any(y not in dosyalar for y in yollar):
                return []
            bulunan.update(yollar)
        elif re.search(r"mevcut (?:zamanlı )?transkript dosyas|"
                       r"zamanlı transkript.*(?:okunmasına izin|girdiler arasında değil)",
                       cumle, re.I):
            adaylar = [y for y in dosyalar if Path(y).stem in ("transcript", "transkript")]
            if len(adaylar) != 1:
                return []
            bulunan.update(adaylar)
            eski_transkript_istegi = True
        elif eski_transkript_istegi and re.fullmatch(
                r"\s*ayrıca işlem günlüğü için yazılabilir bir yol gerekiyor\.?\s*", cumle, re.I):
            # Eski ASR yeniden üretim isteğinin günlüğü yeni dış girdi değildir.
            continue
        else:
            return []
    return [{"gorev": dosyalar[y], "yol": y} for y in sorted(bulunan)]


def bekleme_ciktilari(calisma, plan, gorev):
    """Son engel artık mevcut bağımlı çıktıdan karşılanabiliyor mu? Salt okuma."""
    if gorev.get("durum") != "girdi_bekliyor":
        return []
    yol = Path(calisma) / "yurutme/engeller.jsonl"
    if not yol.exists():
        return []
    son = next((e for s in reversed(yol.read_text(encoding="utf-8").splitlines())
                if s.strip() and (e := json.loads(s)).get("gorev") == gorev["id"]), None)
    return istenen_ciktilar(calisma, plan, gorev, son["neden"]) if son else []
