"""Bağımlılıksız olay kurucu, doğrulayıcı ve JSONL aracı."""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from orvant_gelisim import projeler
except ModuleNotFoundError:  # `python3 orvant_gelisim/kayit.py` betik olarak çağrıldığında
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from orvant_gelisim import projeler

from orvant_op import ayarlar, uyum
ROOT = Path(__file__).resolve().parents[1]
CEKIRDEK = ayarlar.cekirdek_yolu()
OPERATOR = ("hedef_netlestirme", "karar_belgesi", "is_bolme", "brief_yazma",
            "yurutucu_secimi", "calisma_alani", "baslatma_izleme", "dogrulama",
            "ariza_teshisi", "yeniden_is_kapsami", "yontem_gelistirme", "birlestirme",
            "yetki_karari", "olcum", "raporlama", "arastirma")
ISLER = {**dict.fromkeys(OPERATOR, "operator"), "isci_kosusu": "isci",
          "kapi_karari": "kapi", "orvant_kayit": "kayit"}
AKTORLER = {"orvant", "opus", "kullanici", "kullanici_benzetim", "codex", "claude", "dogrulayici"}
SONUCLAR = {"ok", "ret", "hata", "zaman_asimi", "iptal", "bilinmiyor"}
MALIYET = ("girdi_token", "onbellek_token", "cikti_token", "saniye", "insan_dakika")
ALANLAR = {"id", "t", "proje", "kosu", "aktor", "katman", "is_turu", "sonuc",
           "maliyet", "orvant_surumu", "mudahale", "benzetim", "ozet", "kanit", "ham",
           "yurutme_id", "miras"}
EK_ALANLAR = {"olay_kokeni"}


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("t ISO-8601 UTC metni olmalı")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("t geçerli ISO-8601 olmalı") from exc
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("t UTC olmalı")


def denetle(o):
    if not isinstance(o, dict) or not ALANLAR <= set(o) or set(o) - ALANLAR - EK_ALANLAR:
        raise ValueError(f"olay alanları: eksik={sorted(ALANLAR-set(o)) if isinstance(o, dict) else sorted(ALANLAR)}, fazla={sorted(set(o)-ALANLAR-EK_ALANLAR) if isinstance(o, dict) else []}")
    if "olay_kokeni" in o and (not isinstance(o["olay_kokeni"], dict)
            or set(o["olay_kokeni"]) != {"orvant_surumu"}
            or not isinstance(o["olay_kokeni"]["orvant_surumu"], str)
            or not o["olay_kokeni"]["orvant_surumu"]):
        raise ValueError("olay_kokeni geçersiz")
    if not isinstance(o["id"], str) or not re.fullmatch(r"[0-9a-f]{16}(?:[0-9a-f]{16})?", o["id"]):
        raise ValueError("id hex 16 veya 32 hane olmalı")
    _utc(o["t"])
    for field in ("proje", "orvant_surumu"):
        if not isinstance(o[field], str) or not o[field]:
            raise ValueError(f"{field} boş olmayan metin olmalı")
    if o["kosu"] is not None and not isinstance(o["kosu"], str):
        raise ValueError("kosu metin veya null olmalı")
    if o["yurutme_id"] is not None and (not isinstance(o["yurutme_id"], str) or
                                        not re.fullmatch(r"[0-9a-f]{16}", o["yurutme_id"])):
        raise ValueError("yurutme_id null veya 16 haneli hex olmalı")
    if type(o["miras"]) is not bool or (o["miras"] and o["yurutme_id"] is None):
        raise ValueError("miras bool olmalı ve yürütme kimliği gerektirir")
    a = o["aktor"]
    if not isinstance(a, dict) or set(a) != {"tur", "kimlik"} or a["tur"] not in AKTORLER or not isinstance(a["kimlik"], str) or not a["kimlik"]:
        raise ValueError("aktor geçersiz")
    if o["is_turu"] not in ISLER or o["katman"] != ISLER[o["is_turu"]]:
        raise ValueError("is_turu veya katman geçersiz")
    if o["sonuc"] not in SONUCLAR:
        raise ValueError("sonuc geçersiz")
    m = o["maliyet"]
    if not isinstance(m, dict) or set(m) != set(MALIYET):
        raise ValueError("maliyet alanları geçersiz")
    for key in MALIYET:
        value = m[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)) or value < 0 or (key.endswith("token") and not isinstance(value, int))):
            raise ValueError(f"maliyet.{key} geçersiz")
    expected = o["katman"] == "operator" and a["tur"] != "orvant"
    if type(o["mudahale"]) is not bool or o["mudahale"] != expected:
        raise ValueError("mudahale hesapla uyuşmuyor")
    if type(o["benzetim"]) is not bool or o["benzetim"] != (a["tur"] == "kullanici_benzetim"):
        raise ValueError("benzetim aktörle uyuşmuyor")
    if not isinstance(o["ozet"], str) or not isinstance(o["kanit"], list) or not all(isinstance(x, str) for x in o["kanit"]) or not isinstance(o["ham"], dict):
        raise ValueError("ozet/kanit/ham geçersiz")
    for alan in ("onerdi", "karar_verdi", "uyguladi"):
        if alan in o["ham"]:
            deger = o["ham"][alan]
            if not isinstance(deger, str) or deger not in AKTORLER:
                raise ValueError(f"ham.{alan} aktör türü olmalı")
    if "mudahale_bolumu" in o["ham"]:
        bolum = o["ham"]["mudahale_bolumu"]
        if not isinstance(bolum, str) or not bolum.strip():
            raise ValueError("ham.mudahale_bolumu boş olmayan metin olmalı")
    return o


def olay(*, proje, is_turu, aktor_tur=None, aktor_kimlik=None, aktor=None, katman=None,
         sonuc="bilinmiyor", kosu=None,
         ozet="", kanit=None, maliyet=None, orvant_surumu="bilinmiyor", ham=None,
         id=None, t=None, yurutme_id=None, miras=False,
         onerdi=None, karar_verdi=None, uyguladi=None, mudahale_bolumu=None):
    if is_turu not in ISLER:
        raise ValueError("bilinmeyen is_turu")
    if aktor is not None:
        if not isinstance(aktor, dict) or aktor_tur is not None or aktor_kimlik is not None:
            raise ValueError("aktor tek biçimde verilmeli")
        aktor_tur, aktor_kimlik = aktor.get("tur"), aktor.get("kimlik")
    if aktor_tur not in AKTORLER:
        raise ValueError("bilinmeyen aktör")
    if katman is not None and katman != ISLER[is_turu]:
        raise ValueError("katman is_turu ile uyuşmuyor")
    m = {key: None for key in MALIYET}
    m.update(maliyet or {})
    ham = dict(ham or {})
    proje_ham = proje
    proje = projeler.kanonik(proje)
    if proje != proje_ham:
        ham["proje_ham"] = proje_ham
    for alan, deger in (("onerdi", onerdi), ("karar_verdi", karar_verdi),
                        ("uyguladi", uyguladi), ("mudahale_bolumu", mudahale_bolumu)):
        if deger is not None:
            ham[alan] = deger
    o = {"id": id or uuid.uuid4().hex, "t": t or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
         "proje": proje, "kosu": kosu, "aktor": {"tur": aktor_tur, "kimlik": aktor_kimlik},
         "katman": ISLER[is_turu], "is_turu": is_turu, "sonuc": sonuc,
         "maliyet": m, "orvant_surumu": orvant_surumu,
         "mudahale": ISLER[is_turu] == "operator" and aktor_tur != "orvant",
         "benzetim": aktor_tur == "kullanici_benzetim", "ozet": ozet,
         "kanit": list(kanit or []), "ham": dict(ham or {}),
         "yurutme_id": yurutme_id, "miras": miras}
    return denetle(o)


def yaz(yol, item):
    denetle(item)
    # Serileştirme açılıştan önce: reddedilen olay boş dosya bırakmasın (G-149).
    satir = json.dumps(item, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    # newline="\n": Windows'ta metin kipi `\n`'i `\r\n` yapıp izi (ve hash'i) bozmasın.
    with Path(yol).open("a", encoding="utf-8", newline="\n") as fh:
        uyum.kilitle(fh)
        try:
            fh.write(satir)
            fh.flush()
        finally:
            uyum.kilit_birak(fh)


def olay_kokeni_ekle(item, surum=None):
    """Yazılan kopyaya, yalnız açıkça bilinen koşu sürümünü ekle."""
    yeni = copy.deepcopy(item)
    if "olay_kokeni" not in yeni and surum is not None:
        yeni["olay_kokeni"] = {"orvant_surumu": surum}
    return yeni


def oku(yol):
    with Path(yol).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def dogrula(yol):
    hatalar = []
    with Path(yol).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            try:
                denetle(json.loads(line))
            except (ValueError, TypeError, KeyError) as exc:
                hatalar.append(f"{yol}:{n}: {exc}")
    return hatalar


def surum_coz(kok=ROOT, cekirdek=CEKIRDEK):
    gecersiz_kilma = os.environ.get("ORVANT_SURUMU")
    if gecersiz_kilma:
        return gecersiz_kilma, None

    def sha(path):
        try:
            sonuc = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=5)
        except FileNotFoundError:
            return None, "git bulunamadı"
        except subprocess.TimeoutExpired:
            return None, "git zaman aşımı"
        except OSError as exc:
            return None, f"git çalıştırılamadı: {str(exc)[:120]}"
        if sonuc.returncode != 0:
            return None, f"git rev-parse başarısız: {' '.join(sonuc.stderr.split())[:120]}"
        return sonuc.stdout.strip(), None

    duzenek, neden = sha(kok)
    if not duzenek:
        return "bilinmiyor", neden
    cekirdek_sha, _ = sha(cekirdek) if cekirdek is not None else (None, None)
    kirli = False
    try:
        durum = subprocess.run(
            ["git", "-C", str(kok), "status", "--porcelain", "--untracked-files=all", "--",
             "orvant_op/", ":(glob)orvant_gelisim/*.py", "orvant_gelisim/sema/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        kirli = durum.returncode == 0 and bool(durum.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass  # Kirlilik bilgisi alınamasa da çözülen sürüm ve gerekçesi korunur.
    return (f"cekirdek@{cekirdek_sha}+duzenek@{duzenek}" if cekirdek_sha
            else f"duzenek@{duzenek}") + ("+kirli" if kirli else ""), None


def _olay_zamani(item):
    return datetime.fromisoformat(item["t"].replace("Z", "+00:00"))


def mudahale_dosyasi(proje, dosya=None, kok=ROOT, tablo=projeler.TABLO):
    """Açık yol, ortam, proje tablosu, ardından olay zamanı ile iz seç."""
    acik = dosya or os.environ.get("ORVANT_IZ")
    if acik:
        return Path(acik)
    tablodaki = projeler.iz_yolu(proje, tablo)
    if tablodaki:
        yol = Path(tablodaki)
        return yol if yol.is_absolute() else Path(kok) / yol
    iz_koku = Path(kok) / "orvant_gelisim/iz"
    adlar = projeler.adlar(proje, tablo)
    # Adları glob deseni yapma: proje adındaki özel karakterler genişlemesin.
    yollar = [p for p in iz_koku.glob("*.jsonl") if p.is_file()
              and any(p.name.startswith(ad + "-") for ad in adlar)]
    if not yollar:
        raise ValueError("Proje izi bulunamadı; --dosya veya ORVANT_IZ verin")
    en_eski = datetime.min.replace(tzinfo=timezone.utc)
    return max(yollar, key=lambda p: (max((_olay_zamani(e) for e in oku(p)),
                                         default=en_eski), p.name))


def son_bolum(yol, proje, gorev=None):
    """Aynı proje ve varsa görevdeki zaman bakımından son açık bölümü bul."""
    adaylar = [e for e in (oku(yol) if Path(yol).exists() else [])
               if projeler.kanonik(e["proje"]) == projeler.kanonik(proje)
               and (gorev is None or e["ham"].get("gorev") == gorev)
               and isinstance(e["ham"].get("mudahale_bolumu"), str)
               and e["ham"]["mudahale_bolumu"].strip()]
    if not adaylar:
        raise ValueError("Aynı proje/görev için sürdürülecek müdahale bölümü bulunamadı")
    # Eşit zamanlı olaylarda dosyadaki son satırı seç.
    return max(enumerate(adaylar), key=lambda x: (_olay_zamani(x[1]), x[0]))[1]["ham"]["mudahale_bolumu"]


def mudahale_yaz(args):
    yol = mudahale_dosyasi(args.proje, args.dosya)
    print(f"İz dosyası: {yol}", file=sys.stderr)
    bolum = son_bolum(yol, args.proje, args.gorev) if args.son_bolum else args.bolum
    if bolum is None:
        zaman = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
        bolum = f"{args.gorev or 'genel'}-{zaman}-{uuid.uuid4().hex[:4]}"
    surum, neden = surum_coz() if args.surum is None else (args.surum, None)
    ham = {}
    if args.gorev is not None:
        ham["gorev"] = args.gorev
    if surum == "bilinmiyor" and neden is not None:
        ham["surum_nedeni"] = neden
    for alan in ("onerdi", "karar_verdi", "uyguladi"):
        if hasattr(args, alan):
            ham[alan] = getattr(args, alan) or args.aktor_tur
    item = olay(proje=args.proje, is_turu=args.is_turu, aktor_tur=args.aktor_tur,
                aktor_kimlik=args.aktor_kimlik, sonuc=args.sonuc, ozet=args.ozet,
                kosu=args.kosu, kanit=args.kanit, maliyet={"insan_dakika": args.insan_dakika},
                orvant_surumu=surum, ham=ham, mudahale_bolumu=bolum)
    item = olay_kokeni_ekle(item, args.surum)
    yaz(yol, item)
    print(f"{item['id']} bolum={bolum}")


def orvant_surumu():
    return surum_coz()[0]


def kor_gorunum(olaylar):
    result = []
    for item in olaylar:
        clean = copy.deepcopy(item)
        clean.pop("ozet", None)
        # Ham aktarım satırı aynı özeti/duzenleme yolunu tekrar taşıyabilir.
        clean.pop("ham", None)
        clean["kanit"] = [p for p in clean.get("kanit", []) if not any(x in p for x in ("duzenleme/", "DENEME-SONUC", "DEVRET-"))]
        result.append(clean)
    return result


def ozet_yaz(dosyalar):
    from collections import Counter
    events = [e for path in dosyalar for e in oku(path)]
    unique = [e for e in events if not e["miras"]]
    counts = Counter((e["is_turu"], e["aktor"]["tur"]) for e in unique)
    print("is_turu | aktor_turu | sayi")
    for (job, actor), count in sorted(counts.items()):
        print(f"{job} | {actor} | {count}")
    print(f"miras | {sum(e['miras'] for e in events)}")
    print(f"toplam | {len(events)}")
    print(f"tekil_toplam | {len(unique)}")
    print(f"mudahale | {sum(e['mudahale'] for e in unique)}")
    print(f"benzetim | {sum(e['benzetim'] for e in unique)}")
    print(f"gercek_mudahale | {sum(e['mudahale'] and not e['benzetim'] for e in unique)}")
    workers = [e for e in unique if e["is_turu"] == "isci_kosusu"]
    totals = [sum(e["maliyet"][key] or 0 for e in workers)
              for key in ("girdi_token", "onbellek_token", "cikti_token")]
    print(f"maliyet | tekil_isci_kosusu={len(workers)} | girdi_token={totals[0]} | "
          f"onbellek_token={totals[1]} | cikti_token={totals[2]}")


def _konsol_utf8():
    # Windows'ta borulu stdout/stderr yerel kodlamayı (cp1254) kullanır; kodlanamayan karakter çökertmesin.
    if uyum.WINDOWS:
        for akim in (sys.stdout, sys.stderr):
            try:
                akim.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def main():
    _konsol_utf8()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="komut", required=True)
    add = sub.add_parser("ekle")
    for name in ("dosya", "proje", "aktor-tur", "aktor-kimlik", "is-turu", "sonuc", "ozet"):
        add.add_argument("--" + name, required=True)
    add.add_argument("--kosu")
    add.add_argument("--kanit", action="append", default=[])
    add.add_argument("--insan-dakika", type=float)
    add.add_argument("--surum", default=None)
    for alan in ("onerdi", "karar-verdi", "uyguladi"):
        add.add_argument("--" + alan, choices=sorted(AKTORLER))
    add.add_argument("--bolum")
    add.add_argument("--gorev")
    mudahale = sub.add_parser("mudahale", help="Operatör müdahalesini kaydet")
    for alan in ("proje", "ozet"):
        mudahale.add_argument("--" + alan, required=True)
    mudahale.add_argument("--is-turu", choices=OPERATOR, required=True)
    mudahale.add_argument("--aktor-tur", choices=sorted(AKTORLER), default="opus")
    mudahale.add_argument("--aktor-kimlik", default="claude-opus-5.5")
    mudahale.add_argument("--sonuc", choices=sorted(SONUCLAR), default="ok")
    for alan in ("dosya", "gorev", "kosu", "surum"):
        mudahale.add_argument("--" + alan)
    mudahale.add_argument("--kanit", action="append", default=[])
    mudahale.add_argument("--insan-dakika", type=float)
    for alan in ("onerdi", "karar-verdi", "uyguladi"):
        mudahale.add_argument("--" + alan, choices=sorted(AKTORLER), nargs="?",
                             const=None, default=argparse.SUPPRESS)
    bolumler = mudahale.add_mutually_exclusive_group()
    bolumler.add_argument("--bolum")
    bolumler.add_argument("--son-bolum", action="store_true")
    check = sub.add_parser("dogrula")
    check.add_argument("dosyalar", nargs="+")
    summary = sub.add_parser("ozet")
    summary.add_argument("dosyalar", nargs="+")
    args = parser.parse_args()
    if args.komut == "ekle":
        surum, neden = surum_coz() if args.surum is None else (args.surum, None)
        ham = {}
        if surum == "bilinmiyor" and neden is not None:
            ham["surum_nedeni"] = neden
        if args.gorev is not None:
            ham["gorev"] = args.gorev
        item = olay(proje=args.proje, aktor_tur=args.aktor_tur, aktor_kimlik=args.aktor_kimlik,
                    is_turu=args.is_turu, sonuc=args.sonuc, ozet=args.ozet, kosu=args.kosu,
                    kanit=args.kanit, maliyet={"insan_dakika": args.insan_dakika},
                    orvant_surumu=surum, ham=ham, onerdi=args.onerdi,
                    karar_verdi=args.karar_verdi, uyguladi=args.uyguladi,
                    mudahale_bolumu=args.bolum)
        item = olay_kokeni_ekle(item, args.surum)
        yaz(args.dosya, item)
        print(item["id"])
    elif args.komut == "mudahale":
        try:
            mudahale_yaz(args)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"Müdahale kaydedilemedi: {exc}")
    elif args.komut == "dogrula":
        errors = [e for path in args.dosyalar for e in dogrula(path)]
        for e in errors:
            print(e)
        if errors:
            raise SystemExit(1)
        print(f"{len(args.dosyalar)} dosya geçerli")
    else:
        ozet_yaz(args.dosyalar)


if __name__ == "__main__":
    main()
