"""İşletim sistemi uyum katmanı: dosya kilidi, atomik değiştirme, bağlantı ve güvenli açma.

Motor bu işleri doğrudan `fcntl`, `O_NOFOLLOW`, `os.geteuid` gibi POSIX çağrılarıyla yapmaz;
hepsi buradan geçer. Linux/macOS davranışı eskisiyle aynıdır, Windows karşılıkları burada tutulur.
Yalnızca standart kütüphane kullanılır (`ctypes` dahil); dış bağımlılık yoktur.
"""
import contextlib
import errno
import os
import stat
import sys
import time

WINDOWS = os.name == "nt"
# `os.open` ile açılıp `os.write` ile yazılan dosyalarda Windows satır sonunu çevirmesin diye zorunlu.
O_BINARY = getattr(os, "O_BINARY", 0)

if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _k32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
    _k32.LockFileEx.restype = wintypes.BOOL
    _k32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(_OVERLAPPED)]
    _k32.UnlockFileEx.restype = wintypes.BOOL
    _LOCKFILE_FAIL_IMMEDIATELY = 0x1
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_IO_PENDING = 997
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    # Kilit, dosya VERİSİNİN bittiği yerin çok ötesindeki 1 baytlık aralıkta tutulur: Windows kilitleri
    # zorunludur (mandatory), veri aralığını kilitlemek aynı dosyayı okuyan/yazan diğer süreçleri bozardı.
    _KILIT_OFFSET_YUKSEK = 0x00010000
else:
    import fcntl


def _fd(nesne):
    return nesne if isinstance(nesne, int) else nesne.fileno()


def kilitle(nesne, *, paylasimli=False, bekle=True, zaman_asimi=None, aralik=0.05):
    """Dosya kilidi alır (`fcntl.flock` karşılığı). `nesne`: fd ya da `fileno()` olan dosya nesnesi.

    `bekle=False` iken kilit doluysa `BlockingIOError` fırlatır (POSIX `LOCK_NB` ile aynı sözleşme).
    `zaman_asimi` (sn) dolarsa yine `BlockingIOError`. Kilit, fd kapanınca ya da `kilit_birak` ile düşer.
    Windows'ta kilit yoklamalı alınır, böylece Ctrl+C beklerken de çalışır.
    """
    fd = _fd(nesne)
    if not WINDOWS:
        bayrak = fcntl.LOCK_SH if paylasimli else fcntl.LOCK_EX
        if not bekle:
            fcntl.flock(fd, bayrak | fcntl.LOCK_NB)
            return
        if zaman_asimi is None:
            fcntl.flock(fd, bayrak)
            return
        bitis = time.monotonic() + zaman_asimi
        while True:
            try:
                fcntl.flock(fd, bayrak | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= bitis:
                    raise
                time.sleep(aralik)
    tutamak = msvcrt.get_osfhandle(fd)
    bayrak = _LOCKFILE_FAIL_IMMEDIATELY | (0 if paylasimli else _LOCKFILE_EXCLUSIVE_LOCK)
    bitis = None if zaman_asimi is None else time.monotonic() + zaman_asimi
    while True:
        ov = _OVERLAPPED()
        ov.OffsetHigh = _KILIT_OFFSET_YUKSEK
        if _k32.LockFileEx(tutamak, bayrak, 0, 1, 0, ctypes.byref(ov)):
            return
        hata = ctypes.get_last_error()
        if hata not in (_ERROR_LOCK_VIOLATION, _ERROR_IO_PENDING):
            raise ctypes.WinError(hata)
        if not bekle or (bitis is not None and time.monotonic() >= bitis):
            raise BlockingIOError(errno.EAGAIN, "dosya kilidi dolu")
        time.sleep(aralik)


def kilit_birak(nesne):
    """`kilitle` ile alınan kilidi bırakır; kilit yoksa sessizce geçer."""
    fd = _fd(nesne)
    if not WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    ov = _OVERLAPPED()
    ov.OffsetHigh = _KILIT_OFFSET_YUKSEK
    _k32.UnlockFileEx(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(ov))


@contextlib.contextmanager
def kilit(nesne, **kwargs):
    kilitle(nesne, **kwargs)
    try:
        yield nesne
    finally:
        kilit_birak(nesne)


def degistir(kaynak, hedef, *, deneme=40, bekleme=0.025):
    """`os.replace` karşılığı. Windows'ta hedef başka süreçte açıkken (antivirüs, dizinleyici, okuyucu)
    `PermissionError` gelebilir; kısa aralıklarla yeniden dener. POSIX'te doğrudan `os.replace`."""
    if not WINDOWS:
        os.replace(kaynak, hedef)
        return
    for sayac in range(deneme):
        try:
            os.replace(kaynak, hedef)
            return
        except PermissionError:
            if sayac == deneme - 1:
                raise
            time.sleep(bekleme * min(sayac + 1, 8))


def baglanti_mi(yol):
    """Sembolik bağ ya da Windows kavşağı (junction)/yeniden ayrıştırma noktası mı? Bağı izlemez."""
    yol = os.fspath(yol)
    try:
        bilgi = os.lstat(yol)
    except OSError:
        return False
    if stat.S_ISLNK(bilgi.st_mode):
        return True
    return bool(WINDOWS and getattr(bilgi, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def guvenli_ac(yol, bayraklar=os.O_RDONLY, izin=0o666, *, dizin=False):
    """Bağı izlemeden açar (`O_NOFOLLOW | O_CLOEXEC` karşılığı); bağ ise `OSError(ELOOP)` fırlatır.

    Windows'ta `O_NOFOLLOW` yoktur: açmadan önce ve sonra `lstat`/`fstat` kimliği karşılaştırılır,
    bağ ya da araya giren değişiklik reddedilir. `dizin=True` yalnız POSIX'te dizin açar; Windows'ta dizin
    fd'si açılamaz, bu yüzden yalnız bağ olmadığı doğrulanır ve `None` döner.
    """
    yol = os.fspath(yol)
    if not WINDOWS:
        ek = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        if dizin:
            ek |= getattr(os, "O_DIRECTORY", 0)
        return os.open(yol, bayraklar | ek, izin)
    once = os.lstat(yol) if os.path.lexists(yol) else None
    if once is not None and baglanti_mi(yol):
        raise OSError(errno.ELOOP, "bağ izlenmez", yol)
    if dizin:
        if once is not None and not stat.S_ISDIR(once.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "dizin değil", yol)
        return None
    fd = os.open(yol, bayraklar | getattr(os, "O_BINARY", 0), izin)
    try:
        sonra = os.fstat(fd)
        if once is not None and (once.st_ino, once.st_dev) != (sonra.st_ino, sonra.st_dev):
            raise OSError(errno.ELOOP, "açılırken dosya değişti", yol)
        if baglanti_mi(yol):
            raise OSError(errno.ELOOP, "bağ izlenmez", yol)
    except BaseException:
        os.close(fd)
        raise
    return fd


def sahibi_ben_mi(bilgi):
    """Dosya sahibi geçerli kullanıcı mı? POSIX'te `st_uid`; Windows'ta sahiplik ACL ile belirlenir ve
    `st_uid` anlamsızdır (hep 0), bu yüzden orada denetim yapılmaz (True döner)."""
    if WINDOWS:
        return True
    return bilgi.st_uid == os.geteuid()


def kullanici_kimligi():
    """Geçerli kullanıcıyı ayırt eden kısa metin (dizin adı için güvenli)."""
    if WINDOWS:
        return os.environ.get("USERNAME") or "kullanici"
    return str(os.getuid())


def izin_biti_anlamli_mi():
    """`st_mode & 0o077` gibi izin bitleri anlamlı mı? Windows'ta değildir (ACL kullanılır)."""
    return not WINDOWS


def gecici_dizin():
    """Platformun geçici dizini (`/tmp` yerine). `TMPDIR`/`TEMP`/`TMP` ortamını izler."""
    import tempfile
    return tempfile.gettempdir()


def platform_adi():
    return "windows" if WINDOWS else sys.platform


@contextlib.contextmanager
def paylasimli_gecici_dizin(prefix="orvant-", dir=None):
    """`tempfile.TemporaryDirectory` karşılığı; dizin üst dizinin izinlerini miras alır.

    Python 3.12.4+ Windows'ta `mkdtemp` dizinini yalnız sahibine açar; `codex sandbox` kullanıcısı
    (başka bir hesap) bu dizindeki kapı ağacını göremez. Yalıtımlı kehanetin okuyacağı ağaçlar için bunu kullan.
    POSIX'te davranış `TemporaryDirectory` ile aynıdır."""
    import shutil
    import tempfile
    import uuid
    if not WINDOWS:
        with tempfile.TemporaryDirectory(prefix=prefix, dir=dir) as yol:
            yield yol
        return
    taban = dir or tempfile.gettempdir()
    yol = os.path.join(os.fspath(taban), prefix + uuid.uuid4().hex[:12])
    os.mkdir(yol)
    try:
        yield yol
    finally:
        shutil.rmtree(yol, ignore_errors=True)
