"""Kapsam dışındaki küçük, izlenmeyen dosyaları kanıtla ve güvenle temizle."""

import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from orvant_op import uyum

BOYUT_SINIRI = 1_000_000


def _bagli_yol(yol):
    """Windows'ta kök dahil bütün sözcüksel ataları kavşak/bağ için denetler."""
    ham = Path(os.path.abspath(yol))
    return any(uyum.baglanti_mi(parca) for parca in (ham, *ham.parents))


def _git_yollari(agac, *args):
    sonuc = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(agac), *args], capture_output=True)
    if sonuc.returncode:
        raise ValueError("Artık incelemesinde git bilgisi okunamadı")
    return {p.decode("utf-8") for p in sonuc.stdout.split(b"\0") if p}


@contextmanager
def _ust_dizin(agac, ad):
    # Kapı dizinleri dosyalara açarak bildirir. Alt yolları da reddetmek gerekir;
    # otomatik temizlik yalnız kökteki dosyalara uygulanır, dizinlere girilmez.
    # Windows'ta ayrıca ters bölü, sürücü/akış ayracı (`:`) ve sondaki nokta/boşluk yol kaçışıdır.
    if ("/" in ad or ad in ("", ".", "..", ".git")
            or (uyum.WINDOWS and ("\\" in ad or ":" in ad or ad.endswith((".", " "))
                                  or ad.casefold() == ".git"))):
        raise ValueError("Güvensiz artık yolu: " + ad)
    # POSIX: dizin fd'si; sonraki işlemler `dir_fd` ile ona bağlı kalır. Windows'ta dizin fd'si yoktur:
    # `guvenli_ac` yalnız `agac` bağ/kavşak değil diye doğrular ve None döner, işlemler yol üzerinden yürür.
    # Windows'ta doğrulama ile işlem arasında üst dizinin kavşakla değiştirilmesi yarışı POSIX'tekinden zayıftır;
    # `_lstat`/`_sil` bu yüzden bütün yol zincirini her işlemden hemen önce yeniden denetler.
    if uyum.WINDOWS and _bagli_yol(agac):
        raise ValueError("Artık ağacı bağ içeriyor")
    fd = uyum.guvenli_ac(agac, os.O_RDONLY, dizin=True)
    try:
        yield fd, ad
    finally:
        if fd is not None:
            os.close(fd)


def _lstat(agac, fd, isim):
    """Bağı izlemeden `stat`; POSIX'te `dir_fd` ile, Windows'ta yol üzerinden."""
    if not uyum.WINDOWS:
        return os.stat(isim, dir_fd=fd, follow_symlinks=False)
    yol = os.path.join(agac, isim)
    if _bagli_yol(yol):
        raise ValueError("Artık yolu bağ içeriyor: " + isim)
    bilgi = os.lstat(yol)
    if bilgi.st_ino == 0:
        # Dosya kimliği sağlamayan dosya sistemlerinde (FAT, bazı ağ paylaşımları) kimlik kanıtı olmaz.
        raise ValueError("Dosya sistemi dosya kimliği sağlamıyor: " + isim)
    return bilgi


def _sil(agac, fd, isim):
    if not uyum.WINDOWS:
        os.unlink(isim, dir_fd=fd)
        return
    yol = os.path.join(agac, isim)
    if _bagli_yol(yol):
        raise ValueError("Artık yolu bağ içeriyor: " + isim)
    try:
        os.unlink(yol)
    except PermissionError:
        # Salt-okunur öznitelikli dosya POSIX'te de silinebilir (dizin yazma hakkı yeter); eşdeğerlik için bir kez.
        os.chmod(yol, stat.S_IWRITE)
        os.unlink(yol)


@contextmanager
def _indeks_kilidi(agac):
    # Worktree'nin indeksi ortak .git/index değildir. Git'in kendi kilit yolunu
    # kullanmak, son kontrolden unlink'e kadar eşzamanlı git add'i de engeller.
    sonuc = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(agac), "rev-parse", "--path-format=absolute", "--git-path", "index.lock"],
        capture_output=True)
    if sonuc.returncode:
        raise ValueError("Artık temizliğinde Git indeks kilidi bulunamadı")
    ham = sonuc.stdout.removesuffix(b"\n")
    if uyum.WINDOWS:
        ham = ham.removesuffix(b"\r")
    kilit = ham.decode("utf-8")
    if uyum.WINDOWS and _bagli_yol(Path(kilit).parent):
        raise ValueError("Git indeks kilidi yolu bağ içeriyor")
    fd = uyum.guvenli_ac(kilit, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        yield
    finally:
        os.close(fd)
        # Açma başarısızsa buraya girilmez; başka sürecin kilidi kaldırılmaz.
        os.unlink(kilit)


def _kimlik(bilgi):
    # Windows'ta (NTFS/ReFS) st_dev birim seri numarası, st_ino dosya dizinidir; stat/lstat/fstat arasında
    # kararlıdır. st_ctime Windows'ta oluşturulma zamanıdır; yine de yeniden adlandırma/yerine koyma izini yakalar.
    return [bilgi.st_dev, bilgi.st_ino, bilgi.st_size, bilgi.st_mtime_ns, bilgi.st_ctime_ns]


def _izlenen_mi(ad, izlenen):
    if ad in izlenen:
        return True
    # Büyük/küçük harf duyarsız dosya sisteminde `Foo.txt` ile `foo.txt` aynı dosyadır.
    return uyum.WINDOWS and ad.casefold() in {x.casefold() for x in izlenen}


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
            if ad not in izlenmeyen or _izlenen_mi(ad, izlenen):
                raise ValueError("Dosya izlenmeyen artık değil: " + ad)
            if any(_yol_eslesir(ad, kalip) for kalip in gorev["yazilabilir"]):
                raise ValueError("Dosya yazılabilir kapsamda: " + ad)
            if uyum.WINDOWS and any(_yol_eslesir(ad.casefold(), kalip.casefold())
                                    for kalip in gorev["yazilabilir"]):
                raise ValueError("Dosya yazılabilir kapsamda: " + ad)
            with _ust_dizin(agac, ad) as (fd, isim):
                bilgi = _lstat(agac, fd, isim)
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
                bilgi = _lstat(agac, fd, isim)
                if not stat.S_ISREG(bilgi.st_mode) or _kimlik(bilgi) != aday["kimlik"]:
                    raise ValueError("Silme öncesinde dosya kimliği değişti: " + ad)
                _sil(agac, fd, isim)
            silindi(ad)
