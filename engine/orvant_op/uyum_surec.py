"""Süreç uyumu: süreç grubu/ağacı, başlangıç zamanı, kabuk seçimi ve çalıştırılabilir çözümleme.

`yurutucu.py` ve kehanet bu modül üzerinden süreç başlatır/öldürür; `os.killpg`, `start_new_session`,
`/proc`, `/bin/sh` gibi POSIX çağrıları yalnız burada kalır. Linux/macOS davranışı eskisiyle aynıdır
(POSIX kodu `yurutucu.py`'den kelimesi kelimesine taşındı). Yalnızca standart kütüphane (`ctypes` dahil).

Windows tasarımı
- **Süreç ağacı = Job Object.** `grup_baslat` süreci askıda (`CREATE_SUSPENDED`) başlatır, adlandırılmış bir
  Job Object'e atar, sonra `NtResumeProcess` ile devam ettirir; böylece süreç hiçbir torun üretmeden job
  içindedir (yarış yok). Job `KILL_ON_JOB_CLOSE` taşır: Orvant çökerse ya da `grup_birak` çağrılırsa
  kalan tüm ağaç ölür. Job adı lider PID'i + süreç başlangıç zamanını içerir (`Local\\orvant-grup-<pid>-<başlangıç>`),
  bu yüzden BAŞKA bir Orvant süreci (iptal komutu) yalnızca `(pgid, baslangic)` ile jobu açıp
  `TerminateJobObject` çağırabilir; PID yeniden kullanımı farklı adla ayrışır.
- Job kurulamazsa askıdaki süreç başlatılmaz; ağaç öldürme güvencesi olmadan devam edilmez.
- Yeni süreç `CREATE_NEW_PROCESS_GROUP` ile başlar: terminalin Ctrl-C'si ona ulaşmaz (POSIX
  `start_new_session` ile aynı amaç, G-145).
- Süreç başlangıç zamanı `GetProcessTimes` (FILETIME, ondalık metin); ölü/erişilemeyen PID için `None`.
- Nazik sonlandırma (SIGTERM) yoktur: `TerminateJobObject` sert öldürür; `bekleme` yok sayılır.

Kabuk seçimi (Windows) — karar ve gerekçe
Kabuk gerektiren komut (`|`, `&&`, `$VAR`, tek tırnak, `cd`...) için varsayılan **Git for Windows `bash.exe`**,
yoksa `cmd.exe /d /s /c`. Neden: plan/kabul komutları çoğu zaman POSIX sözdizimiyle yazılır
(`test -f x && grep -q y z`, `'tek tırnak'`, `$(...)`, `;`). `cmd.exe` bunları sessizce yanlış yorumlar
(tek tırnak literal kalır, `;` ayırıcı değildir, `$VAR` açılmaz); bash ise kodun Linux'ta kanıtlanmış
anlamını korur. Kabuksuz çalışan basit argv'ler (`python -m unittest ...`) bundan etkilenmez.
Tuzaklar: PATH'teki `bash` çoğu zaman WSL başlatıcısıdır (`System32\\bash.exe`, `WindowsApps`); o reddedilir,
Git'in kendi `bash.exe`'si git konumundan/standart yollardan bulunur. `ORVANT_KABUK=cmd|bash|<yol>` ile
elle seçilebilir. Seçilen kabuk `kabuk_adi()` ile döner ve kabuk gerekçesine yazılır (iz kaydı).
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

WINDOWS = os.name == "nt"
KABUK_ORTAMI = "ORVANT_KABUK"


class BatKomutHatasi(ValueError):
    """`.cmd`/`.bat` betiği kabuk metakarakterli argümanlarla güvenle çalıştırılamıyor."""


# --- POSIX yardımcıları (yurutucu.py'den taşındı; davranış aynı) ----------------------------------

def _posix_surec_baslangici(pid):
    """/proc/<pid>/stat başlangıç zamanı; süreç yoksa None (PID yeniden kullanımı denetimi)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]


def _posix_grup_canli(pgid):
    """Grupta çalışabilir süreç var mı; toplanmayan zombileri canlı sayma."""
    proc = Path("/proc")
    if proc.is_dir():
        for yol in proc.iterdir():
            if not yol.name.isdigit():
                continue
            try:
                alanlar = (yol / "stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
                if int(alanlar[2]) == pgid and alanlar[0] != "Z":
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _posix_grup_durdur(pgid, bekleme):
    for sinyal, sure in ((signal.SIGTERM, bekleme), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sinyal)
        except ProcessLookupError:
            if sinyal == signal.SIGKILL:  # SIGTERM beklemesinin hemen ardından bitti
                return {"pgid": pgid, "durduruldu": True, "sinyal": "SIGTERM"}
            return {"pgid": pgid, "durduruldu": False, "neden": "süreç grubu zaten bitmiş"}
        except PermissionError:
            return {"pgid": pgid, "durduruldu": False, "neden": "sinyal izni yok"}
        if _grup_bitti(pgid, sure):
            return {"pgid": pgid, "durduruldu": True, "sinyal": sinyal.name}
    return {"pgid": pgid, "durduruldu": False, "neden": "SIGKILL sonrası grup hâlâ var"}


# --- Windows: ctypes bağları ---------------------------------------------------------------------

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _nt = ctypes.WinDLL("ntdll")

    class _JOB_TEMEL(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO_SAYAC(ctypes.Structure):
        _fields_ = [(ad, ctypes.c_uint64) for ad in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOB_GENIS(ctypes.Structure):
        _fields_ = [("Basic", _JOB_TEMEL), ("Io", _IO_SAYAC), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _JOB_SAYIM(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    for _ad, _argtipleri, _donus in (
            ("CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
            ("OpenJobObjectW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
            ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                           wintypes.LPVOID], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("GetProcessTimes", [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
                                 wintypes.LPVOID], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("GetConsoleWindow", [], wintypes.HANDLE)):
        _islev = getattr(_k32, _ad)
        _islev.argtypes, _islev.restype = _argtipleri, _donus
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _nt.NtResumeProcess.restype = ctypes.c_long

    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NO_WINDOW = 0x08000000
    _JOB_KILL_ON_CLOSE = 0x2000
    _JOB_GENIS_BILGI, _JOB_SAYIM_BILGI = 9, 1
    _JOB_TERMINATE, _JOB_QUERY = 0x0008, 0x0004
    _PROCESS_QUERY_LIMITED = 0x1000
    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x102
    _ERROR_ALREADY_EXISTS = 183

    _isler = {}  # lider pid -> (job tutamağı, ad); bu süreçte başlatılan gruplar
    _isler_kilidi = threading.Lock()


# --- Süreç başlangıç zamanı / canlılık -------------------------------------------------------------

def surec_baslangici(pid):
    """Süreç başlangıç zamanı (metin); süreç yok/bitmiş/erişilemez ise None (PID yeniden kullanımı denetimi).

    POSIX: /proc/<pid>/stat alan 22. Windows: GetProcessTimes oluşturma FILETIME'ı (ondalık metin)."""
    if not WINDOWS:
        return _posix_surec_baslangici(pid)
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    # WaitForSingleObject ayrıca SYNCHRONIZE ister; yalnız sorgu hakkı WAIT_FAILED döndürür.
    tutamak = _k32.OpenProcess(_PROCESS_QUERY_LIMITED | _SYNCHRONIZE, False, pid)
    if not tutamak:
        return None
    try:
        # Çıkmış ama tutamağı açık kalmış süreç (Popen henüz toplamadı) ölü sayılır.
        if _k32.WaitForSingleObject(tutamak, 0) != _WAIT_TIMEOUT:
            return None
        zamanlar = (wintypes.FILETIME * 4)()
        if not _k32.GetProcessTimes(tutamak, *(ctypes.byref(z) for z in zamanlar)):
            return None
        return str((zamanlar[0].dwHighDateTime << 32) | zamanlar[0].dwLowDateTime)
    finally:
        _k32.CloseHandle(tutamak)


def _is_adi(pid, baslangic):
    return f"Local\\orvant-grup-{int(pid)}-{baslangic}"


def _is_ac(pgid, baslangic, erisim):
    """(tutamak, sahip_mi): bu süreçte kayıtlıysa o, değilse ada göre aç. Bulunamazsa (None, False)."""
    with _isler_kilidi:
        kayit = _isler.get(int(pgid))
    if kayit is not None:
        if baslangic is not None and kayit[1] != _is_adi(pgid, baslangic):
            return None, False
        return kayit[0], False
    if baslangic is None:
        baslangic = surec_baslangici(pgid)
    if baslangic is None:
        return None, False
    tutamak = _k32.OpenJobObjectW(erisim, False, _is_adi(pgid, baslangic))
    return (tutamak or None), True


def _is_birak(tutamak, kapat):
    if kapat and tutamak:
        _k32.CloseHandle(tutamak)


def _is_aktif_sayisi(tutamak):
    sayim = _JOB_SAYIM()
    if not _k32.QueryInformationJobObject(tutamak, _JOB_SAYIM_BILGI, ctypes.byref(sayim),
                                          ctypes.sizeof(sayim), None):
        return None
    return sayim.ActiveProcesses


def grup_canli(pgid, baslangic=None):
    """Grupta çalışabilir süreç var mı. POSIX: /proc taraması (zombiler hariç). Windows: job'da aktif süreç;
    job bulunamazsa lider süreç canlı mı."""
    if not WINDOWS:
        return _posix_grup_canli(pgid)
    tutamak, kapat = _is_ac(pgid, baslangic, _JOB_QUERY)
    if tutamak:
        try:
            sayi = _is_aktif_sayisi(tutamak)
        finally:
            _is_birak(tutamak, kapat)
        if sayi is not None:
            return sayi > 0
    guncel = surec_baslangici(pgid)
    return guncel is not None and (baslangic is None or baslangic == guncel)


def _grup_bitti(pgid, sure, baslangic=None):
    son = time.monotonic() + sure
    while True:
        if not grup_canli(pgid, baslangic):
            return True
        if time.monotonic() >= son:
            return False
        time.sleep(0.05)


# --- Süreç grubu başlatma / öldürme --------------------------------------------------------------

def grup_kwargs():
    """`subprocess.Popen` için yeni süreç grubu argümanları. Windows'ta tek başına ağaç öldürmeyi sağlamaz
    (Job Object için `grup_baslat` kullan); kehanet gibi kendi Popen'ını kuran kodlar için asgari eşdeğerdir."""
    if WINDOWS:
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _is_olustur(proc):
    """Askıdaki sürecin job'unu kurar ve sürece atar; başarısızsa None."""
    baslangic = surec_baslangici(proc.pid)
    if baslangic is None:
        return None
    ad = _is_adi(proc.pid, baslangic)
    tutamak = _k32.CreateJobObjectW(None, ad)
    if not tutamak:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:  # PID+başlangıç çakışması: pratikte imkânsız
        _k32.CloseHandle(tutamak)
        return None
    bilgi = _JOB_GENIS()
    bilgi.Basic.LimitFlags = _JOB_KILL_ON_CLOSE
    if (not _k32.SetInformationJobObject(tutamak, _JOB_GENIS_BILGI, ctypes.byref(bilgi), ctypes.sizeof(bilgi))
            or not _k32.AssignProcessToJobObject(tutamak, int(proc._handle))):
        _k32.CloseHandle(tutamak)
        return None
    return tutamak, ad


def grup_baslat(komut, **popen_kw):
    """`subprocess.Popen` gibi; süreci yeni grupta (POSIX: oturum, Windows: grup + Job Object) başlatır.
    Windows'ta `komut` listesi `cozumle_argv` ile çözülmüş olmalıdır (grup_run bunu yapar)."""
    if not WINDOWS:
        return subprocess.Popen(komut, start_new_session=True, **popen_kw)
    bayrak = popen_kw.pop("creationflags", 0) | _CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED
    if not _k32.GetConsoleWindow():
        bayrak |= _CREATE_NO_WINDOW  # konsolsuz (arka plan) Orvant, çocuk için konsol penceresi açmasın
    proc = subprocess.Popen(komut, creationflags=bayrak, **popen_kw)
    try:
        is_ = _is_olustur(proc)
        if is_ is None:
            raise OSError("Windows Job Object kurulamadı; süreç ağacı güvenle izlenemiyor")
        with _isler_kilidi:
            _isler[proc.pid] = is_
        if _nt.NtResumeProcess(int(proc._handle)) != 0:  # askıda kalmasın
            raise OSError("süreç askıdan çıkarılamadı")
    except BaseException:
        grup_birak(proc)
        proc.kill()
        proc.wait(timeout=5)
        raise
    return proc


def grup_birak(proc_veya_pid):
    """Windows: sürecin job tutamağını kapatır (KILL_ON_JOB_CLOSE: kalan torunlar ölür). POSIX: yok."""
    if not WINDOWS:
        return
    pid = getattr(proc_veya_pid, "pid", proc_veya_pid)
    with _isler_kilidi:
        kayit = _isler.pop(int(pid), None)
    if kayit is not None:
        _k32.CloseHandle(kayit[0])


def _is_sonlandir(pgid, baslangic):
    """Job'u sonlandırır: True (gönderildi), False (job yok), PermissionError (izin yok)."""
    tutamak, kapat = _is_ac(pgid, baslangic, _JOB_TERMINATE | _JOB_QUERY)
    if not tutamak:
        return False
    try:
        if not _k32.TerminateJobObject(tutamak, 1):
            if ctypes.get_last_error() == 5:
                raise PermissionError("TerminateJobObject: erişim engellendi")
            return False
        return True
    finally:
        _is_birak(tutamak, kapat)


def grup_oldur(proc, *, nazik=True):
    """Sürecin grubunu/ağacını sonlandırır; yoksa sessiz geçer. POSIX: nazik → SIGTERM, ≤2 sn bekle, SIGKILL;
    nazik=False → doğrudan SIGKILL. Windows: tüm job (süreç ağacı) sert sonlandırılır (nazik yok sayılır)."""
    if not WINDOWS:
        try:
            if nazik:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            os.killpg(proc.pid, signal.SIGKILL)  # SIGTERM'i yok sayan torunlar da kalmasın
        except ProcessLookupError:
            pass
        return
    try:
        _is_sonlandir(proc.pid, None)
    except PermissionError:
        pass
    try:
        proc.kill()  # lider job dışında kaldıysa yine de öl
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def grup_durdur(pgid, bekleme=5.0, baslangic=None):
    """Kayıtlı grubu durdurur (`yurutucu.grup_durdur` PID yeniden kullanım denetiminden sonra çağırır).
    POSIX: SIGTERM, gerekirse SIGKILL. Windows: yalnız kayıtlı job sonlandırılır; bilinmeyen PID'ye dokunulmaz."""
    if not WINDOWS:
        return _posix_grup_durdur(pgid, bekleme)
    if not grup_canli(pgid, baslangic):
        return {"pgid": pgid, "durduruldu": False, "neden": "süreç grubu zaten bitmiş"}
    try:
        yontem = "TerminateJobObject" if _is_sonlandir(pgid, baslangic) else None
    except PermissionError:
        return {"pgid": pgid, "durduruldu": False, "neden": "sinyal izni yok"}
    if yontem is None:
        return {"pgid": pgid, "durduruldu": False, "neden": "kayıtlı Job Object bulunamadı"}
    if _grup_bitti(pgid, 5.0, baslangic):
        return {"pgid": pgid, "durduruldu": True, "sinyal": yontem}
    return {"pgid": pgid, "durduruldu": False, "neden": f"{yontem} sonrası grup hâlâ var"}


# --- Ortam ---------------------------------------------------------------------------------------

def ortam(env=None):
    """Çocuk ortamı. Windows'ta Python çocukların G/Ç ve `open()` kodlaması UTF-8 olsun (cp1254 yerine);
    kullanıcı `PYTHONUTF8`'i kendisi ayarladıysa dokunulmaz. POSIX: `env` olduğu gibi döner."""
    if not WINDOWS:
        return env
    temel = dict(os.environ if env is None else env)
    if not any(k.upper() == "PYTHONUTF8" for k in temel):
        temel["PYTHONUTF8"] = "1"
    return temel


# --- Kabuk ---------------------------------------------------------------------------------------

def _wsl_baslaticisi_mi(yol):
    kucuk = str(yol).lower().replace("/", "\\")
    return "\\windows\\system32\\" in kucuk or "\\windowsapps\\" in kucuk or "\\windows\\sysnative\\" in kucuk


def git_bash():
    """Git for Windows `bash.exe` yolu ya da None (WSL başlatıcısı sayılmaz)."""
    if not WINDOWS:
        return None
    adaylar = []
    git = shutil.which("git")
    if git:
        for ust in list(Path(git).resolve().parents)[:3]:
            adaylar += [ust / "bin" / "bash.exe", ust / "usr" / "bin" / "bash.exe"]
    for ortam_adi in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(ortam_adi):
            adaylar.append(Path(os.environ[ortam_adi]) / "Git" / "bin" / "bash.exe")
    if os.environ.get("LOCALAPPDATA"):
        adaylar.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Git" / "bin" / "bash.exe")
    genel = shutil.which("bash")
    if genel and not _wsl_baslaticisi_mi(genel):
        adaylar.append(Path(genel))
    for aday in adaylar:
        if aday.is_file():
            return str(aday)
    return None


def _cmd_exe():
    return os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"


def _windows_kabuk():
    """(ad, yol): 'bash' (Git bash) ya da 'cmd'."""
    secim = os.environ.get(KABUK_ORTAMI, "").strip()
    if secim.lower() == "cmd":
        return "cmd", _cmd_exe()
    if secim and secim.lower() != "bash":
        return ("cmd", secim) if Path(secim).name.lower() == "cmd.exe" else ("bash", secim)
    bash = git_bash()
    return ("bash", bash) if bash else ("cmd", _cmd_exe())


def kabuk_adi():
    """Kabuk gerektiren komutlar için kullanılacak kabuk: POSIX 'sh'; Windows 'bash' (Git) ya da 'cmd'."""
    return _windows_kabuk()[0] if WINDOWS else "sh"


def kabuk_argv(komut):
    """Kabuk gerektiren komut metni için argv. POSIX: `/bin/sh -c`. Windows: Git bash `-c` ya da
    `cmd.exe /d /s /c` (bkz. modül açıklaması)."""
    if not WINDOWS:
        return ["/bin/sh", "-c", komut]
    ad, yol = _windows_kabuk()
    if ad == "cmd":
        if not os.environ.get(KABUK_ORTAMI) and ("'" in komut or "$" in komut or "`" in komut):
            raise OSError("POSIX kabuk sözdizimi için Git Bash gerekli; ORVANT_KABUK ile kabuk seçin")
        return [yol, "/d", "/s", "/c", komut]
    if re.search(r"\bpython3\b", komut):
        # Windows'ta `python3` yok (Store takma adı sessizce bozuk); POSIX yazılmış komutlar çalışan
        # yorumlayıcıyı görsün.
        yorumlayici = sys.executable.replace("'", "'\\''")
        komut = f"python3() {{ '{yorumlayici}' \"$@\"; }}; " + komut
    return [yol, "-c", komut]


# --- Çalıştırılabilir çözümleme (Windows) --------------------------------------------------------

def _pathext():
    ham = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    return [e.lower() for e in ham.split(os.pathsep) if e.startswith(".")]


def ikili_yolu(ad):
    """Çalıştırılabilir dosyanın tam yolu ya da None. POSIX: `shutil.which`. Windows: PATH + PATHEXT
    (cwd taranmaz), PATHEXT'te olmayan `.ps1` en sona eklenir; `ad` yol içeriyorsa yalnız o yol denenir."""
    ad = str(ad)
    if not WINDOWS:
        return shutil.which(ad)
    uzantilar = _pathext() + [".ps1"]
    uzanti = Path(ad).suffix.lower()
    adlar = [ad] if uzanti in uzantilar else [ad + e for e in uzantilar]
    if os.path.dirname(ad):
        dizinler = [None]
    else:
        dizinler = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for dizin in dizinler:
        for aday in adlar:
            yol = Path(aday) if dizin is None else Path(dizin.strip('"')) / aday
            if yol.is_file():
                return str(yol)
    return None


def _npm_golge_onek(yol):
    """npm/bun `.cmd` gölge betiğinin çağırdığı gerçek komut: `[exe]` ya da `[node, betik.js]`; çözülemezse None."""
    try:
        satirlar = Path(yol).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    son = next((s for s in reversed(satirlar) if "%*" in s), None)
    if son is None:
        return None
    parca = son.rsplit("%*", 1)[0].rsplit("&", 1)[-1]
    simgeler = re.findall(r'"([^"]+)"', parca)
    if not simgeler:
        return None
    dizin = str(Path(yol).resolve().parent)
    sonuc = []
    for simge in simgeler:
        simge = simge.replace("%dp0%", dizin).replace("%~dp0", dizin)
        if simge == "%_prog%":
            node = Path(dizin) / "node.exe"
            simge = str(node) if node.is_file() else ikili_yolu("node")
        if not simge or "%" in simge:
            return None
        sonuc.append(os.path.normpath(simge))
    if not Path(sonuc[-1]).is_file() or (len(sonuc) > 1 and not Path(sonuc[0]).is_file()):
        return None
    return sonuc


def _cmd_satiri(cmd, ic):
    """`cmd /d /s /c "<ic>"` ham komut satırı (cmd, /s ile dıştaki tırnakları soyar)."""
    return f'"{cmd}" /d /s /c "{ic}"'


_BAT_YASAK = set('&|<>^%"\r\n')


def _bat_sarmala(yol, args):
    """`.cmd`/`.bat` için `cmd /c` satırı; cmd metakarakteri taşıyan argümanda güvenle sarılamaz → hata."""
    kotu = [a for a in args if _BAT_YASAK & set(a)]
    if kotu:
        raise BatKomutHatasi(
            f"{yol} bir .cmd/.bat betiği ve argümanları cmd metakarakteri içeriyor ({kotu[0][:40]!r}): "
            "güvenle çalıştırılamaz; ORVANT_CODEX/ORVANT_CLAUDE ile gerçek .exe yolunu ver")
    return _cmd_satiri(_cmd_exe(), subprocess.list2cmdline([str(yol), *args]))


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def cozumle_argv(argv):
    """Windows'ta `Popen`'a doğrudan verilemeyen argv'yi çalıştırılabilir biçime çevirir; POSIX'te aynen döner.

    - `cmd.exe /d /s /c <komut>` kabuğu → ham komut satırı (list2cmdline `\\"` kaçışını cmd anlamaz),
    - çıplak ad → PATH+PATHEXT ile tam yol (CreateProcess yalnız `.exe` dener),
    - npm/bun `.cmd` gölgesi → gerçek `.exe` / `node betik.js`; çözülemezse `cmd /c` (güvenli argümanlarla),
    - `.ps1` → `powershell -NoProfile -NonInteractive -File`,
    - `python3` → çalışan yorumlayıcı (`sys.executable`).
    Çözülemeyen ad olduğu gibi bırakılır: `Popen` kendi FileNotFoundError'ını verir."""
    if not WINDOWS or isinstance(argv, str) or not argv:
        return argv
    argv = [str(a) for a in argv]
    if (len(argv) == 5 and Path(argv[0]).name.lower() == "cmd.exe"
            and [a.lower() for a in argv[1:4]] == ["/d", "/s", "/c"]):
        return _cmd_satiri(argv[0], argv[4])
    ad = Path(argv[0]).name.lower()
    if ad in ("python3", "python3.exe") and not os.path.dirname(argv[0]):
        return [sys.executable, *argv[1:]]
    yol = ikili_yolu(argv[0])
    if yol is None:
        return argv
    uzanti = Path(yol).suffix.lower()
    if uzanti in (".cmd", ".bat"):
        onek = _npm_golge_onek(yol)
        return onek + argv[1:] if onek else _bat_sarmala(yol, argv[1:])
    if uzanti == ".ps1":
        return [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", yol, *argv[1:]]
    return [yol, *argv[1:]]
