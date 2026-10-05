"""Host üzerindeki skill belgeleri ve araç yollarının salt okunur envanteri."""
import codecs
import json
import os
import re
import shutil
import socket
import stat
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import uyum


ARACLAR = ("ffmpeg", "ffprobe", "uv", "uvx", "python3", "nvidia-smi", "yt-dlp",
           "whisper", "whisper-cli", "tesseract", "git", "node", "npx")
OKUMA_SINIRI = 64 * 1024
GOZLEM_NOTU = ("Host gözlemidir; işçi sandbox'ında GPU ve dosyalara erişim, "
              "ev önbelleklerine yazma ve ağ erişimi farklı olabilir.")

# (ipucu, desen, taranan alan); sıra aynı zamanda kanıt sırasıdır.
GEREKSINIM_KURALLARI = (
    ("uv", r"(?<![\w-])(?:uv\s+(?:run|tool|pip)|uvx)(?![\w-])", "komut"),
    ("--offline", r"(?<!\S)--offline(?![\w-])", "komut"),
    ("paket indirme", r"(?<![\w-])(?:--with\b|uvx\b|pip\s+install\b|npm\s+install\b|npx\b)", "komut"),
    ("huggingface", r"faster[-_]whisper|huggingface|transformers|hf_hub|hf_home", "hepsi"),
    ("whisper", r"openai-whisper|(?<![\w-])whisper\s", "hepsi"),
    ("GPU", r"nvidia-|cuda|cublas|cudnn|gpu", "hepsi"),
    ("ağ ipucu", r"yt-dlp|youtube|https?://|\bcurl\b|\bwget\b", "hepsi"),
    ("URL indirme", r"(?<![\w-])(?:yt-dlp|curl|wget)(?![\w-])[^\n]*https?://[^\s<>]+", "komut"),
)
# ad -> (önbellek, erişim, neden, ağ, GPU)
ARAC_GEREKSINIMLERI = {
    "uv": ("~/.cache/uv", "yazma", "uv ortam/paket önbelleği", "belirsiz", "gerekmez"),
    "uvx": ("~/.cache/uv", "yazma", "uv ortam/paket önbelleği", "belirsiz", "gerekmez"),
    "yt-dlp": (None, None, None, "gerekir", "gerekmez"),
    "whisper": ("~/.cache/whisper", "okuma", "Whisper model önbelleği", "gerekmez", "gerekmez"),
    "whisper-cli": ("~/.cache/whisper", "okuma", "Whisper model önbelleği", "gerekmez", "gerekmez"),
    "nvidia-smi": (None, None, None, "gerekmez", "gerekebilir"),
    "node": ("~/.npm", "yazma", "npm paket önbelleği", "gerekmez", "gerekmez"),
    "npx": ("~/.npm", "yazma", "npm paket önbelleği", "gerekir", "gerekmez"),
}
if uyum.WINDOWS:
    # Windows'ta uv ve npm önbellekleri %LOCALAPPDATA% altındadır (uv\cache, npm-cache); Hugging Face/Whisper
    # önbellekleri Windows'ta da ev altındaki ~/.cache'tir.
    for _ad, _yol in (("uv", "~/AppData/Local/uv/cache"), ("uvx", "~/AppData/Local/uv/cache"),
                      ("node", "~/AppData/Local/npm-cache"), ("npx", "~/AppData/Local/npm-cache")):
        ARAC_GEREKSINIMLERI[_ad] = (_yol,) + ARAC_GEREKSINIMLERI[_ad][1:]
UV_ONBELLEGI = "~/AppData/Local/uv/cache" if uyum.WINDOWS else "~/.cache/uv"

# Windows ev kısaltmaları (komut örneklerinde): "~/" biçimine indirgenir.
WINDOWS_EV_ONEKI = r"(?:~|\$HOME|%USERPROFILE%|\$env:USERPROFILE)"


def hassas_ev_kokleri(ev):
    """Ev altında yetki/proje hedefi olamayacak kökler (altındaki her yol dahil)."""
    ev = Path(ev)
    kokler = [ev / ".ssh", ev / ".gnupg"]
    if uyum.WINDOWS:
        roaming, yerel = ev / "AppData" / "Roaming", ev / "AppData" / "Local"
        # GnuPG (Windows'ta %APPDATA%\gnupg), DPAPI ana anahtarları, kimlik bilgisi ve kasa dizinleri.
        kokler += [roaming / "gnupg", roaming / "Microsoft" / "Credentials", roaming / "Microsoft" / "Protect",
                   roaming / "Microsoft" / "Vault", roaming / "Microsoft" / "SystemCertificates",
                   yerel / "Microsoft" / "Credentials", yerel / "Microsoft" / "Vault"]
    return tuple(kokler)


def ev_kapi_kokleri(ev):
    """Ev altında yalnız kendisi yasak olan geniş kökler (POSIX: ~/.config; Windows: AppData üçlüsü de)."""
    ev = Path(ev)
    if uyum.WINDOWS:
        return (ev / ".config", ev / "AppData", ev / "AppData" / "Roaming", ev / "AppData" / "Local")
    return (ev / ".config",)


def sistem_kokleri():
    """İşletim sistemi/program dizinleri; yalnız Windows'ta (POSIX'te önceki davranış korunur: boş)."""
    if not uyum.WINDOWS:
        return ()
    adlar = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
    return tuple(dict.fromkeys(Path(os.environ[a]) for a in adlar if os.environ.get(a)))


def _anahtar(yol):
    """Büyük/küçük harf duyarsız dosya sisteminde (Windows) karşılaştırma anahtarı."""
    return str(yol).casefold() if uyum.WINDOWS else str(yol)


def gorev_envanteri(calisma, gorev):
    """Kaydedilmiş ilgili çalıştırma yollarını okur; host'u yeniden taramaz."""
    # Eşleştirici de bu modülün gereksinim çıkarıcısını kullanır.
    from .arac_yetki import eslesmeler, gereksinimler

    yol = Path(calisma) / "plan" / "envanter.json"
    if not yol.exists():
        return []
    kayitlar = json.loads(yol.read_text(encoding="utf-8")).get("kayitlar", [])
    ilgili = {e["envanter_id"] for e in eslesmeler(gorev, kayitlar)}
    sonuc = []
    for kayit in kayitlar:
        if kayit["id"] not in ilgili:
            continue
        komut = kayit.get("komut_ornegi") or ""
        # uv run da proje venv'ini seçebilir; sistem Python'u olduğu varsayılamaz.
        ayri_ortam = bool(re.search(
            r"\buv\s+(?:run|tool)\b|\buvx\b|\bvirtualenv\b|\bvenv\b|"
            r"\bVIRTUAL_ENV\b|/bin/activate\b|\bScripts[/\\]activate\b", komut + " " + (kayit.get("yol") or "")))
        sonuc.append({**kayit, "gereksinimler": gereksinimler(kayit),
                      "ayri_python_ortami": ayri_ortam})
    return sonuc


def gereksinim_cikar(kayit):
    """Yalnız kayıt metnini yorumlar; ev, dosya veya araç sorgulamaz."""
    sonuc = {"yollar": [], "ag": "gerekmez", "gpu": "gerekmez", "kanit": []}

    def yol_ekle(yol, erisim, neden):
        for onceki in sonuc["yollar"]:
            if onceki["yol"] == yol:
                if erisim == "yazma":
                    onceki.update(erisim=erisim, neden=neden)
                return
        sonuc["yollar"].append({"yol": yol, "erisim": erisim, "neden": neden})

    if kayit.get("tur") == "arac":
        ad = kayit.get("ad", "").casefold()
        yol, erisim, neden, sonuc["ag"], sonuc["gpu"] = ARAC_GEREKSINIMLERI.get(
            ad, (None, None, None, "gerekmez", "gerekmez"))
        if yol:
            yol_ekle(yol, erisim, neden)
        if ad in ARAC_GEREKSINIMLERI:
            sonuc["kanit"].append(ad)
        return sonuc

    beceri = str(Path(kayit["yol"]).parent) if kayit.get("yol") else None
    # Karşılaştırmalar "/" ayraçlı ve (Windows'ta) harf duyarsızdır; kayda yazılan yol yerel biçimde kalır.
    beceri_pos = _anahtar(Path(kayit["yol"]).parent.as_posix()) if kayit.get("yol") else None
    if beceri:
        yol_ekle(beceri, "okuma", "Beceri dizini")
        sonuc["kanit"].append("SKILL.md")
    if "hata" in kayit:
        sonuc["ag"] = "belirsiz"
        return sonuc
    komut = kayit.get("komut_ornegi") or ""
    hepsi = komut + " " + (kayit.get("aciklama") or "")
    ipuclari = {}
    for ad, desen, alan in GEREKSINIM_KURALLARI:
        es = re.search(desen, komut if alan == "komut" else hepsi, re.IGNORECASE)
        if es:
            ipuclari[ad] = True
            sonuc["kanit"].append(es.group().strip())
    ev_deseni = r"(?:~/|\$HOME/)" if not uyum.WINDOWS else WINDOWS_EV_ONEKI + r"[/\\]"
    for es in re.finditer(ev_deseni + r"[^\s\"'`<>;|&()]+", komut):
        yol = es.group().rstrip(",.")
        if uyum.WINDOWS:
            yol = "~/" + re.sub(r"^" + WINDOWS_EV_ONEKI + r"[/\\]", "", yol).replace("\\", "/")
        else:
            yol = yol.replace("$HOME/", "~/", 1)
        # Mutlak beceri yolunun evini okumadan, aynı göreli son parçayı tanı.
        parcalar = Path(yol[2:]).parts
        yol_pos = _anahtar(yol)
        beceri_altinda = beceri_pos and (yol_pos == beceri_pos or yol_pos.startswith(beceri_pos + "/"))
        for i in range(1, len(parcalar) + 1):
            if beceri_pos and beceri_pos.endswith("/" + _anahtar("/".join(parcalar[:i]))):
                beceri_altinda = True
                break
        if not beceri_altinda:
            yol_ekle(yol, "okuma", "Komut örneğindeki ev yolu")
    if "uv" in ipuclari:
        yol_ekle(UV_ONBELLEGI, "yazma", "uv ortam/paket önbelleği")
    if "huggingface" in ipuclari:
        yol_ekle("~/.cache/huggingface", "okuma" if "--offline" in ipuclari else "yazma",
                 "Hugging Face model önbelleği")
    if "whisper" in ipuclari:
        yol_ekle("~/.cache/whisper", "okuma", "Whisper model önbelleği")
    if "GPU" in ipuclari:
        sonuc["gpu"] = "gerekebilir"
    if "--offline" in ipuclari:
        sonuc["ag"] = "gerekmez"
    elif "paket indirme" in ipuclari or "URL indirme" in ipuclari:
        sonuc["ag"] = "gerekir"
    elif "uv" in ipuclari or "ağ ipucu" in ipuclari or "huggingface" in ipuclari:
        sonuc["ag"] = "belirsiz"
    return sonuc


def _skaler(deger):
    deger = deger.strip()
    if deger.startswith(("'", '"')):
        if len(deger) < 2 or deger[-1] != deger[0]:
            raise ValueError("kapanmamış frontmatter tırnağı")
        if deger[0] == "'":
            return deger[1:-1].replace("''", "'")
        return re.sub(r'\\(["\\])', lambda es: es.group(1), deger[1:-1])
    return deger


def _ayristir(metin):
    """Basit YAML önbilgi skalerleri; geri kalan belge yalnız kod örneği için okunur."""
    # CRLF dosyalarda (Windows) kod çiti kapanışındaki `$` "\r" yüzünden eşleşmezdi.
    metin = metin.replace("\r\n", "\n").replace("\r", "\n")
    satirlar = metin.splitlines()
    alanlar = {}
    govde = metin
    if satirlar and satirlar[0].strip() == "---":
        son = next((i for i in range(1, len(satirlar)) if satirlar[i].strip() == "---"), None)
        if son is None:
            raise ValueError("kapanmamış frontmatter")
        i = 1
        while i < son:
            satir = satirlar[i]
            i += 1
            if not satir.strip() or satir.lstrip().startswith("#") or satir[0].isspace():
                continue
            es = re.fullmatch(r"([\w-]+):\s*(.*)", satir)
            if not es:
                raise ValueError("bozuk frontmatter satırı")
            anahtar, deger = es.groups()
            if deger in (">", "|"):
                blok = []
                while i < son and (not satirlar[i].strip() or satirlar[i][0].isspace()):
                    blok.append(satirlar[i].strip())
                    i += 1
                alanlar[anahtar] = (" " if deger == ">" else "\n").join(blok).strip()
            else:
                alanlar[anahtar] = _skaler(deger)
        govde = "\n".join(satirlar[son + 1:])
    kod = re.search(r"^[ \t]*```[^`\n]*\n(.*?)^[ \t]*```[ \t]*$", govde,
                    re.MULTILINE | re.DOTALL)
    return alanlar, kod.group(1).strip()[:800] if kod else None


def _skill(yol, kok_turu):
    kayit = {"tur": "skill", "ad": yol.parent.name, "aciklama": "",
             "komut_ornegi": None, "yol": str(yol.absolute()), "kok_turu": kok_turu}
    try:
        if not stat.S_ISREG(yol.stat().st_mode):
            raise ValueError("SKILL.md normal dosya değil")
        with yol.open("rb") as fh:
            ham = fh.read(OKUMA_SINIRI)
        # Kesim çok baytlı karakterin ortasındaysa yalnız son eksik karakteri bırak.
        metin = codecs.getincrementaldecoder("utf-8-sig")().decode(
            ham, final=len(ham) < OKUMA_SINIRI)
        alanlar, komut = _ayristir(metin)
        kayit.update(ad=alanlar.get("name") or yol.parent.name,
                     aciklama=alanlar.get("description", "")[:600], komut_ornegi=komut)
    except (OSError, UnicodeError, ValueError) as exc:
        kayit["hata"] = f"{type(exc).__name__}: {exc}"
    kayit["id"] = f"skill:{kok_turu}:{kayit['ad']}"
    return kayit


def _arac_yolu(ad, path):
    yol = shutil.which(ad, path=path)
    if not uyum.WINDOWS or ad != "python3":
        return yol
    # Windows'ta `python3` yoktur; PATH'teki WindowsApps takma adı (Mağaza yönlendirmesi) sessizce bozuk olabilir.
    # Gerçek yorumlayıcı `python`/`py` ile aranır ve takma ad yolları sayılmaz.
    for aday in (yol, shutil.which("python", path=path), shutil.which("py", path=path)):
        if aday and "windowsapps" not in aday.casefold():
            return aday
    return None


def envanter_cikar(*, skill_dizinleri=None, path=None, host=None):
    """Hiçbir araç çalıştırmaz; PATH yalnız shutil.which ile sorgulanır."""
    if skill_dizinleri is None:
        skill_dizinleri = [Path.home() / ".claude/skills", Path.home() / ".codex/skills"]
    if path is None:
        path = os.environ.get("PATH", "")
    baglam = {"ortam": "host", "sandbox": False,
              "host": socket.gethostname() if host is None else host}
    kayitlar, gorulen_yollar, ids = [], set(), set()
    for dizin in skill_dizinleri:
        kok = Path(dizin).expanduser().absolute()
        parcalar = [p.casefold() for p in kok.parts] if uyum.WINDOWS else kok.parts
        kok_turu = ("claude" if ".claude" in parcalar else
                    "codex" if ".codex" in parcalar else "diger")
        try:
            adaylar = sorted(kok.iterdir())
        except OSError:
            continue
        for aday in adaylar:
            if aday.name.startswith(".") or not aday.is_dir():
                continue
            yol = aday / "SKILL.md"
            try:
                yol.stat()
            except FileNotFoundError:
                if not uyum.baglanti_mi(yol):
                    continue
            except OSError:
                pass  # Erişim hatası da skill kaydıdır.
            if _anahtar(yol) in gorulen_yollar:
                continue
            gorulen_yollar.add(_anahtar(yol))
            kayit = _skill(yol, kok_turu)
            temel_id, sayi = kayit["id"], 2
            # Aynı türde kökler veya aynı name alanı kimlikleri çakıştırabilir.
            while kayit["id"] in ids:
                kayit["id"] = f"{temel_id}:{sayi}"
                sayi += 1
            ids.add(kayit["id"])
            kayitlar.append(kayit)
    for ad in ARACLAR:
        yol = _arac_yolu(ad, path)
        kayitlar.append({"id": f"arac:{ad}", "tur": "arac", "ad": ad,
                         "yol": yol, "mevcut": yol is not None})
    for kayit in kayitlar:
        kayit.update(kaynak_turu="yerel_gozlem", gozlem_baglami=dict(baglam))
        kayit["not"] = GOZLEM_NOTU
        kayit["gereksinimler"] = gereksinim_cikar(kayit)
    return {"surum": 1, "t": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "gozlem_baglami": baglam, "kayitlar": kayitlar}
