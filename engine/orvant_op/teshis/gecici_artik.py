"""Kapsam dışındaki küçük, izlenmeyen dosyaları kanıtla ve güvenle temizle."""

import os
import stat
import subprocess
from contextlib import contextmanager

BOYUT_SINIRI = 1_000_000


def _git_yollari(agac, *args):
    sonuc = subprocess.run(["git", "-C", str(agac), *args], capture_output=True)
    if sonuc.returncode:
        raise ValueError("Artık incelemesinde git bilgisi okunamadı")
    return {os.fsdecode(p) for p in sonuc.stdout.split(b"\0") if p}


@contextmanager
def _ust_dizin(agac, ad):
    # Kapı dizinleri dosyalara açarak bildirir. Alt yolları da reddetmek gerekir;
    # otomatik temizlik yalnız kökteki dosyalara uygulanır, dizinlere girilmez.
    if "/" in ad or ad in ("", ".", "..", ".git"):
        raise ValueError("Güvensiz artık yolu: " + ad)
    fd = os.open(agac, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd, ad
    finally:
        os.close(fd)


@contextmanager
def _indeks_kilidi(agac):
    # Worktree'nin indeksi ortak .git/index değildir. Git'in kendi kilit yolunu
    # kullanmak, son kontrolden unlink'e kadar eşzamanlı git add'i de engeller.
    sonuc = subprocess.run(
        ["git", "-C", str(agac), "rev-parse", "--path-format=absolute", "--git-path", "index.lock"],
        capture_output=True)
    if sonuc.returncode:
        raise ValueError("Artık temizliğinde Git indeks kilidi bulunamadı")
    kilit = os.fsdecode(sonuc.stdout.removesuffix(b"\n"))
    fd = os.open(kilit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        yield
    finally:
        os.close(fd)
        # Açma başarısızsa buraya girilmez; başka sürecin kilidi kaldırılmaz.
        os.unlink(kilit)


def _kimlik(bilgi):
    return [bilgi.st_dev, bilgi.st_ino, bilgi.st_size, bilgi.st_mtime_ns, bilgi.st_ctime_ns]


def artik_incele(agac, gorev, ihlaller):
    """Bütün ihlaller uygunsa liste döndürür; tek sakıncada hiçbir aday vermez."""
    from orvant_op.yurutme.akis import _yol_eslesir

    try:
        if not ihlaller:
            raise ValueError("Kapsam ihlali yok")
        izlenmeyen = _git_yollari(agac, "ls-files", "--others", "--exclude-standard", "-z")
        # İndeksten silinip aynı adla bırakılan HEAD dosyası da korunur.
        izlenen = (_git_yollari(agac, "ls-files", "--cached", "-z") |
                   _git_yollari(agac, "ls-tree", "-r", "--name-only", "-z", "HEAD"))
        adaylar = []
        for ad in sorted(set(ihlaller)):
            if ad not in izlenmeyen or ad in izlenen:
                raise ValueError("Dosya izlenmeyen artık değil: " + ad)
            if any(_yol_eslesir(ad, kalip) for kalip in gorev["yazilabilir"]):
                raise ValueError("Dosya yazılabilir kapsamda: " + ad)
            with _ust_dizin(agac, ad) as (fd, isim):
                bilgi = os.stat(isim, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(bilgi.st_mode) or bilgi.st_nlink != 1:
                raise ValueError("Artık sıradan ve tek bağlantılı dosya değil: " + ad)
            if bilgi.st_size >= BOYUT_SINIRI:
                raise ValueError("Artık boyut sınırını aşıyor: " + ad)
            adaylar.append({"yol": ad, "kimlik": _kimlik(bilgi)})
        return {"uygun": True, "dosyalar": adaylar}
    except (OSError, ValueError) as exc:
        return {"uygun": False, "dosyalar": [], "hata": str(exc)}


def artik_temizle(agac, gorev, inceleme, kapsam_oku, silindi):
    """Git indeksini kilitleyip bütün kapsamı ve dosya kimliklerini doğrular.

    Her silme anında makbuz/olay yazdırılır; sonraki silme başarısız olsa da iz kalır.
    """
    with _indeks_kilidi(agac):
        adaylar = inceleme["dosyalar"]
        _, ihlaller = kapsam_oku(agac, gorev)
        if not inceleme.get("uygun") or artik_incele(agac, gorev, ihlaller) != inceleme:
            raise ValueError("Artık incelemesinden sonra kapsam veya dosyalar değişti")
        for aday in adaylar:
            ad = aday["yol"]
            # Önceki silme/kayıt sırasında dosya değişmiş olabilir.
            if artik_incele(agac, gorev, [ad]) != {"uygun": True, "dosyalar": [aday]}:
                raise ValueError("Silme öncesinde artık değişti: " + ad)
            with _ust_dizin(agac, ad) as (fd, isim):
                bilgi = os.stat(isim, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(bilgi.st_mode) or _kimlik(bilgi) != aday["kimlik"]:
                    raise ValueError("Silme öncesinde dosya kimliği değişti: " + ad)
                os.unlink(isim, dir_fd=fd)
            silindi(ad)
