"""Harici iş kuyrukları ve git worktree'leri için salt okunur koordinasyon."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import subprocess

from .ayarlar import koordinasyon_kaynaklari


def _alan(kayit, ad):
    deger = kayit
    for parca in ad.split("."):
        if not isinstance(deger, dict):
            return None
        deger = deger.get(parca)
    return deger


def _yol_normalize(deger):
    deger = deger.replace("\\", "/")
    while deger.startswith("./"):
        deger = deger.removeprefix("./")
    return deger


def _kaliplar(deger):
    if isinstance(deger, str):
        ham = deger.split(",")
    elif isinstance(deger, list):
        ham = [parca for parca in deger if isinstance(parca, str)]
    else:
        ham = []
    return [_yol_normalize(p.strip()) for p in ham if p.strip()]


def _kayitlar(kaynak):
    yol = kaynak["yol"]
    if kaynak["bicim"] == "jsonl":
        sonuc = []
        for no, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
            if satir.strip():
                deger = json.loads(satir)
                if not isinstance(deger, dict):
                    raise ValueError(f"{yol}:{no}: JSON nesnesi bekleniyor")
                sonuc.append(deger)
        return sonuc
    deger = json.loads(yol.read_text(encoding="utf-8"))
    if isinstance(deger, list):
        return deger
    if isinstance(deger, dict):
        listeler = [v for v in deger.values() if isinstance(v, list)]
        if len(listeler) == 1:
            return listeler[0]
    raise ValueError(f"{yol}: JSON kayıt listesi bulunamadı")


def _kesisir(kalip, dosya):
    dosya = _yol_normalize(dosya)
    kalip = kalip.rstrip("/")
    if any(c in kalip for c in "*?["):
        return fnmatch.fnmatchcase(dosya, kalip)
    return dosya == kalip or dosya.startswith(kalip + "/")


def cakismalari_bul(baslangic, gorev_id, dosyalar):
    """Etkin dış kayıtlarla kimlik ve dosya kapsamı çakışmalarını döndürür."""
    sonuc = []
    for kaynak in koordinasyon_kaynaklari(baslangic):
        for kayit in _kayitlar(kaynak):
            durum = _alan(kayit, kaynak["durum_alani"])
            if durum in kaynak["yok_sayilan_durumlar"] or durum not in kaynak["aktif_durumlar"]:
                continue
            kimlik = _alan(kayit, kaynak["id_alani"])
            nedenler = []
            if str(kimlik) == str(gorev_id):
                nedenler.append("id_eslesmesi")
            kaliplar = _kaliplar(_alan(kayit, kaynak["dosyalar_alani"]))
            if any(_kesisir(k, d) for k in kaliplar for d in dosyalar):
                nedenler.append("dosya_kesisimi")
            for neden in nedenler:
                sonuc.append({"kaynak": str(kaynak["yol"]), "kayit_id": kimlik,
                              "durum": durum, "neden": neden})
    return sonuc


def _git(kok, *args):
    sonuc = subprocess.run(["git", "-C", str(kok), *args], capture_output=True, text=True)
    if sonuc.returncode:
        raise ValueError(sonuc.stderr.strip() or "git komutu başarısız")
    return sonuc.stdout


def worktree_kesisimleri(kok, dosyalar):
    """Diğer worktree dallarında merge-base sonrası değişen kapsamı bilgi amaçlı bulur."""
    kok = Path(kok).resolve()
    bloklar = _git(kok, "worktree", "list", "--porcelain").strip().split("\n\n")
    sonuc = []
    for blok in bloklar:
        alanlar = {}
        for satir in blok.splitlines():
            anahtar, _, deger = satir.partition(" ")
            alanlar[anahtar] = deger
        yol = Path(alanlar.get("worktree", "")).resolve()
        head = alanlar.get("HEAD")
        if yol == kok or not head:
            continue
        taban = _git(kok, "merge-base", "HEAD", head).strip()
        degisen = _git(kok, "diff", "--name-only", taban, head).splitlines()
        ortak = sorted({d for d in degisen if any(_kesisir(k, d) or _kesisir(d, k) for k in dosyalar)})
        if ortak:
            sonuc.append({"worktree": str(yol), "dal": alanlar.get("branch", "detached"),
                          "dosyalar": ortak})
    return sonuc
