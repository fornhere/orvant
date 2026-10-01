"""Plan görevlerini yalıtılmış ağaçta çalıştıran bağımsız kabul kapısı."""
import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from math import ceil
from pathlib import Path

from orvant_op.iz import kaydet
from orvant_op.bagimli_ciktilar import bagimli_ciktilar, bekleme_ciktilari
from orvant_op.karsilama.roller import veri_dogrula
from orvant_op.mimar.dogrulama import durumlari_hesapla
from orvant_op.mimar.durum import izin_yolu_dogrula
from orvant_op.mimar.kehanet import Kehanet, calistir_kehanet, kehanet_yolu, okunabilir_girdiler, olcutler
from orvant_op.mimar.kehanet import kehanet_gecersiz_mi, sozlesme_yolu
from orvant_op.yurutucu import hedef_calistir


from orvant_op.butce import (VARSAYILAN_TABAN, butce_tabani, gorev_envanteri,
                             hesap_zaman_asimi)  # noqa: F401 (geri uyumlu dışa aktarım)


def _json_yaz(yol, veri):
    yol.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=yol.parent, delete=False) as fh:
        json.dump(veri, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        gecici = fh.name
    os.replace(gecici, yol)


def _git(kok, *args, check=True):
    proc = subprocess.run(["git", "-C", str(kok), *map(str, args)],
                          capture_output=True, text=True)
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(map(str, args))}: {proc.stderr.strip()}")
    return proc


def _ekle(yol, veri):
    yol.parent.mkdir(parents=True, exist_ok=True)
    satir = (json.dumps(veri, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(yol, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        kalan = memoryview(satir)
        while kalan:
            yazilan = os.write(fd, kalan)
            if yazilan == 0:
                raise OSError("olay satırı yazılamadı")
            kalan = kalan[yazilan:]
    finally:
        os.close(fd)


def goal_oku(thread_id, db_yolu=None):
    """Goal veritabanını yalnız URI mode=ro ile açar; hedef yoksa açıkça bildirir."""
    if not thread_id:
        return {"status": "hedef_yok", "tokens_used": None}
    yol = Path(db_yolu or os.environ.get("ORVANT_GOALS_DB", "~/.codex/goals_1.sqlite")).expanduser()
    try:
        with closing(sqlite3.connect(yol.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT status, tokens_used FROM thread_goals WHERE thread_id=? "
                             "ORDER BY updated_at_ms DESC LIMIT 1", (thread_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"goals DB okunamadı: {exc}") from exc
    return {"status": row[0], "tokens_used": row[1]} if row else {"status": "hedef_yok", "tokens_used": None}


def _yol_eslesir(yol, kalip):
    if not kalip or kalip.startswith("/") or ".." in Path(kalip).parts:
        return False
    # plan.json sözleşmesi: "/" ile biten kalıp dizinin kendisi ve altındaki her şeydir.
    if kalip.endswith("/"):
        kalip += "**"
    # Her yıldız yalnız bir yol bileşenini, ** ise sıfır veya çok bileşeni kapsar.
    yollar, kaliplar = yol.split("/"), kalip.split("/")

    @lru_cache(None)
    def esles(y, k):
        if k == len(kaliplar):
            return y == len(yollar)
        if kaliplar[k] == "**":
            return esles(y, k + 1) or (y < len(yollar) and esles(y + 1, k))
        return (y < len(yollar) and fnmatch.fnmatchcase(yollar[y], kaliplar[k])
                and esles(y + 1, k + 1))

    return esles(0, 0)


def _degisenler(agac):
    yollar = set()
    for args in (("diff", "--no-renames", "--name-only", "-z", "HEAD"),
                 ("diff", "--cached", "--no-renames", "--name-only", "-z"),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        proc = subprocess.run(["git", "-C", str(agac), *args], capture_output=True)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode(errors="replace"))
        yollar.update(os.fsdecode(p) for p in proc.stdout.split(b"\0") if p)
    return sorted(yollar)


def yol_butunlugu(agac, dosyalar, sozlesme=None):
    """Tanımlı yol alanlarında kaçış RET; tanımsız alanlarda eski uyarı sözleşmesi.

    Sözleşmenin string tipi yol anlamını tek başına taşımaz. Yol/path/file adlı
    alanlar ve açıklamasında açıkça yol belirtilen alanlar eşleştirilir.
    """
    tanimli = {}
    for dosya in (sozlesme or {}).get("dosyalar", []):
        alanlar = set()
        for alan in dosya.get("alanlar", []):
            ad = alan.get("ad", "")
            son = ad.split(".")[-1].replace("[]", "")
            if (re.search(r"(?:^|_)(?:yol|yolu|yollar|yollari|path|paths|file|files|filename|dosya|dosyasi|dosyalar|dosyalari)$", son)
                    or re.search(r"\byol(?:u|ları|lari)?\b|\bpaths?\b|\bfile paths?\b",
                                 alan.get("aciklama", "").casefold())):
                alanlar.add(ad.removeprefix("$."))
                if alan.get("tip") == "array":
                    alanlar.add(ad.removeprefix("$.") + "[]")
        if alanlar:
            tanimli[dosya["yol"]] = alanlar
    bulgular, retler = [], []
    kok = Path(agac).resolve()
    for ad in sorted(set(dosyalar) | set(tanimli)):
        if len(bulgular) >= 50 and ad not in tanimli:
            continue
        yol = kok / ad
        if yol.suffix not in (".json", ".jsonl"):
            continue
        try:
            if not yol.is_file():
                continue
            if not yol.resolve().is_relative_to(kok):
                raise ValueError("kayıt dosyası depo dışında")
            if yol.stat().st_size > 1024 * 1024:
                raise ValueError("kayıt dosyası tarama sınırını aşıyor (1 MiB)")
            # Okuma da sınırlı: stat sonrasında büyüyen dosya sınırsız bellek tüketmesin.
            with yol.open("rb") as fh:
                ham = fh.read(1024 * 1024 + 1)
            if len(ham) > 1024 * 1024:
                raise ValueError("kayıt dosyası tarama sınırını aşıyor (1 MiB)")
            metin = ham.decode("utf-8")
            veri = ([json.loads(s) for s in metin.splitlines() if s.strip()]
                    if yol.suffix == ".jsonl" else json.loads(metin))
        except (OSError, ValueError, RuntimeError) as exc:
            if ad in tanimli:
                retler.append({"dosya": ad, "anahtar": "$", "deger": None,
                               "neden": f"tanımlı yol alanları denetlenemedi: {exc}",
                               "sozlesmede_tanimli": True})
            continue
        bekleyen = [("$", veri)]
        while bekleyen and (len(bulgular) < 50 or ad in tanimli):
            anahtar, deger = bekleyen.pop()
            if isinstance(deger, dict):
                bekleyen.extend((f"{anahtar}.{k}", v) for k, v in deger.items())
            elif isinstance(deger, list):
                bekleyen.extend((f"{anahtar}[{i}]", v) for i, v in enumerate(deger))
            elif isinstance(deger, str) and "://" not in deger and not os.path.isabs(deger):
                normal = os.path.normpath(deger)
                alan = re.sub(r"\[\d+\]", "[]", anahtar.removeprefix("$."))
                if yol.suffix == ".jsonl":
                    alan = re.sub(r"^\$\[\]\.?", "", alan)
                # Bir [] alanı dizinin tek tek yol değerlerini de tanımlar.
                baglayici = alan in tanimli.get(ad, ())
                kacis = normal == ".." or normal.startswith("../")
                agac_yolu = normal.startswith(".orvant/agac/")
                neden = "göreli yol kaçışı" if kacis else None
                if baglayici and not neden:
                    try:
                        if (agac_yolu or
                                not (kok / deger).resolve().is_relative_to(kok) or
                                (not (kok / deger).exists() and (yol.parent / deger).exists())):
                            neden = "depo köküne göreli olmayan yol"
                    except (OSError, ValueError, RuntimeError):
                        neden = "çözümlenemeyen yol"
                if baglayici and neden:
                    retler.append({"dosya": ad, "anahtar": anahtar, "deger": deger,
                                   "neden": neden, "sozlesmede_tanimli": True})
                elif (kacis or agac_yolu) and len(bulgular) < 50:
                    bulgular.append({"dosya": ad, "anahtar": anahtar, "deger": deger})
    return {"durum": "ret" if retler else "uyari" if bulgular else "temiz",
            "bulgular": (retler + bulgular)[:50]}


def okuma_denetimi(agac, girdiler, akis):
    """Goal command_execution kayıtlarındaki açık okuma yollarını deterministik tarar.

    Komut metni işletim sistemi okuma izi değildir. Dinamik/bilinmeyen komutlar
    uyarıda kalır; sonuç bütün dosya erişimlerinin kanıtı olarak sunulmaz.
    """
    kok = Path(agac).resolve()
    izinler = [Path(g).resolve() for g in girdiler]
    okunan, dis, belirsiz = set(), [], []
    sayi = 0
    bekleyen = {}

    def yol_ekle(yol, cwd, komut):
        if yol == "-" or "://" in yol:
            return
        if any(c in yol for c in "$`*?[]~"):
            belirsiz.append(komut)
            return
        hedef = (cwd / yol).resolve()
        izin = next((g for g in izinler if hedef == g or
                     (g.is_dir() and hedef.is_relative_to(g))), None)
        if izin is not None:
            okunan.add(str(hedef))
        elif not hedef.is_relative_to(kok):
            dis.append({"yol": str(hedef), "komut": komut})

    def tara(komut, cwd, derinlik=0):
        if not isinstance(komut, str) or derinlik > 4:
            belirsiz.append(str(komut))
            return
        if any(c in komut for c in "$`\n"):
            belirsiz.append(komut)
        try:
            # shlex varsayılanı yolları böler; shell sözcüklerini koru.
            lex = shlex.shlex(komut, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            parcalar = list(lex)
        except ValueError:
            belirsiz.append(komut)
            return
        if not parcalar:
            return
        if Path(parcalar[0]).name in ("bash", "sh", "zsh") and len(parcalar) == 3 and parcalar[1] in ("-c", "-lc"):
            tara(parcalar[2], cwd, derinlik + 1)
            return
        bolumler, bolum = [], []
        for p in parcalar + [";"]:
            if p in (";", "&&", "||", "|", "&"):
                bolumler.append(bolum)
                bolum = []
                if p in ("||", "|", "&"):
                    belirsiz.append(komut)
            else:
                bolum.append(p)
        for bolum in bolumler:
            if not bolum:
                continue
            if bolum[0] == "cd" and len(bolum) == 2 and not any(c in bolum[1] for c in "$`~"):
                cwd = (cwd / bolum[1]).resolve()
                continue
            arac = Path(bolum[0]).name
            # Seçeneksiz veya yalnız bayraklı okuyucular: seçenek argümanları
            # sayı olabilir (head -n 5); diğer araçlar eksik kapsama olarak kalır.
            if arac not in ("cat", "head", "tail", "sha256sum", "sha1sum", "md5sum", "wc"):
                belirsiz.append(komut)
            else:
                if any(p.startswith("-") for p in bolum[1:]):
                    belirsiz.append(komut)  # Seçeneklerin dolaylı okumaları kapsam dışında.
                for p in bolum[1:]:
                    if p in (">", ">>", "2>", "<", "(", ")"):
                        belirsiz.append(komut)
                        break
                    if not p.startswith("-") and not p.isdigit():
                        yol_ekle(p, cwd, komut)
            for i, p in enumerate(bolum[:-1]):
                if p == "<":
                    yol_ekle(bolum[i + 1], cwd, komut)

    for satir in akis.splitlines():
        try:
            olay = json.loads(satir)
        except ValueError:
            belirsiz.append("Akışta çözümlenemeyen satır")
            continue
        if not isinstance(olay, dict):
            belirsiz.append("Akış olayı nesne değil")
            continue
        item = olay.get("item") or {}
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        kimlik = str(item.get("id", item.get("command", "")))
        if olay.get("type") != "item.completed":
            bekleyen[kimlik] = str(item.get("command"))
            continue
        bekleyen.pop(kimlik, None)
        sayi += 1
        if item.get("exit_code") != 0:
            belirsiz.append(str(item.get("command")))
            continue
        cwd = item.get("cwd") or item.get("working_directory") or str(kok)
        try:
            tara(item.get("command"), (kok / cwd).resolve())
        except (OSError, ValueError, RuntimeError, TypeError):
            belirsiz.append(str(item.get("command")))
    belirsiz.extend(bekleyen.values())
    return {"durum": "uyari" if dis or belirsiz or not sayi else "temiz",
            "kapsam": "Yalnız command_execution içindeki açık okuma komutları; sistem çağrısı denetimi değildir.",
            "komut_sayisi": sayi, "okunan_girdiler": sorted(okunan),
            "dis_okumalar": dis, "belirsiz_komutlar": list(dict.fromkeys(belirsiz))}


def _cikti_sozlesmesi(calisma, gorev_id):
    yol = sozlesme_yolu(calisma, gorev_id)
    return ({"yol": str(yol), "sha256": hashlib.sha256(yol.read_bytes()).hexdigest()}
            if yol.exists() else None)


def _kelimeler(metin):
    return {x.casefold() for x in re.findall(r"[^\W_]{3,}", metin, re.UNICODE)}


_ENVANTER_GENEL_KELIMELER = frozenset({
    "yoksa", "veya", "için", "olan", "olarak", "gerçek", "model", "modeli",
    "yerel", "kaynak", "üretmek", "bildirmek", "eksikliği", "içerik", "dosya", "erişimi",
})


def _envanter_puani(anahtar, kayit):
    kelimeler = _kelimeler(" ".join(str(kayit.get(k) or "") for k in
                                    ("ad", "aciklama", "komut_ornegi")))
    return sum(any(a == b or (min(len(a), len(b)) >= 5 and
                             (a.startswith(b) or b.startswith(a)))
                   for b in kelimeler) for a in anahtar - _ENVANTER_GENEL_KELIMELER)


def _baglam(gorev, plan, calisma):
    if calisma is None:
        return ""
    calisma = Path(calisma)
    bolumler = []
    karar_yolu = calisma / "plan" / "kararlar.json"
    sozlesme_yolu = calisma / "karsilama" / "sozlesme.json"
    kararlar = (json.loads(sozlesme_yolu.read_text(encoding="utf-8")).get("kararlar", [])
                if sozlesme_yolu.exists() else [])
    if karar_yolu.exists():
        son_kararlar = json.loads(karar_yolu.read_text(encoding="utf-8"))
        by_id = {k["id"]: k for k in kararlar}
        by_id.update({k["id"]: k for k in son_kararlar})
        kararlar = list(by_id.values())
    anahtar = _kelimeler(" ".join([gorev["baslik"], gorev["amac"], *gorev["yazilabilir"]]))
    cozulmus = [{"id": k["id"], "baslik": k.get("baslik", ""), "deger": k.get("deger")}
                for k in kararlar if k.get("durum") == "cozuldu" and k.get("deger")]
    cozulmus.sort(key=lambda k: len(anahtar & _kelimeler(k["baslik"] + " " + str(k["deger"]))), reverse=True)
    if cozulmus:
        bolumler.append(("Çözülmüş kullanıcı kararları:", [
            json.dumps(k, ensure_ascii=False) for k in cozulmus]))
    bagli = {g["id"]: g for g in plan["gorevler"] if g["id"] in gorev["bagimliliklar"] and g["durum"] == "kabul"}
    makbuz_dizini = calisma / "yurutme" / "makbuzlar"
    onceki = []
    for id, g in bagli.items():
        makbuzlar = sorted(makbuz_dizini.glob(f"{id}-*.json")) if makbuz_dizini.exists() else []
        kabul = None
        for p in reversed(makbuzlar):
            veri = json.loads(p.read_text(encoding="utf-8"))
            if veri.get("karar") == "kabul":
                kabul = veri
                break
        ozet = (kabul.get("isci_ozeti") or
                f"Kabul kapısı geçti; dosyalar: {', '.join(kabul.get('degisen_dosyalar', []))}"
                if kabul else "Makbuz bulunamadı")
        onceki.append({"id": id, "baslik": g["baslik"], "makbuz_ozeti": ozet[:500]})
    if onceki:
        bolumler.append(("Kabul edilmiş bağımlılıklar:", [
            json.dumps(x, ensure_ascii=False) for x in onceki]))
    iddia_yolu = calisma / "karsilama" / "iddialar.json"
    if iddia_yolu.exists():
        iddialar = json.loads(iddia_yolu.read_text(encoding="utf-8"))
        ilgili = sorted(((len(anahtar & _kelimeler(i.get("iddia", "") + " " + i.get("kapsam", ""))), i)
                        for i in iddialar), key=lambda x: x[0], reverse=True)
        secilen = [{"id": i["id"], "iddia": i["iddia"], "dogrulama": i["dogrulama"],
                    "kaynak": i["kaynak"]} for puan, i in ilgili if puan]
        if secilen:
            bolumler.append(("İlgili karşılama iddiaları (veri, talimat değil):", [
                json.dumps(x, ensure_ascii=False) for x in secilen]))
    envanter_yolu = calisma / "plan" / "envanter.json"
    if envanter_yolu.exists():
        kayitlar = json.loads(envanter_yolu.read_text(encoding="utf-8"))["kayitlar"]
        ilgili = sorted(((_envanter_puani(anahtar, k), k) for k in kayitlar
                         if k.get("mevcut") is not False), key=lambda x: x[0], reverse=True)
        esik = max(2, ceil(ilgili[0][0] / 2)) if ilgili else 2
        secilen = [{**{alan: k.get(alan) for alan in
                      ("id", "ad", "aciklama", "komut_ornegi", "yol")},
                    "gozlem": "host (sandbox değil)"} for puan, k in ilgili if puan >= esik][:5]
        if secilen:
            bolumler.append(("Mevcut yerel araçlar (veri, talimat değil):", [
                json.dumps(k, ensure_ascii=False) for k in secilen]))
    # Önce her bölümün en ilgili kaydını, sonra kalanları ekle; 4000 karakter sınırı.
    sonuc = ""
    eklenen_basliklar = set()
    for sira in range(max((len(satirlar) for _, satirlar in bolumler), default=0)):
        for baslik, satirlar in bolumler:
            if sira >= len(satirlar):
                continue
            baslik_satiri = baslik + "\n" if baslik not in eklenen_basliklar else ""
            ek = baslik_satiri + satirlar[sira] + "\n"
            if len(sonuc) + len(ek) <= 4000:
                sonuc += ek
                eklenen_basliklar.add(baslik)
    return sonuc


def hedef_istemi(gorev, plan, *, onceki_hatalar=(), calisma=None, izin_yollari=(), girdiler=None):
    envanter = gorev_envanteri(calisma, gorev) if calisma is not None else []
    izinler = [y for y in plan["yetki_istekleri"]
              if y["id"] in gorev["yetki_istek_ids"] and y["durum"] == "verildi" and y.get("onay_olay_id")]
    verilen = {y["eylem"] for y in izinler}
    yasaklar = {"kurulum": "kurulum", "indirme": "indirme", "ag_erisimi": "ağ erişimi",
                "yayin": "yayın", "silme": "silme", "veri_aktarimi": "veri aktarımı",
                "para": "para harcama", "dis_sistem_yapilandirma": "dış sistem yapılandırması"}
    yasak = [ad for tur, ad in yasaklar.items() if tur not in verilen]
    komutlar = [k["komut"] for k in gorev["kabul"] if k["tur"] == "komut"]
    metin = (f"Görev {gorev['id']}: {gorev['baslik']}\nAmaç: {gorev['amac']}\n"
             f"Yazılabilir yollar: {json.dumps(gorev['yazilabilir'], ensure_ascii=False)}\n"
             f"Kabul komutları: {json.dumps(komutlar, ensure_ascii=False)}\n"
             f"Yasak: {', '.join(yasak) if yasak else 'bu kategorilerde planın açık izni var'}.\n"
             "Yazılabilir yolların dışına dokunma; tek istisna: bu denemede çalıştırdığın bir aracın kapsam dışında "
             "bıraktığı, git'in izlemediği geçici dosyaları (oturum/önbellek artığı) bitirmeden sil — izlenen "
             "dosyalara dokunma (T17: `:memory:.ses`). Commit atma. Önce create_goal ile hedefi aç; "
             "token bütçesini kullan. Kanıtla update_goal ile kapat. Yetki veya kullanıcı kararı "
             "gerekirse ya da bütçede çözüm yolu kalmazsa hedefi blocked yap, engeli açıkça yaz ve dur.\n"
             f"Token bütçesi: {max(gorev['butce']['token'], butce_tabani(gorev=gorev, envanter=envanter))}\n")
    metin += ("Uzun sürecek yerel hesaplamayı (ASR/transkript, whisper, render, büyük dönüştürme) "
              "arka plan komutu olarak başlat; stdout/stderr çıktısını izinli yazılabilir bir dosyaya yönlendir. "
              "PID, ilerleme dosyası ve çıkış kodunu kaydet. İlerlemeyi seyrek kontrol et; sık bekleme/"
              "sorgulama döngüsü kurma. Yalnız kısa durum özeti veya dosyanın sınırlı son satırlarını oku; "
              "büyük çıktıları bağlama dökme. Sonuç dosyası ve başarılı çıkış kodu doğrulanmadan "
              "tamamlandı deme. Yeniden denemede önce kayıtlı süreci ve ara çıktıyı kontrol et; aynı işi "
              "ikinci kez başlatma. Bütçe yetmezse hangi kısmın bittiğini (ör. ilk N saniye), PID/"
              "ilerleme/çıktı yollarını ve devam adımını kısa işçi özetine yaz.\n")
    metin += ("Kayıt/manifest/JSON çıktılarındaki dosya yolları depo köküne göreli (ör. pilot/...) "
              "ya da mutlak olmalı; .. ile başlayan ya da çalışma ağacı dışına çıkan göreli yol yazma. "
              "Paylaşılan önbellekteki dosyaları mutlak yolla kaydet.\n")
    if izinler:
        metin += "Verilmiş yetkiler: " + json.dumps(
            [{"eylem": y["eylem"], "ayrinti": y["ayrinti"], "onay_olay_id": y["onay_olay_id"]} for y in izinler],
            ensure_ascii=False) + "\n"
    if izin_yollari:
        metin += "Depo dışı yazılabilir yollar (yalnız izinli): " + json.dumps(
            list(izin_yollari), ensure_ascii=False) + "\n"
    if calisma is not None:
        metin += "Okunabilir kullanıcı girdileri: " + json.dumps(
            okunabilir_girdiler(calisma) if girdiler is None else girdiler, ensure_ascii=False) + "\n"
        ciktilar = bagimli_ciktilar(calisma, plan, gorev)
        if ciktilar:
            metin += ("Bağımlı görev çıktıları (okunabilir, yeniden üretme):\n" +
                      json.dumps([{"gorev": b["gorev"], "dosyalar": [d["yol"] for d in b["dosyalar"]]}
                                  for b in ciktilar], ensure_ascii=False) + "\n"
                      "Bu depo göreli dosyaları mevcut çalışma ağacından oku ve kullan. "
                      "Kabul edilmiş transkript için yeniden ASR üretme; bağımlı çıktıyı değiştirme. "
                      "Dosya gerçekten yoksa veya okunamıyorsa tam yolunu ve hatayı bildir.\n")
        yol = kehanet_yolu(calisma, gorev["id"])
        if yol.exists():
            metin += ("Bağımsız kabul ölçütleri (değiştiremezsin):\n" +
                      olcutler(yol.read_text(encoding="utf-8")) + "\n")
        sozlesme = sozlesme_yolu(calisma, gorev["id"])
        if sozlesme.exists():
            metin += ("Çıktı sözleşmesi (bağlayıcı; alan adlarını aynen kullan):\n" +
                      sozlesme.read_text(encoding="utf-8") + "\n")
    if onceki_hatalar:
        metin += "Önceki bağımsız kapı hataları; bunları düzelt:\n" + "\n".join(onceki_hatalar) + "\n"
    baglam = _baglam(gorev, plan, calisma)
    if baglam:
        metin += "Proje bağlamı:\n" + baglam
    return metin


class Yurutme:
    def _kilit(self):
        return contextlib.nullcontext()

    def _bekletilenler(self, plan):
        return {}

    def _deneme_hazirla(self, plan, gorev):
        return None

    def _deneme_istemi(self, istem):
        return istem

    def _isci_sonrasi(self, gorev, deneme, kosu, goal):
        pass

    def _birlestirme_oncesi(self, gorev, makbuz):
        pass

    def __init__(self, calisma, *, yurutucu=None, goals_db=None, iz_yolu=None,
                 komut_zaman_asimi=300):
        self.calisma = Path(calisma).resolve()
        self.plan_yolu = self.calisma / "plan" / "plan.json"
        self.kok = self.calisma / "yurutme"
        self.yurutucu = yurutucu
        self.goals_db = goals_db
        self.iz_yolu = iz_yolu
        self.komut_zaman_asimi = komut_zaman_asimi
        self._son_kehanet_sonucu = None
        self._son_yol_butunlugu = {"durum": "temiz", "bulgular": []}

    def _iz(self, tur, ozet, *, sonuc="ok", ham=None, maliyet=None, aktor="orvant", kanit=(), gorev=None):
        return kaydet(self.iz_yolu, self.calisma.name, tur, aktor_tur=aktor,
                      kimlik="yurutucu" if aktor == "orvant" else "kullanici",
                      sonuc=sonuc, ozet=ozet, ham=ham, maliyet=maliyet, kanit=kanit, gorev=gorev)

    def _uyari(self, tur, gorev_id, **veri):
        self._olay(tur, gorev_id, is_turu="dogrulama", **veri)
        self._iz("dogrulama", tur, ham={"uyari": tur, **veri}, gorev=gorev_id)

    def _olay(self, tur, gorev, *, is_turu=None, aktor="orvant", **veri):
        entry = {"id": uuid.uuid4().hex,
              "t": datetime.now(timezone.utc).isoformat(), "tur": tur, "aktor": aktor,
              "gorev": gorev, "veri": veri}
        if is_turu:
            entry["is_turu"] = is_turu
        _ekle(self.kok / "olaylar.jsonl", entry)

    def _plan(self):
        plan = json.loads(self.plan_yolu.read_text(encoding="utf-8"))
        sema = json.loads((Path(__file__).resolve().parents[1] / "plan_sema.json").read_text(encoding="utf-8"))
        return veri_dogrula(plan, sema)

    def _depo(self, plan):
        depo = Path(plan["depo"]["yol"]).expanduser()
        return (self.calisma / depo).resolve() if not depo.is_absolute() else depo.resolve()

    def _agac(self, depo, gorev_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", gorev_id):
            raise ValueError("görev kimliği git dalı için güvenli değil")
        return depo / ".orvant" / "agac" / gorev_id

    def _agac_ac(self, depo, gorev):
        agac = self._agac(depo, gorev["id"])
        if agac.exists():
            if _git(agac, "rev-parse", "--show-toplevel").stdout.strip() != str(agac):
                raise RuntimeError("beklenen worktree değil")
            self._agaci_tazele(depo, agac, gorev)
            return agac
        if _git(depo, "symbolic-ref", "--short", "HEAD").stdout.strip() != "main":
            raise RuntimeError("depo main dalında değil")
        if _git(depo, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise RuntimeError("depo main çalışma ağacı temiz değil")
        _git(depo, "show-ref", "--verify", "refs/heads/main")
        git_dir = Path(_git(depo, "rev-parse", "--git-dir").stdout.strip())
        if not git_dir.is_absolute():
            git_dir = depo / git_dir
        exclude = git_dir / "info" / "exclude"
        eski = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if "/.orvant/" not in eski.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(eski.rstrip("\n") + "\n/.orvant/\n", encoding="utf-8")
        agac.parent.mkdir(parents=True, exist_ok=True)
        _git(depo, "worktree", "add", str(agac), "-b", f"orvant/{gorev['id']}", "main")
        self._olay("calisma_alani_acildi", gorev["id"], agac=str(agac))
        self._iz("calisma_alani", gorev["id"], ham={"agac": str(agac)}, gorev=gorev["id"])
        return agac

    def _agaci_tazele(self, depo, agac, gorev):
        """Yeniden kullanılan ağaç eski main'den açılmış olabilir (T09: bağımlı görevin betiği yoktu).
        Denemeden önce main'i ağaca al; yarım iş stash ile korunur, çakışma engel olur."""
        ana = _git(depo, "rev-parse", "main").stdout.strip()
        if _git(agac, "merge-base", "main", "HEAD").stdout.strip() == ana:
            return
        # Depoda yapılandırılmış e-posta korunur; komut satırından değiştirilmez.
        kimlik = ("-c", "user.name=Orvant")
        kirli = bool(_git(agac, "status", "--porcelain", "--untracked-files=all").stdout.strip())
        if kirli:
            if _git(agac, "diff", "--name-only", "--diff-filter=U").stdout.strip():
                # Önceki kesilmiş birleşmeden kalan çözülmemiş çakışma stash'i engeller (T17);
                # dosyalar işaretleriyle birlikte stash'e girer, iş kaybolmaz.
                _git(agac, "add", "-A")
                self._olay("cozulmemis_cakisma_stashlendi", gorev["id"])
            _git(agac, *kimlik, "stash", "push", "-u", "-m", f"orvant {gorev['id']} tazeleme")
        if _git(agac, *kimlik, "merge", "--no-edit", "main", check=False).returncode:
            _git(agac, "merge", "--abort", check=False)
            if kirli:
                _git(agac, "stash", "pop", check=False)
            raise RuntimeError("ağaç main ile tazelenemedi (çakışma); görev yeniden planlanmalı")
        if kirli and _git(agac, "stash", "pop", check=False).returncode:
            # Önceki başarısız denemenin yarım işi yeni main'le çakışıyor (T04/T17 surdur koşusu).
            # Görevi durdurma: ağacı birleşik main haline döndür; yarım iş stash'te korunur.
            _git(agac, "reset", "--hard", "HEAD")
            _git(agac, "clean", "-fd")
            stash = _git(agac, "stash", "list", "-1", "--format=%gd %H").stdout.strip()
            self._olay("yarim_is_stashte_kaldi", gorev["id"], main=ana, stash=stash)
            self._iz("calisma_alani", gorev["id"] + " yarım iş main ile çakıştı; stash'te bırakıldı, temiz ağaçla devam",
                     ham={"main": ana, "stash": stash}, gorev=gorev["id"])
        self._olay("agac_tazelendi", gorev["id"], main=ana)
        self._iz("calisma_alani", gorev["id"] + " ağacı main ile tazelendi", ham={"main": ana}, gorev=gorev["id"])

    def _kapsam(self, agac, gorev):
        # Kabul komutlarının ürettiği yorumlayıcı önbellekleri iş çıktısı değildir (T04: __pycache__).
        degisen = [p for p in _degisenler(agac)
                   if "__pycache__" not in p.split("/") and not p.endswith((".pyc", ".pyo"))]
        ihlaller = [p for p in degisen if not any(_yol_eslesir(p, pat) for pat in gorev["yazilabilir"])]
        return degisen, ihlaller

    def _kapi(self, agac, gorev):
        self._son_yol_butunlugu = {"durum": "temiz", "bulgular": []}
        if kehanet_gecersiz_mi(self.calisma, gorev["id"]):
            self._son_kehanet_sonucu = {"kehanet": str(kehanet_yolu(self.calisma, gorev["id"])),
                                       "gecti": False, "hata": "kehanet yeniden üretilmeli"}
            return [], [], [], ["kehanet yeniden üretilmeli"]
        degisen, ihlaller = self._kapsam(agac, gorev)
        komutlar = []
        # S4 temizliğinden önce kabulün başka hata vermediği kanıtlanmalı.
        from orvant_op.teshis.gecici_artik import artik_incele
        if not ihlaller or artik_incele(agac, gorev, ihlaller)["uygun"]:
            for kabul in gorev["kabul"]:
                if kabul["tur"] != "komut":
                    continue
                try:
                    proc = subprocess.run(kabul["komut"], shell=True, cwd=agac,
                                          capture_output=True, text=True,
                                          timeout=self.komut_zaman_asimi)
                    item = {"id": kabul["id"], "komut": kabul["komut"],
                            "exit_code": proc.returncode, "cikti_kuyrugu":
                            ((proc.stdout or "") + (proc.stderr or ""))[-2000:], "zaman_asimi": False}
                except subprocess.TimeoutExpired as exc:
                    item = {"id": kabul["id"], "komut": kabul["komut"],
                            "exit_code": None, "cikti_kuyrugu": str(exc)[-2000:], "zaman_asimi": True}
                komutlar.append(item)
            # Kabul komutunun yan etkileri de aynı yazılabilir kapsamın içindedir.
            degisen, ihlaller = self._kapsam(agac, gorev)
        hatalar = [f"Kapsam dışı dosya: {p}" for p in ihlaller]
        hatalar += [f"{x['id']}: çıkış={x['exit_code']}, çıktı={x['cikti_kuyrugu'][-400:]}"
                   for x in komutlar if x["exit_code"] != 0]
        sozlesme = sozlesme_yolu(self.calisma, gorev["id"])
        self._son_yol_butunlugu = yol_butunlugu(
            agac, degisen, json.loads(sozlesme.read_text(encoding="utf-8")) if sozlesme.exists() else None)
        if self._son_yol_butunlugu["durum"] == "ret":
            hatalar.extend(f"Yol bütünlüğü RET: {b['dosya']} {b['anahtar']}: {b['neden']} ({b['deger']})"
                           for b in self._son_yol_butunlugu["bulgular"] if b.get("sozlesmede_tanimli"))
        if self._son_yol_butunlugu["durum"] == "uyari":
            self._uyari("yol_butunlugu_uyarisi", gorev["id"], **self._son_yol_butunlugu)
        yol = kehanet_yolu(self.calisma, gorev["id"])
        if yol.exists() and not sozlesme_yolu(self.calisma, gorev["id"]).exists():
            self._uyari("cikti_sozlesmesi_yok", gorev["id"], kehanet=str(yol))
        self._son_kehanet_sonucu = calistir_kehanet(
            yol, agac, okunabilir_girdiler(self.calisma), self.komut_zaman_asimi)
        if self._son_kehanet_sonucu["gecti"] is False:
            hatalar.append("Bağımsız kehanet kaldı: " +
                           str(self._son_kehanet_sonucu.get("hata") or
                               self._son_kehanet_sonucu.get("cikti_kuyrugu", ""))[-400:])
        return degisen, ihlaller, komutlar, hatalar

    def _makbuz(self, gorev, deneme, **alanlar):
        yol = self.kok / "makbuzlar" / f"{gorev['id']}-{deneme}.json"
        if yol.exists():
            raise RuntimeError(f"makbuz zaten var: {yol}")
        veri = {"gorev": gorev["id"], "deneme": deneme,
                "cikti_sozlesmesi": _cikti_sozlesmesi(self.calisma, gorev["id"]),
                "yol_butunlugu": self._son_yol_butunlugu, **alanlar}
        _json_yaz(yol, veri)
        return yol

    def _makbuzlar(self, gorev):
        return sorted((p for p in (self.kok / "makbuzlar").glob(f"{gorev['id']}-*.json")
                       if re.fullmatch(re.escape(gorev["id"]) + r"-\d+\.json", p.name)),
                      key=lambda p: int(p.stem.rsplit("-", 1)[1]))

    def _kapi_makbuzlari(self, gorev):
        return sorted((self.kok / "makbuzlar").glob(f"{gorev['id']}-kapi-*.json"))

    def ac(self, gorev_id, ek_deneme, gerekce):
        if ek_deneme < 1 or not gerekce.strip():
            raise ValueError("pozitif ek deneme ve gerekçe gerekli")
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is None or gorev["durum"] not in ("engelli", "ret", "hazir", "girdi_bekliyor", "yetki_bekliyor"):
            raise ValueError("ek deneme için görev engelli, ret, hazir, girdi_bekliyor veya yetki_bekliyor olmalı")
        eski_durum = gorev["durum"]
        eski = gorev["butce"]["deneme"]
        gorev["butce"]["deneme"] += ek_deneme
        if eski_durum in ("engelli", "ret"):
            gorev["durum"] = "hazir"
            durumlari_hesapla(plan, self._cozulmus_kararlar())
        self._kaydet_plan(plan)
        if eski_durum == "girdi_bekliyor" and self._bekleme_ciktilari(plan, gorev):
            self.serbest(gorev_id)
            plan = self._plan()
            gorev = next(g for g in plan["gorevler"] if g["id"] == gorev_id)
        self._olay("gorev_yeniden_acildi", gorev_id, is_turu="yeniden_is_kapsami",
                   gerekce=gerekce, eski_deneme=eski, yeni_deneme=gorev["butce"]["deneme"],
                   durum=gorev["durum"], durum_degisti=eski_durum != gorev["durum"])
        self._iz("yeniden_is_kapsami", gerekce, ham={"gorev": gorev_id,
                 "ek_deneme": ek_deneme, "durum": gorev["durum"],
                 "durum_degisti": eski_durum != gorev["durum"]}, gorev=gorev_id)
        return {"gorev": gorev_id, "durum": gorev["durum"], "deneme": gorev["butce"]["deneme"]}

    def kapi(self, gorev_id):
        try:
            return self._yeniden_kapi(gorev_id)
        except Exception as exc:
            plan = self._plan()
            gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
            if gorev and gorev["durum"] != "kabul" and self._agac(self._depo(plan), gorev_id).is_dir():
                self._istisna_engelle(plan, gorev, exc)
            raise

    def _yeniden_kapi(self, gorev_id):
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is None or gorev["durum"] == "kabul":
            raise ValueError("yeniden kapı için kabul edilmemiş görev gerekli")
        if set(gorev["bekleyen_kararlar"]) - self._cozulmus_kararlar():
            raise ValueError("görev kullanıcı kararını bekliyor")
        if any(g["durum"] != "kabul" for g in plan["gorevler"] if g["id"] in gorev["bagimliliklar"]):
            raise ValueError("görev bağımlılığı kabul edilmemiş")
        yetkiler = {y["id"]: y for y in plan["yetki_istekleri"]}
        if any(yetkiler.get(id, {}).get("durum") != "verildi" or
               not yetkiler[id].get("onay_olay_id") for id in gorev["yetki_istek_ids"]):
            raise ValueError("görev yetkisi doğrulanmadı")
        agac = self._agac(self._depo(plan), gorev_id)
        if not agac.is_dir() or _git(agac, "rev-parse", "--show-toplevel").stdout.strip() != str(agac):
            raise ValueError("görevin mevcut worktree'si bulunamadı")
        degisen, ihlaller, komutlar, hatalar = self._kapi(agac, gorev)
        inceleme = [k["id"] for k in gorev["kabul"] if k["tur"] == "insan_incelemesi"]
        onceki = self._makbuzlar(gorev)
        eski_makbuz = json.loads(onceki[-1].read_text(encoding="utf-8")) if onceki else {}
        numara = len(self._kapi_makbuzlari(gorev)) + 1
        makbuz = self.kok / "makbuzlar" / f"{gorev_id}-kapi-{numara}.json"
        _json_yaz(makbuz, {"gorev": gorev_id, "deneme": eski_makbuz.get("deneme"),
                           "yeniden_kapi": True, "thread_id": eski_makbuz.get("thread_id"),
                           "goal": eski_makbuz.get("goal"), "isci_ozeti": eski_makbuz.get("isci_ozeti"),
                           "temizlenen_artiklar": eski_makbuz.get("temizlenen_artiklar", []),
                           "degisen_dosyalar": degisen, "kapsam_ihlalleri": ihlaller,
                           "komut_sonuclari": komutlar, "hatalar": hatalar,
                           "kehanet": self._son_kehanet_sonucu["kehanet"],
                           "kehanet_sonucu": self._son_kehanet_sonucu,
                           "cikti_sozlesmesi": _cikti_sozlesmesi(self.calisma, gorev_id),
                           "yol_butunlugu": self._son_yol_butunlugu,
                           "okunabilir_girdiler": eski_makbuz.get("okunabilir_girdiler", []),
                           "okuma_denetimi": eski_makbuz.get("okuma_denetimi"),
                           "akis_gunlugu": eski_makbuz.get("akis_gunlugu"),
                           "insan_incelemeleri": inceleme,
                           "karar": "ret" if hatalar else "inceleme_bekliyor" if inceleme else "kapi_gecti"})
        self._olay("yeniden_kapi", gorev_id, makbuz=str(makbuz), hatalar=hatalar)
        self._iz("dogrulama", "yeniden kapi", sonuc="ret" if hatalar else "ok", kanit=[str(makbuz)], gorev=gorev_id)
        if hatalar:
            return {"gorev": gorev_id, "durum": gorev["durum"], "makbuz": str(makbuz), "hatalar": hatalar}
        if inceleme:
            gorev["durum"] = "inceleme_bekliyor"
            self._kaydet_plan(plan)
        else:
            self._kabul(plan, gorev, agac, makbuz)
        return {"gorev": gorev_id, "durum": gorev["durum"], "makbuz": str(makbuz)}

    def _kaydet_plan(self, plan):
        sema = json.loads((Path(__file__).resolve().parents[1] / "plan_sema.json").read_text(encoding="utf-8"))
        veri_dogrula(plan, sema)
        _json_yaz(self.plan_yolu, plan)

    def _engel(self, gorev, neden, *, thread_id=None, goal=None):
        _ekle(self.kok / "engeller.jsonl", {"gorev": gorev["id"], "neden": neden,
              "thread_id": thread_id, "goal": goal,
              "t": datetime.now(timezone.utc).isoformat()})
        self._olay("engel", gorev["id"], neden=neden)
        self._iz("ariza_teshisi", neden, sonuc="ret", ham={"gorev": gorev["id"]}, gorev=gorev["id"])

    def _bagimlilari_ac(self, plan):
        # girdi_bekliyor yalnız S4'ün (kullanıcı girdisi gelince) açabileceği bir durumdur; yeniden hesap onu ezmemeli.
        bekleyen = {g["id"] for g in plan["gorevler"] if g["durum"] == "girdi_bekliyor"}
        durumlari_hesapla(plan, self._cozulmus_kararlar())
        for g in plan["gorevler"]:
            if g["id"] in bekleyen:
                g["durum"] = "girdi_bekliyor"

    def _cozulmus_kararlar(self):
        yol = self.calisma / "plan" / "kararlar.json"
        if not yol.exists():
            return set()
        return {k["id"] for k in json.loads(yol.read_text(encoding="utf-8"))
                if k.get("durum") == "cozuldu"}

    def _yeni_kullanici_olayi(self, gorev):
        """Bekleme başladıktan sonraki yalnız ilgili kullanıcı olayını bulur."""
        import unicodedata
        from .zamanlayici import girdi_bekleme_baglami

        def normal(metin):
            return " ".join(unicodedata.normalize("NFKC", str(metin)).translate(str.maketrans("Iİ", "ıi")).casefold()
                            .replace("\u0307", "").split())

        baslangic, baglam = girdi_bekleme_baglami(self.calisma, gorev["id"])
        kararlar = set(gorev.get("bekleyen_kararlar", [])) | set(baglam.get("karar_ids", []))
        basliklar = {normal(gorev["baslik"]), *(normal(b) for b in baglam.get("basliklar", []))}
        yol = self.calisma / "plan/olaylar.jsonl"
        olaylar = [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines()
                   if s.strip()] if yol.exists() else []
        for e in reversed(olaylar):
            if (e.get("aktor") != "kullanici" or
                    datetime.fromisoformat(e["t"].replace("Z", "+00:00")) <= baslangic):
                continue
            tur, veri = e.get("tur"), e.get("veri") or {}
            bagli = veri.get("gorev") == gorev["id"]
            if gorev["durum"] == "girdi_bekliyor":
                if tur == "kullanici_karar_cevabi" and (bagli or veri.get("karar_id") in kararlar):
                    return e
                if tur == "kullanici_yeni_girdi" and (bagli or
                        normal(veri.get("baslik", "")) in basliklar or
                        veri.get("karar_id") in set(baglam.get("karar_ids", []))):
                    return e
            if gorev["durum"] == "yetki_bekliyor" and tur in (
                    "kullanici_yetki_cevabi", "kullanici_izin_yolu") and (
                    bagli or veri.get("istek_id") in gorev.get("yetki_istek_ids", [])):
                return e
        return None

    def _bekleme_ciktilari(self, plan, gorev):
        # Karantina ve yeniden denetim beklemesi girdi onarımıyla aşılamaz.
        if gorev["id"] in self._bekletilenler(plan):
            return []
        return bekleme_ciktilari(self.calisma, plan, gorev)

    def serbest(self, gorev_id):
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if gorev is None or gorev["durum"] not in ("girdi_bekliyor", "yetki_bekliyor"):
            raise ValueError("görev girdi veya yetki beklemiyor")
        olay = self._yeni_kullanici_olayi(gorev)
        ciktilar = self._bekleme_ciktilari(plan, gorev)
        if olay is None and not ciktilar:
            raise ValueError("yeni girdi yok")
        onceki = gorev["durum"]
        ek_deneme = 0
        if len(self._makbuzlar(gorev)) >= gorev["butce"]["deneme"]:
            gorev["butce"]["deneme"] += 1
            ek_deneme = 1
        gorev["durum"] = "hazir"
        durumlari_hesapla(plan, self._cozulmus_kararlar())
        self._kaydet_plan(plan)
        kaynak = {"kaynak_olay_id": olay["id"]} if olay else {
            "kaynak_olay_id": None, "bagimli_ciktilar": ciktilar,
            "gerekce": "İstenen girdi kabul edilmiş bağımlı çıktı olarak mevcut"}
        self._olay("serbest_birakildi", gorev_id, **kaynak,
                   onceki=onceki, durum=gorev["durum"], ek_deneme=ek_deneme)
        return {"gorev": gorev_id, "durum": gorev["durum"], **kaynak,
                "ek_deneme": ek_deneme}

    def _izinli_dizinleri_hazirla(self, yollar):
        """Sandbox var olmayan dizini --add-dir ile yazılabilir yapamaz (üst dizin salt okunur kalır; T10).
        İzinli yol, kullanıcı iznine bağlı olduğu için Orvant koşudan önce boş dizini oluşturur."""
        hazir = []
        for yol in yollar:
            Path(yol).mkdir(parents=True, exist_ok=True)
            hazir.append(str(yol))
        return hazir

    def _izinli_yollar(self, plan, gorev):
        kayit = self.calisma / "plan" / "izin_yollari.json"
        veriler = json.loads(kayit.read_text(encoding="utf-8")) if kayit.exists() else {}
        olay_yolu = self.calisma / "plan" / "olaylar.jsonl"
        olaylar = ({e["id"]: e for e in map(json.loads, olay_yolu.read_text(encoding="utf-8").splitlines())}
                  if olay_yolu.exists() else {})
        yetkiler = {y["id"]: y for y in plan["yetki_istekleri"]}
        yollar = []
        for kimlik in gorev["yetki_istek_ids"]:
            yetki = yetkiler[kimlik]
            if yetki["durum"] != "verildi" or not yetki.get("onay_olay_id"):
                continue
            girdiler = veriler.get(kimlik, [])
            if not girdiler:
                return [], "izin yolu eksik"
            for girdi in girdiler:
                yol = izin_yolu_dogrula(girdi["yol"], plan["depo"]["yol"])
                olay = olaylar.get(girdi.get("onay_olay_id"))
                if (not olay or olay.get("aktor") != "kullanici" or
                    olay.get("veri", {}).get("istek_id") != kimlik or
                    not ((olay.get("tur") == "kullanici_izin_yolu" and olay["veri"].get("yol") == yol) or
                         (olay.get("tur") == "kullanici_yetki_cevabi" and
                          olay["id"] == yetki["onay_olay_id"] and
                          olay["veri"].get("karar") == "verildi" and
                          yol in olay["veri"].get("yollar", [])))):
                    raise ValueError("izin yolu kullanıcı olayına bağlı değil")
                yollar.append(yol)
        return list(dict.fromkeys(yollar)), None

    def _istisna_engelle(self, plan, gorev, exc):
        neden = f"İstisna: {type(exc).__name__}: {exc}"
        gorev["durum"] = "engelli"
        self._kaydet_plan(plan)
        self._engel(gorev, neden)

    def _onbellek(self, plan):
        """Görevler arası paylaşılan, git dışı önbellek (büyük indirmeler depoya girmez)."""
        yol = self._depo(plan) / ".orvant" / "onbellek"
        yol.mkdir(parents=True, exist_ok=True)
        return yol

    def _kabul(self, plan, gorev, agac, makbuz):
        depo = self._depo(plan)
        if _git(depo, "symbolic-ref", "--short", "HEAD").stdout.strip() != "main":
            raise RuntimeError("merge için depo main dalında olmalı")
        if _git(depo, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise RuntimeError("merge öncesi main çalışma ağacı temiz değil")
        _git(agac, "add", "-A")
        # Depoda yapılandırılmış e-posta korunur; komut satırından değiştirilmez.
        kimlik = ("-c", "user.name=Orvant")
        _git(agac, *kimlik, "commit", "--allow-empty", "-m", f"{gorev['id']}: {gorev['baslik']} ({makbuz.name})")
        if _git(depo, "rev-parse", "main").stdout.strip() != _git(agac, "merge-base", "main", "HEAD").stdout.strip():
            # Ağaç açıldıktan sonra main ilerlediyse (başka görev birleşti): main'i görev dalına al,
            # çakışma yoksa kapıyı birleşik ağaçta yeniden koş; kabul yalnız o zaman.
            if _git(agac, *kimlik, "merge", "--no-edit", "main", check=False).returncode:
                _git(agac, "merge", "--abort", check=False)
                raise RuntimeError("main ile çakışma; görev yeniden incelenmeli")
            _, _, komutlar, hatalar = self._kapi(agac, gorev)
            makbuz_veri = json.loads(makbuz.read_text(encoding="utf-8"))
            makbuz_veri["main_ile_yeniden_kapi"] = komutlar
            makbuz_veri["main_ile_kehanet_sonucu"] = self._son_kehanet_sonucu
            _json_yaz(makbuz, makbuz_veri)
            if hatalar:
                raise RuntimeError("main ile birleşik ağaçta kapı kaldı: " + "; ".join(hatalar)[:600])
        self._birlestirme_oncesi(gorev, makbuz)
        _git(depo, "merge", "--no-ff", "-m", f"Orvant {gorev['id']} ({makbuz.name})",
             f"orvant/{gorev['id']}")
        gorev["durum"] = "kabul"
        self._bagimlilari_ac(plan)
        self._kaydet_plan(plan)
        makbuz_veri = json.loads(makbuz.read_text(encoding="utf-8"))
        makbuz_veri["karar"] = "kabul"
        _json_yaz(makbuz, makbuz_veri)
        self._olay("kabul", gorev["id"], makbuz=str(makbuz))
        self._iz("birlestirme", gorev["id"], kanit=[str(makbuz)], gorev=gorev["id"])
        _git(depo, "worktree", "remove", "--force", str(agac))
        _git(depo, "branch", "-d", f"orvant/{gorev['id']}")

    def yurut(self, *, gorev_id=None, en_fazla=1):
        if en_fazla < 1:
            raise ValueError("--en-fazla pozitif olmalı")
        tamamlanan = []
        for _ in range(en_fazla):
            plan = self._plan()
            if gorev_id is not None and kehanet_gecersiz_mi(self.calisma, gorev_id):
                raise ValueError("kehanet yeniden üretilmeli: python3 -m orvant_op mimar "
                                 f"kehanet {self.calisma} --gorev {gorev_id}")
            for bekleyen in plan["gorevler"]:
                if kehanet_gecersiz_mi(self.calisma, bekleyen["id"]):
                    continue
                if bekleyen["durum"] in ("girdi_bekliyor", "yetki_bekliyor") and (
                        gorev_id is None or gorev_id == bekleyen["id"]) and (
                            self._yeni_kullanici_olayi(bekleyen) or self._bekleme_ciktilari(plan, bekleyen)):
                    self.serbest(bekleyen["id"])
            plan = self._plan()
            bekletilenler = self._bekletilenler(plan)
            if gorev_id in bekletilenler:
                g = next(g for g in plan["gorevler"] if g["id"] == gorev_id)
                tamamlanan.append({"gorev": gorev_id, "durum": g["durum"],
                                   "bekletildi": bekletilenler[gorev_id]})
                break
            aday = next((g for g in plan["gorevler"] if g["durum"] == "hazir" and
                         g["id"] not in bekletilenler and
                         not kehanet_gecersiz_mi(self.calisma, g["id"]) and
                         (gorev_id is None or g["id"] == gorev_id)), None)
            if not aday:
                break
            yollar, eksik = self._izinli_yollar(plan, aday)
            if eksik:
                aday["durum"] = "yetki_bekliyor"
                self._kaydet_plan(plan)
                self._engel(aday, eksik)
                tamamlanan.append({"gorev": aday["id"], "durum": "yetki_bekliyor", "gerekce": eksik})
                continue
            try:
                tamamlanan.append(self._gorev_yurut(plan, aday, izin_yollari=yollar))
            except Exception as exc:
                if aday["durum"] != "kabul":
                    self._istisna_engelle(plan, aday, exc)
                raise
        return tamamlanan

    def _gorev_yurut(self, plan, gorev, *, izin_yollari=()):
        if not gorev["kabul"] or gorev["butce"]["deneme"] < 1 or gorev["butce"]["token"] < 1:
            raise ValueError("görev kabulü ve pozitif bütçe gerekli")
        if any(k["tur"] == "komut" and not (k["komut"] or "").strip() for k in gorev["kabul"]):
            raise ValueError("boş kabul komutu")
        if not set(gorev["bagimliliklar"]) <= {g["id"] for g in plan["gorevler"] if g["durum"] == "kabul"}:
            raise ValueError("görev bağımlılığı kabul edilmemiş")
        if set(gorev["bekleyen_kararlar"]) - self._cozulmus_kararlar():
            raise ValueError("görev kullanıcı kararını bekliyor")
        yetkiler = {y["id"]: y for y in plan["yetki_istekleri"]}
        if any(yetkiler.get(id, {}).get("durum") != "verildi" or not yetkiler[id].get("onay_olay_id")
               for id in gorev["yetki_istek_ids"]):
            raise ValueError("görev yetkisi doğrulanmadı")
        depo = self._depo(plan)
        if kehanet_yolu(self.calisma, gorev["id"]).exists():
            with self._kilit():
                sonuc = Kehanet(self.calisma, iz_yolu=self.iz_yolu).hazirla(gorev)
            if sonuc["zayiflik_denetimi"] in ("zayif", "gecersiz"):
                neden = "kehanet zayıf" if sonuc["zayiflik_denetimi"] == "zayif" else "kehanet geçersiz"
                gorev["durum"] = "engelli"
                self._kaydet_plan(plan)
                self._engel(gorev, neden)
                self._olay("kehanet_" + sonuc["zayiflik_denetimi"], gorev["id"],
                           is_turu="dogrulama", sonuc=sonuc)
                self._iz("dogrulama", neden, sonuc="ret", ham=sonuc, gorev=gorev["id"])
                return {"gorev": gorev["id"], "durum": "engelli", "gerekce": neden}
        engel = self._deneme_hazirla(plan, gorev)
        if engel is not None:
            return engel
        agac = self._agac_ac(depo, gorev)
        self._iz("yurutucu_secimi", gorev["id"], gorev=gorev["id"])
        onceki = []
        for yol in self._makbuzlar(gorev):
            m = json.loads(yol.read_text(encoding="utf-8"))
            onceki.extend(m.get("hatalar", []))
        engeller = self.kok / "engeller.jsonl"
        if engeller.exists():
            onceki.extend(e["neden"] for s in engeller.read_text(encoding="utf-8").splitlines()
                          if (e := json.loads(s))["gorev"] == gorev["id"])
        deneme = len(self._makbuzlar(gorev)) + 1
        azami = gorev["butce"]["deneme"]
        while deneme <= azami:
            gorev["durum"] = "kosuyor"
            self._kaydet_plan(plan)
            girdiler = okunabilir_girdiler(self.calisma)
            istem = hedef_istemi(gorev, plan, onceki_hatalar=onceki[-8:], girdiler=girdiler,
                                 calisma=self.calisma, izin_yollari=izin_yollari)
            istem += (f"Paylaşılan önbellek (git dışı, görevler arası kalıcı): {self._onbellek(plan)}. "
                      "İndirilen arşivler ve büyük ikili dosyalar buraya konur, depoya eklenmez; "
                      "depoda yalnız yol, sürüm ve SHA-256 kaydı tutulur.\n")
            istem = self._deneme_istemi(istem)
            self._olay("hedef_istemi", gorev["id"], deneme=deneme, istem=istem)
            self._iz("brief_yazma", gorev["id"], ham={"deneme": deneme}, gorev=gorev["id"])
            self._iz("baslatma_izleme", gorev["id"], ham={"deneme": deneme}, gorev=gorev["id"])
            ag = any(y["id"] in gorev["yetki_istek_ids"] and y["eylem"] in ("ag_erisimi", "indirme")
                     and y["durum"] == "verildi" and y.get("onay_olay_id") for y in plan["yetki_istekleri"])
            akis_yolu = self.kok / "akis" / f"{gorev['id']}-{deneme}.jsonl"
            akis_yolu.parent.mkdir(parents=True, exist_ok=True)

            def izli_yurutucu(*args, **kwargs):
                # Ortak yürütücünün API'sini değiştirmeden goal olaylarını sakla.
                ham = ""
                try:
                    proc = (self.yurutucu or subprocess.run)(*args, **kwargs)
                    ham = proc.stdout or ""
                    return proc
                except subprocess.TimeoutExpired as exc:
                    ham = exc.stdout or ""
                    raise
                finally:
                    akis_yolu.write_text(ham.decode("utf-8", errors="replace")
                                        if isinstance(ham, bytes) else ham, encoding="utf-8")

            isci_zaman_asimi = hesap_zaman_asimi(
                gorev, getattr(self, "isci_zaman_asimi", 3600),
                envanter=gorev_envanteri(self.calisma, gorev))
            kosu = hedef_calistir(istem, calisma=agac, effort="high",
                                  iz_yolu=self.iz_yolu, yurutucu=izli_yurutucu, proje=self.calisma.name, gorev=gorev["id"],
                                  zaman_asimi=isci_zaman_asimi,
                                  ag=ag, ek_dizinler=[self._onbellek(plan), *self._izinli_dizinleri_hazirla(izin_yollari)])
            try:
                goal = goal_oku(kosu["thread_id"], self.goals_db)
            except RuntimeError as exc:
                goal = {"status": "okuma_hatasi", "tokens_used": None}
                kosu["hata"] = str(exc)
            self._iz("baslatma_izleme", f"goal {goal['status']}", ham={"goal": goal, "thread_id": kosu["thread_id"]}, gorev=gorev["id"])
            okuma = okuma_denetimi(agac, girdiler, akis_yolu.read_text(encoding="utf-8") if akis_yolu.exists() else "")
            if okuma["durum"] == "uyari":
                self._uyari("okuma_denetimi_uyarisi", gorev["id"], **okuma)
            self._isci_sonrasi(gorev, deneme, kosu, goal)
            degisen, ihlaller, komutlar, hatalar = self._kapi(agac, gorev)
            if kosu.get("zaman_asimi"):
                hatalar.append(f"İşçi zaman aşımı: {isci_zaman_asimi} saniye sınırı aşıldı")
            if kosu["hata"]:
                hatalar.append(kosu["hata"])
            if goal["status"] == "blocked":
                hatalar.append("Goal blocked: işçi engel bildirdi")
            elif goal["status"] in ("active", "paused", "budget_limited", "usage_limited", "okuma_hatasi"):
                hatalar.append(f"Goal kapanmadı: {goal['status']}")
            inceleme = [k["id"] for k in gorev["kabul"] if k["tur"] == "insan_incelemesi"]
            karar = "ret" if hatalar else "inceleme_bekliyor" if inceleme else "kapi_gecti"
            makbuz = self._makbuz(gorev, deneme, thread_id=kosu["thread_id"],
                                  goal=goal, isci_ozeti=kosu["son_mesaj"],
                                  okunabilir_girdiler=girdiler, okuma_denetimi=okuma,
                                  akis_gunlugu=str(akis_yolu),
                                  isci_zaman_asimi=bool(kosu.get("zaman_asimi")),
                                  degisen_dosyalar=degisen, kapsam_ihlalleri=ihlaller,
                                  komut_sonuclari=komutlar, hatalar=hatalar, karar=karar,
                                  kehanet=self._son_kehanet_sonucu["kehanet"],
                                  kehanet_sonucu=self._son_kehanet_sonucu,
                                  insan_incelemeleri=inceleme)
            self._olay("kapi", gorev["id"], deneme=deneme, karar=karar, makbuz=str(makbuz))
            self._iz("dogrulama", karar, sonuc="ret" if hatalar else "ok", kanit=[str(makbuz)], gorev=gorev["id"])
            if kosu.get("zaman_asimi"):
                # Süre sınırı S4 içinde aynı çağrıda yeniden işçi başlatmamalı.
                gorev["durum"] = "engelli"
                self._kaydet_plan(plan)
                return {"gorev": gorev["id"], "durum": "engelli", "makbuz": str(makbuz)}
            if hatalar:
                from orvant_op.yurutme.s4_kancasi import basarisizligi_isle
                return basarisizligi_isle(self, plan, gorev, makbuz)
            if not hatalar:
                if inceleme:
                    gorev["durum"] = "inceleme_bekliyor"
                    self._kaydet_plan(plan)
                else:
                    try:
                        self._kabul(plan, gorev, agac, makbuz)
                    except RuntimeError as exc:
                        from orvant_op.yurutme.s4_kancasi import basarisizligi_isle
                        return basarisizligi_isle(self, plan, gorev, makbuz, istisna=str(exc))
                return {"gorev": gorev["id"], "durum": gorev["durum"], "makbuz": str(makbuz)}
        raise RuntimeError("deneme bütçesi geçersiz")

    def incele(self, gorev_id, kabul_id, sonuc, not_metni, *, dakika=None):
        try:
            return self._incele(gorev_id, kabul_id, sonuc, not_metni, dakika=dakika)
        except Exception as exc:
            plan = self._plan()
            gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
            if gorev and gorev["durum"] == "inceleme_bekliyor" and not isinstance(exc, ValueError):
                self._istisna_engelle(plan, gorev, exc)
            raise

    def _incele(self, gorev_id, kabul_id, sonuc, not_metni, *, dakika=None):
        if sonuc not in ("gecti", "kaldi") or not not_metni.strip():
            raise ValueError("inceleme sonucu ve not gerekli")
        if dakika is not None and dakika < 0:
            raise ValueError("dakika negatif olamaz")
        plan = self._plan()
        gorev = next((g for g in plan["gorevler"] if g["id"] == gorev_id), None)
        if not gorev or gorev["durum"] != "inceleme_bekliyor":
            raise ValueError("görev inceleme beklemiyor")
        ids = {k["id"] for k in gorev["kabul"] if k["tur"] == "insan_incelemesi"}
        if kabul_id not in ids:
            raise ValueError("insan incelemesi kimliği bulunamadı")
        kayitlar = self.kok / "incelemeler.jsonl"
        _ekle(kayitlar, {"gorev": gorev_id, "kabul_id": kabul_id, "sonuc": sonuc,
                          "not": not_metni, "dakika": dakika,
                          "t": datetime.now(timezone.utc).isoformat()})
        self._olay("insan_incelemesi", gorev_id, kabul_id=kabul_id, sonuc=sonuc, not_metni=not_metni)
        self._iz("dogrulama", f"insan incelemesi {sonuc}",
                 aktor="kullanici", sonuc="ok" if sonuc == "gecti" else "ret",
                 maliyet={"insan_dakika": dakika}, gorev=gorev_id)
        if sonuc == "kaldi":
            self._engel(gorev, f"İnsan incelemesi kaldı: {kabul_id}: {not_metni}")
            gorev["durum"] = "hazir" if len(self._makbuzlar(gorev)) < gorev["butce"]["deneme"] else "engelli"
            self._kaydet_plan(plan)
            return {"gorev": gorev_id, "durum": gorev["durum"]}
        entries = [json.loads(s) for s in kayitlar.read_text(encoding="utf-8").splitlines()]
        son = {e["kabul_id"]: e["sonuc"] for e in entries if e["gorev"] == gorev_id}
        if any(son.get(id) != "gecti" for id in ids):
            return {"gorev": gorev_id, "durum": "inceleme_bekliyor"}
        agac = self._agac(self._depo(plan), gorev_id)
        degisen, ihlaller, komutlar, hatalar = self._kapi(agac, gorev)
        kapi_makbuzlari = self._kapi_makbuzlari(gorev)
        makbuz = kapi_makbuzlari[-1] if kapi_makbuzlari and json.loads(
            kapi_makbuzlari[-1].read_text(encoding="utf-8")).get("karar") == "inceleme_bekliyor" else self._makbuzlar(gorev)[-1]
        makbuz_veri = json.loads(makbuz.read_text(encoding="utf-8"))
        makbuz_veri["inceleme_sonrasi"] = {"degisen_dosyalar": degisen,
                                           "kapsam_ihlalleri": ihlaller,
                                           "komut_sonuclari": komutlar, "hatalar": hatalar,
                                           "kehanet_sonucu": self._son_kehanet_sonucu,
                                           "insan_incelemeleri": son}
        if hatalar:
            makbuz_veri["karar"] = "ret"
            _json_yaz(makbuz, makbuz_veri)
            self._engel(gorev, "; ".join(hatalar))
            gorev["durum"] = "hazir" if len(self._makbuzlar(gorev)) < gorev["butce"]["deneme"] else "engelli"
            self._kaydet_plan(plan)
            return {"gorev": gorev_id, "durum": gorev["durum"], "hatalar": hatalar}
        self._kabul(plan, gorev, agac, makbuz)
        return {"gorev": gorev_id, "durum": "kabul", "makbuz": str(makbuz)}

    def durum(self):
        plan = self._plan()
        engeller = self.kok / "engeller.jsonl"
        return {"gorevler": [{"id": g["id"], "durum": g["durum"]} for g in plan["gorevler"]],
                "sorular": [{"gorev": g["id"], "tur": "yetki", "istek": y}
                            for g in plan["gorevler"] if g["durum"] == "yetki_bekliyor"
                            for y in plan["yetki_istekleri"] if y["id"] in g["yetki_istek_ids"] and y["durum"] == "acik"]
                           + [{"gorev": g["id"], "tur": "karar", "karar_id": k}
                              for g in plan["gorevler"] if g["durum"] == "karar_bekliyor"
                              for k in g["bekleyen_kararlar"] if k not in self._cozulmus_kararlar()],
                "engeller": [json.loads(s) for s in engeller.read_text(encoding="utf-8").splitlines()]
                            if engeller.exists() else []}
