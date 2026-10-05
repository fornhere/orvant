"""Windows kehanet yalıtımı: `codex sandbox` (Windows kısıtlı token, elevated kip) ile fail-closed çalışma.

Linux'taki bwrap/`codex sandbox` yolu `kehanet.py`'de aynen kalır; bu modül yalnız Windows'ta kullanılır.

İlke (Linux ile aynı): yalıtım sandbox İÇİNDE yoklamayla KANITLANMAZSA kehanet koşmaz; yalıtımsız geri
düşüş yoktur. Windows'ta kanıt:
- Kapı dışına yazma: korumalı ve kullanıcının normalde yazabildiği dizinlerde tek `os.open(O_CREAT|O_EXCL)`
  denemesi. `tempfile.mkstemp` KULLANILMAZ: Windows'ta erişim reddini ad çakışması sayıp dakikalarca döner.
  Yalnız `PermissionError` "yazılamaz" kanıtıdır; dizin yoksa ya da başka hata gelirse yalıtım kanıtlanmamıştır.
- Ağ: `socket.if_nameindex()` Windows'ta bütün bağdaştırıcıları döndürür, anlamsızdır. Engel güvenlik duvarı
  kuralıyla gelir; dış TCP denemesinin İZİN hatasıyla (WSAEACCES/10013 → PermissionError) reddi esas kanıttır.
  Zaman aşımı/ulaşılamaz gibi başka hatalar engel kanıtı sayılmaz (unelevated kipte ağ engeli yoktur).

Kurulum gereksinimi: sandbox kullanıcısı (`CodexSandboxOffline`) kullanıcı profilindeki Python'u çalıştıramaz
(`CreateProcessAsUserW failed: 5`). Bu yüzden Python çalışma zamanının küçük bir kopyası gerekir. Kopya
SESSİZCE üretilmez: kullanıcı `python -m orvant_op.mimar.yalitim_windows kur` ile açıkça kurar.

Bilinen sınır (yoklamayla kapatılamaz): sandbox içinde ad çözümleme (DNS) sistemin DNS istemci hizmeti
üzerinden çalışır; benzersiz bir ad dış sunucuya sorulabilir. TCP/UDP doğrudan bağlantı engellidir.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from orvant_op import uyum
from orvant_op.uyum_surec import BatKomutHatasi
from orvant_op.yurutucu import grup_run

WINDOWS = uyum.WINDOWS
PYTHON_ORTAMI = "ORVANT_KEHANET_PYTHON"  # sandbox'ın çalıştırabildiği python.exe (isteğe bağlı)
KUR_KOMUTU = "python -m orvant_op.mimar.yalitim_windows kur"
# Kopyaya alınmayan Lib alt dizinleri (kehanet yalnız standart kütüphane çalıştırır).
LIB_HARIC = frozenset(("site-packages", "test", "tests", "idlelib", "turtledemo", "__pycache__",
                       "ensurepip", "tkinter"))
KOK_DOSYALARI = ("python.exe", "pythonw.exe", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")
# Kapı ağacı dışı TCP denemesi: TEST-NET-1 (RFC 5737), hiçbir yere ulaşmaz.
TCP_HEDEF = ("192.0.2.1", 9)


class YalitimHatasi(RuntimeError):
    """Windows yalıtım kurulumu/yoklaması yapılamadı."""


def _surum_dll():
    return f"python{sys.version_info.major}{sys.version_info.minor}.dll"


def varsayilan_kok():
    """Sandbox kullanıcılarının okuyup çalıştırabildiği varsayılan kopya dizini."""
    return Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / "orvant-py"


def python_yolu():
    """(python.exe yolu, None) ya da (None, neden). Kopya yoksa kurmaz; açıklayıcı neden döner."""
    ham = os.environ.get(PYTHON_ORTAMI, "").strip()
    yol = Path(ham) if ham else varsayilan_kok() / "python.exe"
    if not yol.is_file():
        return None, (f"sandbox'ın çalıştırabileceği Python yok ({yol}); kullanıcı profilindeki Python "
                      f"sandbox kullanıcısınca çalıştırılamaz. Kurmak için: {KUR_KOMUTU}"
                      + ("" if ham else f" (ya da {PYTHON_ORTAMI} ile python.exe yolu verin)"))
    if not (yol.parent / _surum_dll()).is_file():
        return None, (f"sandbox Python sürümü motorla uyuşmuyor: {yol.parent} içinde {_surum_dll()} yok "
                      f"(motor {sys.version_info.major}.{sys.version_info.minor}); yeniden kurun: {KUR_KOMUTU} --zorla")
    return str(yol), None


def _lib_haric(dizin, adlar):
    return [ad for ad in adlar if ad in LIB_HARIC]


def _kullanici_sid():
    proc = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=30)
    sid = proc.stdout.strip().rsplit(",", 1)[-1].strip().strip('"')
    if proc.returncode != 0 or not sid.startswith("S-1-"):
        raise YalitimHatasi(f"kullanıcı SID'i okunamadı: {proc.stderr.strip()[-200:]}")
    return sid


def acl_sertlestir(dizin):
    """Kalıtımı keser: yalnız Yöneticiler/SYSTEM/kullanıcı tam, Users (sandbox kullanıcıları dahil) oku+çalıştır.

    `C:\\Users\\Public` altındaki kalıtılan ACL INTERACTIVE/SERVICE/BATCH'e DEĞİŞTİRME izni verir; başka bir
    oturum kopyadaki python.exe/DLL'leri değiştirip kehaneti ele geçirebilirdi."""
    sid = _kullanici_sid()
    proc = subprocess.run(["icacls", str(dizin), "/inheritance:r", "/grant:r",
                           "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", f"*{sid}:(OI)(CI)F",
                           "*S-1-5-32-545:(OI)(CI)RX", "/Q"],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise YalitimHatasi(f"ACL ayarlanamadı ({dizin}): {(proc.stdout + proc.stderr).strip()[-300:]}")


def kur(hedef=None, *, kaynak=None, zorla=False):
    """Sandbox'ın çalıştırabileceği Python kopyasını AÇIK kullanıcı isteğiyle üretir; python.exe yolunu döner.

    Tarif: python.exe, pythonw.exe, python3.dll, python3XY.dll, vcruntime140*.dll + `DLLs\\` + `Lib\\`
    (site-packages, test, tests, idlelib, turtledemo, __pycache__, ensurepip, tkinter hariç), ~32 MB.
    Kopya önce yan dizinde hazırlanır, ACL'i sertleştirilir, sonra yerine taşınır."""
    if not WINDOWS:
        raise YalitimHatasi("sandbox Python kopyası yalnız Windows'ta gerekir")
    kaynak = Path(kaynak or sys.base_prefix)  # sanal ortam değil, gerçek kurulum
    hedef = Path(hedef) if hedef else varsayilan_kok()
    for ad in ("python.exe", _surum_dll()):
        if not (kaynak / ad).is_file():
            raise YalitimHatasi(f"kaynak Python eksik: {kaynak / ad}")
    for ad in ("DLLs", "Lib"):
        if not (kaynak / ad).is_dir():
            raise YalitimHatasi(f"kaynak Python dizini eksik: {kaynak / ad}")
    if hedef.exists() and not zorla:
        raise YalitimHatasi(f"{hedef} zaten var; yeniden kurmak için --zorla verin")
    gecici = hedef.with_name(hedef.name + ".kuruluyor")
    if gecici.exists():
        shutil.rmtree(gecici)
    gecici.mkdir(parents=True)
    try:
        for ad in (*KOK_DOSYALARI, _surum_dll()):
            if (kaynak / ad).is_file():
                shutil.copy2(kaynak / ad, gecici / ad)
        shutil.copytree(kaynak / "DLLs", gecici / "DLLs", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(kaynak / "Lib", gecici / "Lib", ignore=_lib_haric)
        acl_sertlestir(gecici)
        if hedef.exists():
            shutil.rmtree(hedef)
        uyum.degistir(gecici, hedef)
    except BaseException:
        shutil.rmtree(gecici, ignore_errors=True)
        raise
    return hedef / "python.exe"


def sandbox_modu():
    """codex `config.toml` [windows].sandbox değeri (yalnız teşhis; kanıt yoklamadır) ya da None."""
    kok = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"
    try:
        veri = tomllib.loads((kok / "config.toml").read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    deger = (veri.get("windows") or {}).get("sandbox") if isinstance(veri.get("windows"), dict) else None
    return deger if isinstance(deger, str) else None


def codex_ortami(env):
    """codex için asgari ortam: kehanet ortamı + SystemRoot (node/Winsock), USERPROFILE (codex yapılandırması
    ~/.codex), TEMP/TMP, CODEX_HOME. Kullanıcının diğer değişkenleri (anahtarlar dahil) aktarılmaz."""
    sonuc = dict(env)
    for ad in ("SYSTEMROOT", "USERPROFILE", "TEMP", "TMP", "CODEX_HOME"):
        if os.environ.get(ad):
            sonuc[ad] = os.environ[ad]
    sonuc.setdefault("SYSTEMROOT", r"C:\Windows")
    return sonuc


def isci_ortami():
    """İşçi programı için ortam: işçi/kehanet ortamı miras alınmaz; yalnız Windows'un zorunlu SystemRoot'u."""
    return {"SYSTEMROOT": os.environ.get("SYSTEMROOT") or r"C:\Windows"}


def dis_yollar(agac, *ekler, python=None):
    """Sandbox'ın YAZAMAMASI gereken, host'ta var olan dizinler.

    Zorunlu (sandbox içinde görünmeli ve yazma İZİN hatasıyla reddedilmeli): kullanıcı profili ve TEMP
    (kullanıcı normalde yazar), korumalı Windows dizini (`/etc` karşılığı), sandbox Python kopyası.
    İsteğe bağlı ('?' önekli: kapı ağacının üst dizini ve ekler, ör. plan dizini): sandbox onu hiç
    göremiyorsa da yazamaz (mkdtemp dizinleri Windows'ta yalnız sahibine açıktır)."""
    zorunlu = [Path.home(), Path(uyum.gecici_dizin()), Path(os.environ.get("SYSTEMROOT") or r"C:\Windows")]
    if python:
        zorunlu.append(Path(python).parent)
    sonuc, gorulen = [], set()
    for onek, adaylar in (("", zorunlu), ("?", [Path(agac).resolve().parent, *(Path(e) for e in ekler)])):
        for aday in adaylar:
            anahtar = os.path.normcase(str(aday))
            if aday.is_dir() and anahtar not in gorulen:
                gorulen.add(anahtar)
                sonuc.append(onek + str(aday))
    return sonuc


# Sandbox İÇİNDE çalışır (yoklama betiği ve kehanet önsözü); yalnız os, socket gerekir.
DIS_YALITIM = """def dis_yalitim_dogrula(agac, tur, dis_yollar):
    def yazilabilir(dizin, zorunlu=True):
        # mkstemp yok: Windows'ta erişim reddini çakışma sayıp döner. Tek O_EXCL denemesi.
        # Zorunlu dizin görünmeli ve yalnız İZİN hatası kanıttır; isteğe bağlı dizin görünmüyorsa
        # (host'ta var, sandbox'ta erişilemez) yine de denenir: FileNotFoundError da yazılamaz demektir.
        if zorunlu and not os.path.isdir(dizin):
            raise RuntimeError('yalıtım yok: yoklama dizini yok: ' + dizin)
        yol = os.path.join(dizin, '.orvant-yalitim-%d-%s' % (os.getpid(), os.urandom(6).hex()))
        try:
            fd = os.open(yol, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0))
        except PermissionError:
            return False
        except FileNotFoundError:
            if zorunlu:
                raise RuntimeError('yalıtım yok: yoklama dizini yok: ' + dizin)
            return False
        except OSError as exc:
            raise RuntimeError('yalıtım yok: yazma yoklaması belirsiz (%s): %s' % (type(exc).__name__, dizin))
        os.close(fd)
        try:
            os.unlink(yol)
        except OSError:
            pass
        return True
    if not [d for d in dis_yollar if not d.startswith('?')]:
        raise RuntimeError('yalıtım yok: yoklanacak dış dizin yok')
    for dizin in dis_yollar:
        zorunlu = not dizin.startswith('?')
        dizin = dizin if zorunlu else dizin[1:]
        if yazilabilir(dizin, zorunlu):
            raise RuntimeError('yalıtım yok: kapı dışına yazılabilir: ' + dizin)
    if tur == 'bwrap' and yazilabilir(agac):
        raise RuntimeError('yalıtım yok: kapı ağacı yazılabilir')
    # Arayüz listesi Windows'ta anlamsız; esas kanıt dış TCP'nin güvenlik duvarınca İZİN hatasıyla reddi.
    try:
        bag = socket.create_connection(""" + repr(TCP_HEDEF) + """, timeout=2)
    except PermissionError:
        pass
    except OSError as exc:
        raise RuntimeError('yalıtım yok: dış TCP engeli kanıtlanamadı (%s)' % type(exc).__name__)
    else:
        bag.close()
        raise RuntimeError('yalıtım yok: dış TCP bağlantısı başarılı')
    return True
"""

YOKLAMA = ("import json, os, socket, sys\n" + DIS_YALITIM +
           "try:\n"
           "    dis_yalitim_dogrula(sys.argv[1], 'codex-sandbox', sys.argv[2:])\n"
           "except RuntimeError as exc:\n"
           "    print(json.dumps({'yalitim': False, 'neden': str(exc)}, ensure_ascii=False))\n"
           "    sys.exit(3)\n"
           "print(json.dumps({'yalitim': True, 'dis_yazma': False, 'tcp': False}))\n")


def yokla(codex, python, agac, env, dizinler, zaman_asimi=60):
    """Yalıtımı sandbox İÇİNDE kanıtlar: (True, None) ya da (False, neden). Kanıt yoksa kehanet koşmaz."""
    komut = [codex, "sandbox", "--", python, "-I", "-B", "-X", "utf8", "-", str(Path(agac).resolve()),
             *dizinler]
    try:
        proc = grup_run(komut, cwd=agac, env=env, input=YOKLAMA, timeout=zaman_asimi)
    except subprocess.TimeoutExpired:
        return False, "Windows sandbox yoklaması zaman aşımı"
    except (OSError, BatKomutHatasi) as exc:
        return False, f"codex sandbox başlatılamadı: {exc}"
    satirlar = (proc.stdout or "").strip().splitlines()
    try:
        veri = json.loads(satirlar[-1]) if satirlar else None
    except ValueError:
        veri = None
    if proc.returncode == 0 and veri == {"yalitim": True, "dis_yazma": False, "tcp": False}:
        return True, None
    if isinstance(veri, dict) and veri.get("neden"):
        neden = str(veri["neden"])
    else:
        neden = f"Windows sandbox yoklaması rc={proc.returncode}: {(proc.stderr or '').strip()[-300:]}"
        if "CreateProcessAsUserW" in (proc.stderr or ""):
            neden += (f" (sandbox kullanıcısı {python} yorumlayıcısını çalıştıramıyor; "
                      f"kullanıcı profili dışında bir kopya gerekir: {KUR_KOMUTU})")
    mod = sandbox_modu()
    if mod not in (None, "elevated"):
        neden += (f"; codex [windows] sandbox={mod!r}: bu kipte gerçek ağ engeli yok, "
                  "elevated kurulum gerekir")
    return False, neden


# --- Kehanet önsözüne (sandbox İÇİNDE, audit kancasından ÖNCE) eklenen Windows yardımcıları -----------
# kehanet.py yalnız Windows'ta ekler; Linux önsözü değişmez. Popen audit olayı Windows'ta komutu tek bir
# komut satırı METNİ olarak verir (executable genellikle None); denetim bu metni çözer ve CreateProcess'in
# gerçekte çalıştıracağı ikiliyi bulur.
KORUMA_EKI = r'''import threading
BASLANGIC_ORTAMI = {k.upper(): v for k, v in os.environ.items()}
BASLANGIC_PATH = os.environ.get('PATH', '')
satir_yap = subprocess.list2cmdline
def win_bol(satir):
    # MS C çalışma zamanı kuralları; list2cmdline'ın tersi (ters bölü yalnız tırnaktan önce kaçıştır).
    sonuc, parca, tirnakta, var, i, n = [], [], False, False, 0, len(satir)
    while i < n:
        c = satir[i]
        if c == '\\':
            j = i
            while j < n and satir[j] == '\\':
                j += 1
            if j < n and satir[j] == '"':
                parca.append('\\' * ((j - i) // 2))
                if (j - i) % 2:
                    parca.append('"')
                    j += 1
            else:
                parca.append('\\' * (j - i))
            i, var = j, True
            continue
        if c == '"':
            tirnakta, var, i = not tirnakta, True, i + 1
            continue
        if c in ' \t' and not tirnakta:
            if var:
                sonuc.append(''.join(parca))
            parca, var, i = [], False, i + 1
            continue
        parca.append(c)
        var, i = True, i + 1
    if var:
        sonuc.append(''.join(parca))
    return sonuc
def win_calisan(yurutucu, ad):
    # CreateProcess'in gerçekte çalıştıracağı dosya. executable verilmişse arama yok, cwd'ye göre çözülür;
    # yoksa komutun ilk parçası: uzantısızsa .exe eklenir; yol değilse uygulama dizini, süreç cwd'si,
    # System32, System, Windows, PATH sırası.
    if yurutucu is not None:
        yol = os.path.abspath(os.fsdecode(yurutucu))
        return yol if os.path.isfile(yol) else None
    if not os.path.splitext(os.path.basename(ad))[1]:
        ad += '.exe'
    if os.path.dirname(ad) or ':' in ad:
        yol = os.path.abspath(ad)
        return yol if os.path.isfile(yol) else None
    kok = os.environ.get('SYSTEMROOT') or ''
    for dizin in [os.path.dirname(sys.executable), os.getcwd(), os.path.join(kok, 'System32'),
                  os.path.join(kok, 'System'), kok, *os.environ.get('PATH', '').split(os.pathsep)]:
        dizin = dizin.strip().strip('"')
        if dizin and os.path.isfile(os.path.join(dizin, ad)):
            return os.path.abspath(os.path.join(dizin, ad))
    return None
def win_guvenilir(arac):
    # Güvenilir konum: yalnız sistem dizinleri ve BAŞLANGIÇ PATH'inin mutlak dizinleri (uygulama dizini,
    # cwd ve kapı ağacı hariç); betik PATH'i değiştirse de bu liste değişmez.
    kok = os.environ.get('SYSTEMROOT') or BASLANGIC_ORTAMI.get('SYSTEMROOT', '')
    for dizin in [os.path.join(kok, 'System32'), os.path.join(kok, 'System'), kok,
                  *BASLANGIC_PATH.split(os.pathsep)]:
        dizin = dizin.strip().strip('"')
        if not dizin or not os.path.isabs(dizin) or altinda(os.path.realpath(dizin), ISCI_AGAC):
            continue
        aday = os.path.join(dizin, arac + '.exe')
        if os.path.isfile(aday):
            return aday
    return None
def win_gercek_arac(calisan):
    guvenilir = win_guvenilir(arac_adi(calisan))
    gercek = os.path.realpath(calisan)
    return (bool(guvenilir) and not altinda(gercek, ISCI_AGAC) and
            os.path.normcase(os.path.realpath(guvenilir)) == os.path.normcase(gercek))
def win_popen(args):
    # (executable, komut satırı metni, cwd, env) → POSIX biçimi (çalışan ikili, argv listesi, cwd, env).
    # Yalnız list2cmdline'ın kanonik çıktısı kabul edilir: ayrıştırma CreateProcess'in gördüğüyle aynıdır.
    yurutucu, satir, cwd, ortam = args
    if not isinstance(satir, str):
        raise PermissionError(SALT_OKUNUR)
    parcalar = win_bol(satir)
    if not parcalar or satir_yap(parcalar) != satir or '"' in parcalar[0]:
        raise PermissionError(SALT_OKUNUR)
    if parcalar[0].endswith(('\\', '/')):
        raise PermissionError(SALT_OKUNUR)
    calisan = win_calisan(yurutucu, parcalar[0])
    if calisan is None:
        # İzinli araç kurulu değilse Linux'taki gibi "araç yok" hatası; hiçbir şey başlatılmaz.
        if arac_adi(parcalar[0]) in IZINLI:
            raise FileNotFoundError(2, 'araç bulunamadı', parcalar[0])
        raise PermissionError(SALT_OKUNUR)
    return (calisan, parcalar, cwd, ortam)
WIN_KAYIT_YAZAN = ('winreg.CreateKey', 'winreg.DeleteKey', 'winreg.DeleteValue', 'winreg.SetValue',
                   'winreg.SaveKey', 'winreg.LoadKey', 'winreg.ConnectRegistry',
                   'winreg.DisableReflectionKey', 'winreg.EnableReflectionKey')
# GENERIC_WRITE|GENERIC_ALL|MAXIMUM_ALLOWED|DELETE|WRITE_DAC|WRITE_OWNER|FILE_WRITE_DATA|APPEND|EA|ATTRIBUTES
WIN_YAZMA_ERISIMI = 0x40000000 | 0x10000000 | 0x02000000 | 0x10000 | 0x40000 | 0x80000 | 0x2 | 0x4 | 0x10 | 0x100
def win_olay_denetle(olay, args):
    if olay in ('os.startfile', '_winapi.CreateJunction') or olay in WIN_KAYIT_YAZAN:
        raise PermissionError('kehanet ağ veya sistem komutu kullanamaz')
    if olay == '_winapi.CreateFile' and len(args) > 3 and (args[1] & WIN_YAZMA_ERISIMI or args[3] != 3):
        raise PermissionError('kehanet dosya yazamaz')
def win_is_api():
    # İşçi süreç ağacı için Job Object (KILL_ON_JOB_CLOSE). ctypes audit kancasından ÖNCE yüklenir ve
    # sys.modules'tan çıkarılır: betiğin `import ctypes`'ı yeniden audit 'import' olayına düşer, reddedilir.
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        ntdll = ctypes.WinDLL('ntdll')
        class TEMEL(ctypes.Structure):
            _fields_ = [('a', ctypes.c_int64), ('b', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                        ('c', ctypes.c_size_t), ('d', ctypes.c_size_t), ('e', wintypes.DWORD),
                        ('f', ctypes.c_size_t), ('g', wintypes.DWORD), ('h', wintypes.DWORD)]
        class GENIS(ctypes.Structure):
            _fields_ = [('Basic', TEMEL), ('Io', ctypes.c_uint64 * 6), ('i', ctypes.c_size_t),
                        ('j', ctypes.c_size_t), ('k', ctypes.c_size_t), ('l', ctypes.c_size_t)]
        olustur = k32.CreateJobObjectW
        olustur.argtypes, olustur.restype = [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE
        ayarla = k32.SetInformationJobObject
        ayarla.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        ayarla.restype = wintypes.BOOL
        ata = k32.AssignProcessToJobObject
        ata.argtypes, ata.restype = [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL
        bitir = k32.TerminateJobObject
        bitir.argtypes, bitir.restype = [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL
        kapat = k32.CloseHandle
        kapat.argtypes, kapat.restype = [wintypes.HANDLE], wintypes.BOOL
        devam = ntdll.NtResumeProcess
        devam.argtypes, devam.restype = [wintypes.HANDLE], ctypes.c_long
        byref, boyut = ctypes.byref, ctypes.sizeof
    except Exception:
        return None
    finally:
        for ad in [m for m in sys.modules if m in ('ctypes', '_ctypes') or m.startswith('ctypes.')]:
            del sys.modules[ad]
    def kur(proc):
        # Süreç askıda (CREATE_SUSPENDED) başlatıldı: job'a atanmadan torun üretemez.
        is_ = olustur(None, None)
        if not is_:
            return None
        bilgi = GENIS()
        bilgi.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not ayarla(is_, 9, byref(bilgi), boyut(bilgi)) or not ata(is_, int(proc._handle)):
            kapat(is_)
            return None
        if devam(int(proc._handle)) != 0:
            bitir(is_, 1)
            kapat(is_)
            return None
        return is_
    return kur, (lambda is_: bitir(is_, 1)), kapat
def win_isci_bekle(proc, is_, is_api, veri, zaman_asimi):
    # selectors Windows borularında çalışmaz: okuma/yazma iplikleri. Süre dolunca bütün job (ağaç) ölür.
    _, oldur, birak = is_api
    sinir = 1048576
    cikti = {'stdout': bytearray(), 'stderr': bytearray()}
    kesildi = [False]
    son = time.monotonic() + zaman_asimi
    def oku(boru, ad):
        try:
            while True:
                parca = os.read(boru.fileno(), 65536)
                if not parca:
                    return
                bos = sinir - len(cikti[ad])
                cikti[ad].extend(parca[:bos])
                kesildi[0] = kesildi[0] or len(parca) > bos
        except OSError:
            return
    def yaz():
        try:
            for i in range(0, len(veri), 65536):
                os.write(proc.stdin.fileno(), veri[i:i + 65536])
        except OSError:
            pass  # işçi girdiyi okumadan çıktı (kırık boru)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
    okuyucular = [threading.Thread(target=oku, args=(proc.stdout, 'stdout'), daemon=True),
                  threading.Thread(target=oku, args=(proc.stderr, 'stderr'), daemon=True)]
    iplikler = list(okuyucular)
    if veri:
        iplikler.append(threading.Thread(target=yaz, daemon=True))
    else:
        proc.stdin.close()
    sure_doldu = False
    try:
        for iplik in iplikler:
            iplik.start()
        for iplik in okuyucular:
            iplik.join(max(0.0, son - time.monotonic()))
        if any(iplik.is_alive() for iplik in okuyucular):
            sure_doldu = True  # boruyu açık tutan torun dahil bütün ağaç
            oldur(is_)
        try:
            rc = proc.wait(timeout=max(0.001, son - time.monotonic()))
        except subprocess.TimeoutExpired:
            sure_doldu = True
            oldur(is_)
            rc = proc.wait()
    finally:
        if proc.poll() is None:
            oldur(is_)
            proc.wait()
        birak(is_)  # KILL_ON_JOB_CLOSE: kalan torunlar da ölür
        for iplik in iplikler:
            iplik.join(2)
        for boru in (proc.stdin, proc.stdout, proc.stderr):
            try:
                boru.close()
            except OSError:
                pass
    return {'rc': rc, 'stdout': cikti['stdout'].decode('utf-8', errors='ignore'),
            'stderr': cikti['stderr'].decode('utf-8', errors='ignore'),
            'zaman_asimi': sure_doldu, 'kesildi': kesildi[0]}
'''


def main(argv=None):
    """`python -m orvant_op.mimar.yalitim_windows kur|durum`."""
    for akis in (sys.stdout, sys.stderr):
        if hasattr(akis, "reconfigure"):
            akis.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="python -m orvant_op.mimar.yalitim_windows",
                                 description="Windows kehanet yalıtımı (codex sandbox) kurulum ve yoklama")
    alt = ap.add_subparsers(dest="komut", required=True)
    k = alt.add_parser("kur", help="sandbox'ın çalıştırabileceği Python kopyasını üret (~32 MB)")
    k.add_argument("--hedef", help=f"kopya dizini (varsayılan {varsayilan_kok()})")
    k.add_argument("--zorla", action="store_true", help="var olan kopyayı silip yeniden kur")
    d = alt.add_parser("durum", help="sandbox Python'u ve yalıtım yoklamasını göster")
    d.add_argument("--agac", default=os.getcwd(), help="yoklamanın çalışacağı dizin (kapı ağacı)")
    a = ap.parse_args(argv)
    if a.komut == "kur":
        try:
            yol = kur(a.hedef, zorla=a.zorla)
        except YalitimHatasi as exc:
            print(f"kurulamadı: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"python": str(yol)}, ensure_ascii=False))
        return 0
    from orvant_op import ayarlar
    python, neden = python_yolu()
    sonuc = {"python": python, "codex_windows_sandbox": sandbox_modu()}
    if python is None:
        sonuc.update(yalitim="yok", neden=neden)
    else:
        codex = ayarlar.codex_ikili(a.agac)
        env = codex_ortami({"PATH": os.environ.get("PATH", "")})
        tamam, neden = yokla(codex, python, a.agac, env, dis_yollar(a.agac, python=python))
        sonuc.update(yalitim="codex-sandbox" if tamam else "yok", neden=neden)
    print(json.dumps(sonuc, ensure_ascii=False, indent=2))
    return 0 if sonuc["yalitim"] != "yok" else 1


if __name__ == "__main__":
    sys.exit(main())
