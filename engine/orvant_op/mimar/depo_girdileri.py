"""Mevcut tablo gözlemini temiz main ağacındaki kaynak kimliğine bağlar."""
import subprocess
from pathlib import Path

from orvant_op.bagimli_ciktilar import guvenli_dosya
from orvant_op.karsilama.kaynaklar import DOSYA_SINIRI, depo_kaynaklari


def _git(depo, *komut):
    sonuc = subprocess.run(["git", "--literal-pathspecs", *komut], cwd=depo,
                           capture_output=True, check=False)
    if sonuc.returncode:
        raise ValueError("depo kaynak kimliği okunamadı")
    return sonuc.stdout


def _kimlik(depo):
    """Okuma öncesi yol güvenliği ve temiz ağacın tam blob kimlikleri."""
    if depo is None or not Path(depo).is_dir():
        return None, {}
    kok = Path(depo).expanduser()
    if kok.is_symlink():
        raise ValueError("depo kaynağı sembolik bağ içeriyor")
    kok = kok.resolve()
    konum = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=kok,
                           capture_output=True, check=False)
    if konum.returncode or Path(konum.stdout.decode().strip()).resolve() != kok:
        return None, {}
    # Yalnız index metaverisi: içerik gözlemci çağrısından önce hiçbir kaynak okunmaz.
    index = subprocess.run(["git", "ls-files", "--stage", "-z", "--", "*.csv", "*.tsv"],
                           cwd=kok, capture_output=True, check=False)
    if index.returncode:
        raise ValueError("depo kaynak listesi okunamadı")
    kayitlar = [r for r in index.stdout.decode("utf-8").split("\0") if r]
    if len(kayitlar) > DOSYA_SINIRI:
        raise ValueError("çok fazla tablo girdisi; kaynak kapsamını daraltın")
    kimlikler = {}
    for kayit in kayitlar:
        baslik, ad = kayit.split("\t", 1)
        kip, sha, asama = baslik.split()
        yol = kok / ad
        if any(p.is_symlink() for p in (yol, *yol.parents) if p.is_relative_to(kok)):
            raise ValueError(f"depo kaynağı sembolik bağ içeriyor: {ad}")
        if not guvenli_dosya(kok, ad) or kip not in ("100644", "100755") or asama != "0":
            raise ValueError(f"güvensiz depo kaynağı: {ad}")
        main = _git(kok, "ls-tree", "-z", "main", "--", ad).decode("utf-8").rstrip("\0")
        if main != f"{kip} blob {sha}\t{ad}":
            raise ValueError(f"depo kaynağı main/index kimliği değişti: {ad}")
        if _git(kok, "hash-object", "--no-filters", "--", ad).decode().strip() != sha:
            raise ValueError(f"depo kaynağı çalışma içeriği main ile uyuşmuyor: {ad}")
        kimlikler[ad] = sha
    main = _git(kok, "rev-parse", "main").decode().strip() if kimlikler else None
    return kok, {"main": main, "dosyalar": kimlikler} if kimlikler else {}


def kaynak_baglami(depo):
    """Keşif ve sınırlı CSV okumasını mevcut gözlemciden aynen alır."""
    kok, kimlik = _kimlik(depo)
    kaynaklar = depo_kaynaklari(kok)
    if {k["yol"] for k in kaynaklar} != set(kimlik.get("dosyalar", {})):
        raise ValueError("depo kaynak listesi gözlem sırasında değişti")
    veri = {"kaynak_icerikleri": kaynaklar, "depo_kaynak_kimligi": kimlik}
    baglam_dogrula(depo, veri)
    return veri


def baglam_dogrula(depo, veri):
    """Rol/temiz ağaç arasında değişen kaynak eski gözlemin dayanağı olamaz."""
    _, kimlik = _kimlik(depo)
    if kimlik != veri["depo_kaynak_kimligi"]:
        raise ValueError("depo kaynağı/main kimliği gözlemden sonra değişti")


def referans_bagi_dogrula(depo, veri):
    """Eski çıktı makbuzu kaynaklı depoda bugünün kimliğiyle doğrulanamaz."""
    _, kimlik = _kimlik(depo)
    if "depo_kaynak_kimligi" not in veri:
        if kimlik:
            raise ValueError("referansın depo kaynak kimliği yok; referansı yeniden üretin")
        return  # Kaynaksız eski çıktı makbuzuna kaynak kimliği eklenmez.
    if (not isinstance(veri["depo_kaynak_kimligi"], dict)
            or veri["depo_kaynak_kimligi"] != kimlik):
        raise ValueError("referansın depo kaynağı/main kimliği doğrulanamadı; referansı yeniden üretin")


def agac_dogrula(agac, veri):
    """Salt okunur klon, gözlemin alındığı main commit'inden açılmış olmalı."""
    main = veri["depo_kaynak_kimligi"].get("main")
    if main and _git(agac, "rev-parse", "HEAD").decode().strip() != main:
        raise ValueError("referans ağacı kaynak gözleminin main kimliğiyle uyuşmuyor")
