"""Komut metni uyumu: komut ayrıştırma ve kullanıcıya gösterilen/çalıştırılan komut metinleri.

Linux/macOS'ta `shlex` (POSIX) kullanılır ve `python3` adı korunur; o dallar eskisiyle bire bir aynıdır.
Windows'ta:
- ters eğik çizgili yollar `shlex.split` ile bozulur; bu yüzden `bol` Windows'a uygun ayrıştırır
  (CommandLineToArgvW kuralları: ters eğik çizgi yalnız `"` öncesinde kaçıştır; ek olarak POSIX tarzı
  tek tırnak, plan komutları çoğu zaman o biçimde yazıldığı için desteklenir),
- `python3` komutu yoktur (Microsoft Store takma adı sessizce başarısız olur); çalışan yorumlayıcı
  (`sys.executable`) kullanılır,
- komut metinleri `birlestir`/`tirnakla` ile CreateProcess kurallarına göre birleştirilir ve
  `bol(birlestir(x)) == x` gidiş-dönüşü korunur.

Python adı eşleştirme kararı (Windows):
- Plan/kabul komutları taşınabilir veridir; metinleri yeniden yazılmaz, `python3 -m pytest` olarak kalır.
- ÇÖZÜMLEME (yol çıkarma, `mimar/dogrulama.py`) `python`, `python3`, `python3.12`, `py`, `py -3`, `PYTHON.EXE`
  ve tam yol (`C:\\...\\python.exe`) adlarını aynı "python" sayar; `-m` modül adı bundan sonra aranır.
  Yalnız yol çıkarmadır, bir izin kararı değildir.
- ÇALIŞTIRMA (`argv_uyarla`) yalnız YOLSUZ `python`/`python3`/`py` adlarını `sys.executable` ile değiştirir.
  Gerekçe: `python3` Windows'ta yoktur ya da Store takma adıdır; PATH'teki `python` ise Orvant'ı çalıştıran
  yorumlayıcıdan (venv) farklı olabilir. Yol içeren ad (`.\\venv\\Scripts\\python.exe`) ve sürüm seçen
  `py -3.11` olduğu gibi kalır: kullanıcı açıkça seçmiştir, değiştirmek yetki genişletmek olurdu.
- İzin listeleri (yoklama listesi, kabuk yerleşikleri, `sh`/`bash` gibi) ASLA gevşetilmez; yalnız
  ad kanonikleştirilir (`GIT.EXE` -> `git`, yol içeren ad kanonikleştirilmez).
"""
import os
import re
import shlex
import shutil
import sys
from pathlib import PurePosixPath, PureWindowsPath

WINDOWS = os.name == "nt"
_BOSLUK = " \t\r\n"
_ISLEC = ";&|"
# Windows'ta tırnaksız yazılırsa cmd/PowerShell için özel anlamı olan karakterler (boşluk ayrıca denetlenir).
_OZEL = set('<>|&^%();,"\'')
_PY_LAUNCHER_BAYRAGI = re.compile(r"-(?:\d+(?:\.\d+)?(?:-\d+)?|V:\S*)", re.IGNORECASE)
_PY_AD = re.compile(r"py|python(?:3(?:\.\d+)?)?")


def python_argv():
    """Orvant'ın ya da yardımcı betiklerin çalıştırılacağı yorumlayıcı argv'si."""
    return [sys.executable] if WINDOWS else ["python3"]


def _win_tirnakla(parca):
    """Tek argümanı CreateProcess kurallarıyla çift tırnağa alır (`"` öncesi ters eğik çizgiler katlanır)."""
    cikti, ters = ['"'], 0
    for c in parca:
        if c == "\\":
            ters += 1
        elif c == '"':
            cikti.append("\\" * (ters * 2 + 1) + '"')
            ters = 0
        else:
            cikti.append("\\" * ters + c)
            ters = 0
    cikti.append("\\" * (ters * 2) + '"')
    return "".join(cikti)


def tirnakla(parca):
    parca = str(parca)
    if not WINDOWS:
        return shlex.quote(parca)
    if parca and not any(c in _BOSLUK or c in _OZEL for c in parca):
        return parca
    return _win_tirnakla(parca)


def birlestir(parcalar):
    """argv listesini kabuk/komut satırı metnine çevirir (kullanıcıya gösterim ve `shell=True` için)."""
    parcalar = [str(p) for p in parcalar]
    return " ".join(tirnakla(p) for p in parcalar) if WINDOWS else shlex.join(parcalar)


def _win_bol(komut, islecli):
    """Windows komut satırı ayrıştırıcısı. Kapanmayan ÇİFT tırnakta `ValueError` (shlex ile aynı sözleşme);
    kapanmayan tek tırnak karakter olarak kalır (Windows ayrıştırıcısı tek tırnağı tanımaz)."""
    parcalar, cur, var, dq, i, n = [], [], False, False, 0, len(komut)

    def bitir():
        nonlocal cur, var
        if var:
            parcalar.append("".join(cur))
        cur, var = [], False

    while i < n:
        c = komut[i]
        if c == "\\":
            j = i
            while j < n and komut[j] == "\\":
                j += 1
            ters = j - i
            var = True
            if j < n and komut[j] == '"':
                cur.append("\\" * (ters // 2))
                if ters % 2:  # tek sayıda: son ters eğik çizgi tırnağı kaçırır
                    cur.append('"')
                    j += 1
            else:
                cur.append("\\" * ters)
            i = j
        elif c == '"':
            var = True
            if dq and komut[i + 1:i + 2] == '"':  # tırnak içinde "" -> değişmez tırnak
                cur.append('"')
                i += 2
            else:
                dq = not dq
                i += 1
        elif dq:
            cur.append(c)
            var = True
            i += 1
        elif c in _BOSLUK:
            bitir()
            i += 1
        elif c == "'" and (not var or cur[-1:] == ["="]) and komut.find("'", i + 1) != -1:
            # POSIX tarzı tek tırnak yalnız sözcük başında ya da `=` sonrasında; `O'Brien` gibi yollar bozulmaz.
            son = komut.find("'", i + 1)
            cur.append(komut[i + 1:son])
            var = True
            i = son + 1
        elif islecli and c in _ISLEC:
            bitir()
            j = i
            while j < n and komut[j] in _ISLEC:
                j += 1
            parcalar.append(komut[i:j])
            i = j
        else:
            cur.append(c)
            var = True
            i += 1
    if dq:
        raise ValueError("No closing quotation")
    bitir()
    return parcalar


def bol(komut):
    """Komut metnini argv'ye böler. POSIX'te `shlex.split`. Windows'ta ters eğik çizgiler yol ayırıcıdır,
    kaçış değil (yalnız `"` öncesinde kaçıştır); çift ve tek tırnaklar soyulur (tekin kapanmaması hata değildir).
    Kapanmayan çift tırnakta `ValueError`."""
    if not WINDOWS:
        return shlex.split(komut)
    return _win_bol(komut, False)


def bol_islecli(komut):
    """`bol` gibi, ama tırnak dışı kabuk işleçlerini (`;`, `&`, `|`, `&&`, `||`) ayrı sözcük yapar;
    tırnak içindekiler korunur. Kapanmayan çift tırnakta `ValueError`."""
    if WINDOWS:
        return _win_bol(komut, True)
    parcalar = shlex.split(komut)
    if any(c in komut for c in _ISLEC):
        lexer = shlex.shlex(komut, posix=True, punctuation_chars=_ISLEC)
        lexer.whitespace_split = True
        lexer.commenters = ""
        parcalar = list(lexer)
    return parcalar


def komut_adi(belirtec):
    """Komut sözcüğünün yolsuz adı. Windows'ta büyük/küçük harf duyarsız ve `.exe` atılmış (`C:\\x\\GIT.EXE` -> `git`)."""
    ad = (PureWindowsPath if WINDOWS else PurePosixPath)(belirtec).name
    if WINDOWS:
        ad = ad.lower()
        if ad.endswith(".exe"):
            ad = ad[:-4]
    return ad


def yolsuz_ad(belirtec):
    """`belirtec` yol içermeyen çıplak komut adıysa `komut_adi`, değilse `None` (izin listeleri için)."""
    if not belirtec or "/" in belirtec or (WINDOWS and ("\\" in belirtec or ":" in belirtec)):
        return None
    return komut_adi(belirtec)


def python_adi_mi(belirtec):
    """Sözcük bir Python yorumlayıcısı mı? POSIX'te `python`/`python3` birebir; Windows'ta ek olarak
    `py`, `python3.12`, `.exe` uzantısı, büyük/küçük harf ve tam yol (yalnız çözümleme için)."""
    if not WINDOWS:
        return belirtec in ("python", "python3")
    return bool(_PY_AD.fullmatch(komut_adi(belirtec)))


def python_modul_indeksi(belirtecler):
    """`<python> [launcher bayrakları] -m <modül>` desenindeki modül sözcüğünün indeksi (yoksa `None`)."""
    for i in range(len(belirtecler)):
        if not python_adi_mi(belirtecler[i]):
            continue
        j = i + 1
        if WINDOWS and komut_adi(belirtecler[i]) == "py":
            while j < len(belirtecler) and _PY_LAUNCHER_BAYRAGI.fullmatch(belirtecler[j]):
                j += 1
        if j + 1 < len(belirtecler) and belirtecler[j] == "-m":
            return j + 1
    return None


def argv_uyarla(argv):
    """Çalıştırılacak argv'de yolsuz `python`/`python3`/`py` adını `sys.executable` ile değiştirir (yalnız Windows).

    Sürüm seçen `py -3.11` ve yol içeren ad değişmez. POSIX'te argv aynen döner."""
    if not WINDOWS or not argv:
        return list(argv)
    ad = yolsuz_ad(argv[0])
    if ad is None or not _PY_AD.fullmatch(ad):
        return list(argv)
    if ad == "py" and len(argv) > 1 and _PY_LAUNCHER_BAYRAGI.fullmatch(argv[1]):
        return list(argv)
    return [sys.executable, *argv[1:]]


def _gosterim_python():
    """Kullanıcıya gösterilen komutta Python adı: PATH'teki `python` bu yorumlayıcıysa çıplak ad, değilse tam yol."""
    bulunan = shutil.which("python")
    if bulunan:
        try:
            if os.path.samefile(bulunan, sys.executable):
                return "python"
        except OSError:
            pass
    return sys.executable


def orvant_komutu(parcalar=(), *, modul="orvant_op"):
    """Kullanıcıya gösterilecek `python -m orvant_op ...` komut metni (POSIX'te `python3 -m orvant_op ...`).

    Windows'ta Python yolu boşluk içerirse komut `& "yol" ...` biçiminde PowerShell'de çalışır
    (cmd.exe'de `&` öneki gerekmez; çıplak `python` adı kullanılabiliyorsa zaten eklenmez)."""
    if not WINDOWS:
        return birlestir(["python3", "-m", modul, *parcalar])
    komut = birlestir([_gosterim_python(), "-m", modul, *parcalar])
    return "& " + komut if komut.startswith('"') else komut


def prog(*alt, modul="orvant_op"):
    """`argparse` `prog=` metni: `python3 -m orvant_op karsila` (Windows'ta `python -m ...`)."""
    return " ".join(["python" if WINDOWS else "python3", "-m", modul, *alt])


def konsolu_utf8_yap():
    """Windows'ta stdout/stderr/stdin'i UTF-8'e çevirir (konsol/boru cp1254 ya da cp437 olabilir; Türkçe
    metin `UnicodeEncodeError` vermesin, boruya giden JSON baytları UTF-8 olsun). Yazma hatasında karakter `?` olur.
    POSIX'te hiçbir şey yapmaz. Yinelenen çağrı zararsızdır. Değiştirilen akış sayısını döndürür."""
    if not WINDOWS:
        return 0
    sayi = 0
    for ad in ("stdout", "stderr", "stdin"):
        akis = getattr(sys, ad, None)
        degistir = getattr(akis, "reconfigure", None)
        if degistir is None:
            continue
        try:
            if ad == "stdin":
                # PowerShell/`>` dosyaları BOM'lu UTF-8 üretebilir; konsolda kodlama zaten UTF-8'dir.
                degistir(encoding="utf-8" if akis.isatty() else "utf-8-sig", errors="replace")
            else:
                degistir(encoding="utf-8", errors="replace")
            sayi += 1
        except (OSError, ValueError):  # okuma başlamış stdin ya da kapalı akış: olduğu gibi bırak
            pass
    return sayi


def utf8_ortam(env=None):
    """Alt Python süreçleri için UTF-8 zorlayan ortam kopyası (Windows'ta `PYTHONUTF8`/`PYTHONIOENCODING`);
    POSIX'te `env` (yoksa `os.environ`) kopyası değişmeden döner."""
    sonuc = dict(os.environ if env is None else env)
    if WINDOWS:
        sonuc.setdefault("PYTHONUTF8", "1")
        sonuc.setdefault("PYTHONIOENCODING", "utf-8")
    return sonuc
