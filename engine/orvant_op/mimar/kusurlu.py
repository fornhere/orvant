"""Model kullanmadan kusurlu çıktılar ve kehanet denetim kanıtları üretir."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

VARYANT_ADLARI = ("bos", "iskelet", "sabit", "kismi")
DIZIN = "<dizin>"


def _goreli(yol):
    p = Path(yol)
    if not str(yol).strip() or p.is_absolute() or ".." in p.parts or p == Path("."):
        raise ValueError(f"güvensiz çıktı yolu: {yol!r}")
    # Git yönetim verileri bir çıktı değildir; worktree .git dosyası da korunur.
    if ".git" in p.parts:
        raise ValueError(f"git yönetim yolu çıktı olamaz: {yol!r}")
    return p


def _alan_agaci(alanlar):
    """Ebeveyn bildirimi çocukları ezmeden alan yollarını birleştirir."""
    kok = {}
    for alan in alanlar:
        dugum = kok
        for parca in alan["ad"].split("."):
            es = re.fullmatch(r"([^.\[\]\s]+)((?:\[\])*)", parca)
            if not es:
                raise ValueError(f"geçersiz alan yolu: {alan['ad']!r}")
            dugum = dugum.setdefault("alanlar", {}).setdefault(es[1], {})
            for _ in range(len(es[2]) // 2):
                dugum = dugum.setdefault("oge", {})
        dugum["tip"] = alan["tip"]
        dugum["zorunlu"] = alan.get("zorunlu", False)
    return kok


def alan_nesnesi(alanlar, *, sabit=False):
    kok = _alan_agaci(alanlar)

    def deger(d):
        tip = d.get("tip")
        if "oge" in d or tip == "array":
            return [deger(d["oge"]) if d.get("oge") else "x"] if sabit else []
        if "alanlar" in d or tip == "object":
            return {ad: deger(alt) for ad, alt in d.get("alanlar", {}).items()}
        return {"string": "x" if sabit else "", "number": 1 if sabit else 0,
                "integer": 1 if sabit else 0, "boolean": sabit, "null": None}.get(tip, "x" if sabit else {})
    return deger(kok) if kok else {}


def referans_denetle(sozlesme, dosyalar):
    """Üretim iddiasını dosya/alan ağacına göre deterministik doğrular."""
    def atla(neden):
        return {"durum": "atlandi", "neden": neden, "dosyalar": []}

    def gerekli(d):
        return (d.get("zorunlu", False) or any(gerekli(a) for a in d.get("alanlar", {}).values())
                or ("oge" in d and gerekli(d["oge"])))

    def dogrula(v, d, yol):
        tip = "array" if "oge" in d else "object" if "alanlar" in d else d.get("tip")
        tipler = {"array": (list,), "object": (dict,), "string": (str,), "number": (int, float),
                  "integer": (int,), "boolean": (bool,), "null": (type(None),)}
        if tip and type(v) not in tipler[tip]:
            raise ValueError(f"referans alan tipi uyumsuz: {yol} ({tip})")
        for ad, alt in d.get("alanlar", {}).items():
            if ad not in v:
                if gerekli(alt):
                    raise ValueError(f"referans zorunlu alan eksik: {yol}.{ad}")
            else:
                dogrula(v[ad], alt, f"{yol}.{ad}")
        if "oge" in d:
            for i, oge in enumerate(v):
                dogrula(oge, d["oge"], f"{yol}[{i}]")

    try:
        beklenen = (sozlesme or {}).get("dosyalar", [])
        if not beklenen:
            raise ValueError("çıktı sözleşmesi yok veya boş")
        yollar = {str(_goreli(d["yol"])): d for d in beklenen}
        if any(d["bicim"] in ("ikili", "dizin") for d in beklenen):
            return atla("ikili/dizin çıktı referansla üretilemez")
        bulunan = set()
        for d in dosyalar:
            yol = str(_goreli(d["yol"]))
            if yol not in yollar or yol in bulunan:
                raise ValueError(f"referans yolu sözleşmede yok veya tekrarlı: {yol}")
            bulunan.add(yol)
            if not isinstance(d["icerik"], str):
                raise ValueError(f"referans içeriği metin değil: {yol}")
            bicim = yollar[yol]["bicim"]
            if bicim == "metin":
                if not d["icerik"].strip():
                    raise ValueError(f"referans metni boş: {yol}")
                continue
            nesneler = ([json.loads(d["icerik"])] if bicim == "json" else
                        [json.loads(s) for s in d["icerik"].splitlines() if s.strip()])
            agac = _alan_agaci(yollar[yol].get("alanlar", []))
            for nesne in nesneler:
                if bicim == "jsonl" and not isinstance(nesne, dict):
                    raise ValueError(f"referans JSONL satırı nesne değil: {yol}")
                dogrula(nesne, agac, yol)
        if bulunan != set(yollar):
            raise ValueError("referans dosyası eksik: " + ", ".join(sorted(set(yollar) - bulunan)))
    except (ValueError, TypeError, KeyError) as exc:
        return atla(str(exc))
    return {"durum": "uretildi", "neden": "", "dosyalar": dosyalar}


def referans_oku(yol, sozlesme, sozlesme_sha256):
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
        if veri.get("sozlesme_sha256") != sozlesme_sha256:
            return {"durum": "atlandi", "neden": "referans eski sözleşmeye ait", "dosyalar": []}
        if veri.get("durum") == "atlandi":
            return veri
        return referans_denetle(sozlesme, veri["dosyalar"])
    except FileNotFoundError:
        return {"durum": "atlandi", "neden": "referans yok", "dosyalar": []}
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        return {"durum": "atlandi", "neden": f"referans okunamadı: {exc}", "dosyalar": []}


def _referans_varyanti(sozlesme, referans):
    yazilacak = {d["yol"]: None for d in sozlesme["dosyalar"]}
    yazilacak.update({d["yol"]: d["icerik"].encode("utf-8") for d in referans["dosyalar"]})
    return {"yazilacak": yazilacak}


def _dizi_varyanti(sozlesme, referans):
    """Yalnız dizileri bozar; kaynak ve kanıt alanlarını referanstan korur."""
    varyant = _referans_varyanti(sozlesme, referans)
    degisti = False

    def boz(v, ornek):
        nonlocal degisti
        if isinstance(v, list):
            sabit = ornek[0] if isinstance(ornek, list) and ornek else "x"
            def farkli(x, n):
                if isinstance(x, dict):
                    return {k: farkli(a, n) for k, a in x.items()}
                if isinstance(x, list):
                    return [farkli(a, n) for a in x]
                if type(x) is bool:
                    return bool(n % 2)
                if isinstance(x, str):
                    return "sabit" + str(n)
                if type(x) in (int, float):
                    return n
                return x
            # Sonlu referans öğelerinin hiçbirini tekrar etme.
            for n in range(1, len(v) + 2):
                aday = farkli(sabit, n)
                if aday not in v:
                    degisti = True
                    return [aday]
            return v  # Yalnız null/boş nesne gibi farklı öğesi olmayan yapı.
        if isinstance(v, dict):
            return {k: boz(a, ornek.get(k) if isinstance(ornek, dict) else None) for k, a in v.items()}
        return v

    for d in sozlesme["dosyalar"]:
        if d["bicim"] not in ("json", "jsonl") or not any(
                a["tip"] == "array" or "[]" in a["ad"] for a in d.get("alanlar", [])):
            continue
        ham = varyant["yazilacak"][d["yol"]].decode("utf-8")
        ornek = alan_nesnesi(d["alanlar"], sabit=True)
        nesneler = [json.loads(ham)] if d["bicim"] == "json" else [json.loads(s) for s in ham.splitlines() if s.strip()]
        varyant["yazilacak"][d["yol"]] = "\n".join(
            json.dumps(boz(v, ornek), ensure_ascii=False) for v in nesneler).encode("utf-8")
    return varyant if degisti else None


def pozitif_denetimi(yol, sozlesme, agac_ac, girdiler, referans, zaman_asimi=60):
    from .kehanet import calistir_kehanet
    if not referans or referans["durum"] != "uretildi":
        return {"durum": "atlandi", "neden": (referans or {}).get("neden", "referans yok"), "kanit": []}
    from .izlenebilirlik import calisma_denetimi
    with agac_ac() as agac:
        uygula(agac, _referans_varyanti(sozlesme, referans))
        sonuc = calistir_kehanet(yol, agac, girdiler, zaman_asimi=zaman_asimi)
    negatif = negatif_kontrol(sonuc)
    iyol = Path(yol).with_suffix(".iddialar.json")
    try:
        iddialar = json.loads(iyol.read_text(encoding="utf-8")) if iyol.exists() else None
    except ValueError:
        iddialar = None
    # G-131: izinsiz araç ve atlanan kontrol referansta başarı sayılmaz; ölçüt gevşetilmez.
    kapi = calisma_denetimi(sonuc, iddialar)
    return {**negatif, "durum": "gecti" if sonuc.get("gecti") and not kapi else "reddetti",
            "kontroller": [k["ad"] for k in sonuc.get("kontroller", []) if k.get("gecti") is False],
            "kapi_hatalari": kapi}


def ornege_ozel_denetimi(yol, sozlesme, agac_ac, girdiler, referans, zaman_asimi=60):
    from .kehanet import calistir_kehanet
    altlar = []

    def kos(ad, varyant, kaynaklar):
        with agac_ac() as agac:
            uygula(agac, varyant)
            sonuc = calistir_kehanet(yol, agac, kaynaklar, zaman_asimi=zaman_asimi)
        negatif = negatif_kontrol(sonuc)
        altlar.append({"ad": ad, "sonuc": "gecti" if sonuc.get("gecti") else
                       "reddetti_hata" if negatif["durum"] == "kehanet_hatasi" else "reddetti",
                       "ayrinti": "; ".join(negatif["kanit"])})

    if not referans or referans["durum"] != "uretildi":
        return {"ad": "ornege_ozel", "sonuc": "atlandi", "ayrinti": (referans or {}).get("neden", "referans yok")}
    varyant = _dizi_varyanti(sozlesme, referans)
    if varyant:
        kos("dizi_tek_sabit_oge", varyant, girdiler)
    else:
        altlar.append({"ad": "dizi_tek_sabit_oge", "sonuc": "atlandi", "ayrinti": "değiştirilebilir dizi alanı yok"})
    dosyalar = [Path(g) for g in girdiler if Path(g).is_file()]
    if dosyalar:
        # Büyük girdiler /tmp'ye taşınmaz; kaynak ve çıktı ağaçlarına dokunulmaz.
        with tempfile.TemporaryDirectory(prefix="ornege-ozel-", dir=yol.parent) as gecici:
            kopyalar = []
            for i, g in enumerate(dosyalar):
                kopya = Path(gecici) / f"degismis-{i}-{g.name}"
                shutil.copyfile(g, kopya)
                with kopya.open("ab") as f:
                    f.write(b"\nORVANT-ornek-kontrolu\n")
                kopyalar.append(str(kopya))
            kos("girdiden_bagimsiz", _referans_varyanti(sozlesme, referans), kopyalar)
    else:
        altlar.append({"ad": "girdiden_bagimsiz", "sonuc": "atlandi", "ayrinti": "girdi dosyası yok"})
    etkin = [a for a in altlar if a["sonuc"] != "atlandi"]
    durum = ("gecti" if any(a["sonuc"] == "gecti" for a in etkin) else
             "reddetti_hata" if etkin and all(a["sonuc"] == "reddetti_hata" for a in etkin) else
             "reddetti" if etkin else "atlandi")
    return {"ad": "ornege_ozel", "sonuc": durum, "alt_kontroller": altlar,
            "ayrinti": "; ".join(f"{a['ad']}: {a['sonuc']} ({a['ayrinti']})" for a in altlar)}


def _icerik(dosya, ad, alanlar=None):
    bicim = dosya["bicim"]
    if ad == "bos":
        return DIZIN if bicim == "dizin" else (b"{}" if bicim == "json" else b"")
    if bicim == "dizin" or (bicim == "ikili" and ad == "iskelet"):
        raise ValueError(f"{bicim} için {ad} uygulanamıyor")
    if bicim == "ikili":
        return b"x"
    if bicim == "metin" and alanlar is None:
        return b"\n" if ad == "iskelet" else b"x\n"
    alanlar = dosya.get("alanlar", []) if alanlar is None else alanlar
    if ad == "iskelet":
        alanlar = [a for a in alanlar if a.get("zorunlu")]
    nesne = alan_nesnesi(alanlar, sabit=ad != "iskelet")
    return (json.dumps(nesne, ensure_ascii=False) + ("\n" if bicim in ("jsonl", "metin") else "")).encode("utf-8")


def varyantlar(sozlesme):
    dosyalar = (sozlesme or {}).get("dosyalar", [])
    for d in dosyalar:
        _goreli(d["yol"])
    sonuc = []
    for ad in VARYANT_ADLARI:
        yazilacak = {}
        neden = None
        try:
            if not dosyalar:
                raise ValueError("çıktı sözleşmesi yok veya boş")
            if ad == "kismi":
                if all(d["bicim"] in ("ikili", "dizin") for d in dosyalar):
                    raise ValueError("yalnız ikili/dizin çıktıları var")
                ilk = dosyalar[0]
                if len(dosyalar) > 1:
                    yazilacak = {d["yol"]: None for d in dosyalar[1:]}
                    yazilacak[ilk["yol"]] = _icerik(ilk, "sabit")
                else:
                    zorunlu = [a for a in ilk.get("alanlar", []) if a.get("zorunlu")]
                    if len(zorunlu) <= 1:
                        raise ValueError("kısmi çıktı için yeterli zorunlu yapısal alan yok")
                    secilen = zorunlu[:len(zorunlu) // 2]
                    tam = _icerik(ilk, "sabit", zorunlu)
                    icerik = _icerik(ilk, "sabit", secilen)
                    # Çocuk yolu ebeveyni örtük doldurabilir: kısmi çıktı tam kalmasın.
                    while secilen and icerik == tam:
                        secilen = secilen[:-1]
                        icerik = _icerik(ilk, "sabit", secilen)
                    yazilacak[ilk["yol"]] = icerik
            else:
                yazilacak = {d["yol"]: _icerik(d, ad) for d in dosyalar}
        except ValueError as exc:
            neden = str(exc)
            yazilacak = {}
        sonuc.append({"ad": ad, "yazilacak": yazilacak, "atlandi": neden})
    return sonuc


def uygula(agac, varyant):
    """Her hedefi, hiçbir değişiklik yapmadan önce temiz ağaç sınırında doğrular."""
    kok = Path(agac).resolve()
    hedefler = []
    for yol, icerik in varyant["yazilacak"].items():
        hedef = kok / _goreli(yol)
        if not hedef.resolve().is_relative_to(kok) or hedef.resolve() == kok:
            raise ValueError(f"çıktı temiz ağaç dışında: {yol}")
        hedefler.append((hedef, icerik))
    # Silme önce yapılır: main'de bulunan çıktı dizinleri de gerçekten boşaltılır.
    for hedef, _ in hedefler:
        if hedef.is_symlink() or hedef.is_file():
            hedef.unlink()
        elif hedef.is_dir():
            shutil.rmtree(hedef)
    for hedef, icerik in hedefler:
        if icerik is None:
            continue
        hedef.parent.mkdir(parents=True, exist_ok=True)
        if icerik == DIZIN:
            hedef.mkdir(exist_ok=True)
        else:
            hedef.write_bytes(icerik)


def negatif_kontrol(sonuc):
    from .kehanet import IZINLI_ARACLAR
    if sonuc.get("gecti") is True:
        return {"durum": "atlandi", "neden": None, "kanit": []}
    kontroller = sonuc.get("kontroller") or []
    kalanlar = [k["ad"][:120] for k in kontroller if k.get("gecti") is False]
    metin = "\n".join(str(s or "") for s in (
        sonuc.get("hata"), sonuc.get("stderr_kuyrugu"), sonuc.get("cikti_kuyrugu"),
        *(k.get("ayrinti") for k in kontroller)))
    izler = [iz for iz in (
        "Traceback (most recent call last)", "ModuleNotFoundError", "ImportError", "SyntaxError",
        "IndentationError", "NameError", "kehanet dosya yazamaz", "kehanet ağ veya sistem komutu kullanamaz",
        "kehanet dosya değiştiremez", "kehanet yalnız salt okunur araç çağırabilir",
        "kehanet araç çıktısını dosyaya yazamaz", "command not found", "executable not found") if iz in metin]
    izler.extend(es[0] for es in re.finditer(
        r"No such file or directory: ['\"](?:[^'\"]*/)?(?:" + "|".join(IZINLI_ARACLAR) + r")['\"]", metin))
    if sonuc.get("hata"):
        izler.append(str(sonuc["hata"])[:200])
    if sonuc.get("zaman_asimi"):
        izler.append("zaman_asimi")
    if not kalanlar and not izler:
        izler.append("başarısız davranış kontrolü yok")
    return {"durum": "kehanet_hatasi" if izler else "anlamli",
            "neden": "kehanet_hatasi" if izler else "davranis_eksik",
            "kanit": list(dict.fromkeys(izler + kalanlar))}


def kusur_denetimi(kehanet_yolu, sozlesme, agac_ac, girdiler, *, zaman_asimi=60, referans=None):
    from .kehanet import calistir_kehanet
    pozitif = pozitif_denetimi(kehanet_yolu, sozlesme, agac_ac, girdiler, referans, zaman_asimi)
    kayitlar = []
    for varyant in varyantlar(sozlesme):
        ayrinti = varyant["atlandi"]
        durum = "atlandi"
        if not ayrinti:
            with agac_ac() as agac:
                uygula(agac, varyant)
                sonuc = calistir_kehanet(kehanet_yolu, agac, girdiler, zaman_asimi=zaman_asimi)
            negatif = negatif_kontrol(sonuc)
            durum = ("gecti" if sonuc.get("gecti") else
                     "reddetti_hata" if negatif["durum"] == "kehanet_hatasi" else "reddetti")
            ayrinti = "; ".join(negatif["kanit"]) or "kusurlu çıktı kabul edildi"
        kayitlar.append({"ad": varyant["ad"], "sonuc": durum, "ayrinti": ayrinti[:300]})
    # Referanssız eski makbuz okuyucuları dört varyantı birebir bekliyor.
    if referans is not None:
        kayitlar.append(ornege_ozel_denetimi(kehanet_yolu, sozlesme, agac_ac, girdiler, referans, zaman_asimi))
    etkin = [v for v in kayitlar if v["sonuc"] != "atlandi"]
    uyarilar = []
    if pozitif["durum"] == "reddetti":
        uyarilar.append("kehanet doğru referans çıktıyı reddetti (aşırı katı): " + "; ".join(pozitif["kanit"]))
    if etkin and all(v["sonuc"] == "reddetti_hata" for v in etkin):
        uyarilar.append("tüm kusurlu varyantlar kehanet hatasıyla reddedildi")
    if not etkin:
        uyarilar.append("kusur denetimi atlandı: " + kayitlar[0]["ayrinti"])
    return {"durum": "zayif" if any(v["sonuc"] == "gecti" for v in etkin) else
            "gecti" if etkin else "atlandi", "varyantlar": kayitlar, "uyarilar": uyarilar, "pozitif": pozitif}


@contextmanager
def temiz_agac(depo, *, yontem="worktree", cikar=()):
    if yontem not in ("worktree", "klon"):
        raise ValueError(f"bilinmeyen temiz ağaç yöntemi: {yontem}")
    with tempfile.TemporaryDirectory(prefix="orvant-kehanet-") as gecici:
        temiz = Path(gecici) / "agac"
        komut = (["git", "clone", "--quiet", "--no-hardlinks", "--single-branch", "--branch", "main",
                  str(depo), str(temiz)] if yontem == "klon" else
                 ["git", "-C", str(depo), "worktree", "add", "--detach", str(temiz), "main"])
        proc = subprocess.run(komut, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError(f"temiz kehanet ağacı açılamadı: {proc.stderr.strip()}")
        try:
            uygula(temiz, {"yazilacak": {yol: None for yol in cikar}})
            yield temiz
        finally:
            if yontem == "worktree":
                subprocess.run(["git", "-C", str(depo), "worktree", "remove", "--force", str(temiz)],
                               capture_output=True, text=True, check=True)


def _dosya_sha(yol):
    if not yol.is_file():
        return None
    h = hashlib.sha256()
    with yol.open("rb") as f:
        for parca in iter(lambda: f.read(1024 * 1024), b""):
            h.update(parca)
    return h.hexdigest()


def girdiler_sha256(girdiler):
    # Yalnız yol listesi değil içerik de bağlanır; aynı adla değişen girdi önbelleği bozar.
    kayitlar = []
    for ham in girdiler:
        p = Path(ham)
        yollar = [p, *sorted(p.rglob("*"))] if p.is_dir() else [p]
        kayitlar.append([(str(y), "dizin" if y.is_dir() else _dosya_sha(y)) for y in yollar])
    return hashlib.sha256(json.dumps(kayitlar, ensure_ascii=False).encode("utf-8")).hexdigest()


def _kimlik(yol, sozlesme_yolu, depo, girdiler):
    sha = subprocess.run(["git", "-C", str(depo), "rev-parse", "main"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return {"kehanet_sha256": _dosya_sha(yol), "sozlesme_sha256": _dosya_sha(sozlesme_yolu),
            "main_sha": sha, "girdiler_sha256": girdiler_sha256(girdiler),
            "referans_sha256": _dosya_sha(yol.with_suffix(".referans.json"))}


def atlanan_denetimler(neden):
    return {"pozitif_kontrol": {"durum": "atlandi", "neden": "referans yok", "kanit": []},
            "negatif_kontrol": {"durum": "atlandi", "neden": None, "kanit": []},
            "kusur_denetimi": {"durum": "atlandi", "varyantlar": [], "uyarilar": [neden]}}


def _makbuz_yaz(yol, kayit, kimlik, kaynak):
    veri = {"gorev": kayit["gorev"], "zaman": datetime.now(timezone.utc).isoformat(),
            "kaynak": kaynak, **kimlik,
            **{k: kayit[k] for k in ("zayiflik_denetimi", "negatif_kontrol", "kusur_denetimi", "pozitif_kontrol", "uyarilar")}}
    if kaynak == "denetle":
        veri.update({k: kayit[k] for k in ("sonuc", "neden") if k in kayit})
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=yol.parent, delete=False) as f:
            gecici = Path(f.name)
            json.dump(veri, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(gecici, yol)
    finally:
        if gecici is not None:
            gecici.unlink(missing_ok=True)


def denetim(yazar, gorev, *, kaynak="hazirla", yeniden_hakki=True):
    """Tek ortak yeniden üretim hakkı; salt denetimde üretim ve önbellek yok."""
    from .kehanet import (calistir_kehanet, kehanet_yolu, sozlesme_yolu, okunabilir_girdiler,
                          sozlesme_yol_uyarilari)
    yol = kehanet_yolu(yazar.calisma, gorev["id"])
    syol = sozlesme_yolu(yazar.calisma, gorev["id"])
    myol = yol.with_suffix(".denetim.json")
    plan = json.loads((yazar.calisma / "plan/plan.json").read_text(encoding="utf-8"))
    depo = Path(plan["depo"]["yol"]).resolve()
    girdiler = okunabilir_girdiler(yazar.calisma)
    duzeltilen = []
    for deneme in range(2 if kaynak == "hazirla" and yeniden_hakki else 1):
        kimlik = _kimlik(yol, syol, depo, girdiler)
        kayit = {"gorev": gorev["id"], "kehanet": str(yol), "uyarilar": [],
                 "duzeltilen_uyarilar": list(duzeltilen),
                 **atlanan_denetimler("kusur denetimi atlandı: negatif kontrol tamamlanmadı")}
        if not yol.exists():
            kayit.update(zayiflik_denetimi="gecersiz", sonuc="atlandi", gorev_durumu=gorev["durum"],
                         temiz_agac_sonucu={"kehanet": "yok", "gecti": None})
            _makbuz_yaz(myol, kayit, kimlik, kaynak)
            return kayit
        sozlesme = json.loads(syol.read_text(encoding="utf-8")) if syol.exists() else None
        kayit["uyarilar"] = sozlesme_yol_uyarilari(sozlesme)
        ryol = yol.with_suffix(".referans.json")
        referans = (referans_oku(ryol, sozlesme, kimlik["sozlesme_sha256"])
                    if ryol.exists() or yazar.referans else None)
        cikar = [d["yol"] for d in (sozlesme or {}).get("dosyalar", [])] if (
            kaynak == "denetle" or yazar.referans) else []

        def agac_ac():
            return temiz_agac(depo, yontem="klon" if kaynak == "denetle" else "worktree", cikar=cikar)

        with agac_ac() as agac:
            sonuc = calistir_kehanet(yol, agac, girdiler)
        kayit["temiz_agac_sonucu"] = sonuc
        negatif = negatif_kontrol(sonuc)
        kayit["negatif_kontrol"] = negatif
        geri_bildirim = ""
        if sonuc.get("hata") or sonuc.get("zaman_asimi"):
            kayit["zayiflik_denetimi"] = "gecersiz"
        elif sonuc["gecti"]:
            kayit["zayiflik_denetimi"] = "zayif"
            geri_bildirim = "Kehanet temiz main ağacında geçti; eksik davranışı reddetmeli."
        elif negatif["durum"] == "kehanet_hatasi":
            kayit.update(zayiflik_denetimi="gecersiz", neden="negatif_kontrol")
            geri_bildirim = "Negatif kontrol kehanet hatasıyla kaldı: " + "; ".join(negatif["kanit"])
        else:
            onceki = {}
            if kaynak == "hazirla" and myol.exists():
                try:
                    onceki = json.loads(myol.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass  # Yarım/eski makbuz kanıt değildir; denetim yeniden koşar.
            if (isinstance(onceki, dict) and onceki.get("kaynak") == "hazirla" and
                    onceki.get("zayiflik_denetimi") == "gecti" and
                    all(onceki.get(k) == v for k, v in kimlik.items()) and
                    isinstance(onceki.get("kusur_denetimi"), dict)):
                kusur = {**onceki["kusur_denetimi"], "onbellek": True}
                kayit["onbellek"] = True
            else:
                kusur = kusur_denetimi(yol, sozlesme, agac_ac, girdiler, referans=referans)
            kayit["kusur_denetimi"] = kusur
            kayit["zayiflik_denetimi"] = "zayif" if kusur["durum"] == "zayif" else "gecti"
            if kusur["durum"] == "zayif":
                kayit["neden"] = "kusurlu_cozum"
                gecen = ", ".join(v["ad"] for v in kusur["varyantlar"] if v["sonuc"] == "gecti")
                geri_bildirim = (f"Kusurlu varyantlar geçti: {gecen}. "
                                 "Kehanet boş/sabit/iskelet/kısmi çıktıyı reddetmeli.")
                if "ornege_ozel" in gecen:
                    geri_bildirim += (" Çıktı girdiye bağlı doğrulanmalı; sabit tek öğe/başka girdiyle "
                                      "aynı çıktı reddedilmeli.")
        kusur = kayit["kusur_denetimi"]
        pozitif = kusur.get("pozitif")
        if pozitif is None:
            pozitif = pozitif_denetimi(yol, sozlesme, agac_ac, girdiler, referans)
            kusur["pozitif"] = pozitif
            if pozitif["durum"] == "reddetti":
                kusur["uyarilar"].append("kehanet doğru referans çıktıyı reddetti (aşırı katı): " +
                                         "; ".join(pozitif["kanit"]))
        kayit["pozitif_kontrol"] = pozitif
        kayit["uyarilar"].extend(kusur["uyarilar"])
        kapi = pozitif.get("kapi_hatalari") or []
        if kapi and kayit["zayiflik_denetimi"] != "gecersiz":
            # Kapıda çalıştırılamayan kehanet doğru işi de reddeder (G-131): kullanılamaz.
            kayit.update(zayiflik_denetimi="gecersiz", neden="kapida_calistirilamaz")
            geri_bildirim = ("Kehanet doğru referansta kapı kısıtlarıyla çalıştırılamadı: " +
                             "; ".join(kapi) + ". Yalnız izinli araçları kullan, her iddianın "
                             "kontrolünü gerçekten koş; atlanan kontrolü geçti sayma." +
                             ("\n" + geri_bildirim if geri_bildirim else ""))
        if pozitif["durum"] == "reddetti" and yazar.referans:
            geri_bildirim = (
                "Kehanet sözleşmeye uyan doğru referans çıktıyı reddetti: " +
                "; ".join(pozitif["kanit"]) + ". Aşırı katı kontrolleri sözleşme ve kabul ölçütüne "
                "göre gevşet; kusurlu çıktıları reddetmeye devam et. Çıktı sözleşmesini (dosya "
                "yolları, alan adları, tipleri) değiştirme; betiği sözleşmedeki tiplerle tutarlı yap." +
                ("\n" + geri_bildirim if geri_bildirim else ""))
        if geri_bildirim and kaynak == "hazirla" and yeniden_hakki and deneme == 0:
            duzeltilen.extend(u for u in kusur["uyarilar"] if "aşırı katı" in u)
            yazar.uret(gorev, geri_bildirim="\n\n" + geri_bildirim,
                       sozlesmeyi_koru=syol.exists())
            if yazar.referans and _dosya_sha(syol) != kimlik["sozlesme_sha256"]:
                # T12 gerçek denemesi: referans iki çağrıyı düzeltmeye harcayınca yeni sözleşmede
                # pozitif kontrol hiç koşmadı. Sözleşme değiştiyse tek ek referans çağrısı ayrılır.
                if yazar._referans_siniri is not None:
                    yazar._referans_siniri = max(yazar._referans_siniri, yazar._referans_cagrilari + 1)
                yazar.referans_uret(gorev)
            continue
        if kaynak == "denetle":
            kayit["gorev_durumu"] = gorev["durum"]
            kayit["sonuc"] = {"gecti": "saglam", "zayif": "zayif", "gecersiz": "bozuk"}[kayit["zayiflik_denetimi"]]
            if negatif["durum"] == "anlamli" and kayit["kusur_denetimi"]["durum"] == "atlandi":
                kayit.update(sonuc="denetlenemedi", neden=(
                    "sozlesme_yok" if not syol.exists() else "kusur_denetimi_atlandi"))
            kayit["temiz_agac_sonucu"] = {k: sonuc[k] for k in
                ("gecti", "exit_code", "hata", "zaman_asimi") if k in sonuc}
        _makbuz_yaz(myol, kayit, kimlik, kaynak)
        return kayit
