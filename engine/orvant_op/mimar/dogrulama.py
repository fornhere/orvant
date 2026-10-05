"""Model çıktısını ve görev grafiğini deterministik olarak denetler."""
from orvant_op.karsilama.roller import veri_dogrula
from .roller import SEMA_YOLU
import copy
import fnmatch
import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

from orvant_op import uyum_komut
from orvant_op.uyum_komut import WINDOWS, bol_islecli, birlestir, komut_adi


def kalip_eslesir(yol, kalip):
    """Yürütücünün bileşen bazlı yazılabilir kalıp sözleşmesi."""
    if not kalip or kalip.startswith("/") or ".." in PurePosixPath(kalip).parts:
        return False
    if WINDOWS:
        # Sözleşme `/` ayırıcılı göreli kalıptır; `\` ya da sürücü harfi içeren kalıp fail-closed reddedilir.
        # Yol (git/`Path` çıktısı) `\` ile gelebilir; dosya sistemi büyük/küçük harf duyarsızdır.
        if "\\" in kalip or re.match(r"[A-Za-z]:", kalip):
            return False
        yol, kalip = yol.replace("\\", "/").lower(), kalip.lower()
    if kalip.endswith("/"):
        kalip += "**"
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


def komut_yollari(komut, *, depo=None, uyarilar=None, _derinlik=0):
    """Komutu çalıştırmadan depo içi dosya ve Python modülü adaylarını çıkarır."""
    if _derinlik > 3:
        return []
    try:
        # Bitişik kabuk ayraçları da ayrılır; tırnak içindeki ayraçlar korunur. Windows'ta `\` kaçış değildir.
        parcalar = bol_islecli(komut)
    except ValueError:
        parcalar = re.split(r"\s+|&&|\|\||;|\|", komut)
    sonuc = []
    uzantilar = (".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".json", ".jsonl",
                ".txt", ".toml", ".yaml", ".yml", ".csv", ".srt", ".md")

    def ekle(aday):
        if aday.startswith("--") and "=" in aday:
            aday = aday.split("=", 1)[1]
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", aday):
            return
        if WINDOWS:
            # `tests\a.py` -> `tests/a.py`; sürücü harfli (`C:`) ve UNC (`\sunucu`) yollar depo dışıdır.
            if re.match(r"[A-Za-z]:", aday):
                return
            aday = aday.replace("\\", "/")
        while aday.startswith("./"):
            aday = aday[2:]
        if (not aday or aday.startswith(("/", "~", "-")) or "://" in aday or
                ".." in aday or any(c in aday for c in "*?[]") or
                "$(" in aday or "${" in aday or
                not ("/" in aday or (aday.lower() if WINDOWS else aday).endswith(uzantilar))):
            return
        if aday not in sonuc:
            sonuc.append(aday)

    alt = []

    def incele(belirtecler):
        if not belirtecler:
            return
        bas = 0
        if belirtecler[0] == "env":
            bas = 1
        while bas < len(belirtecler) and re.match(r"^[A-Za-z_]\w*=", belirtecler[bas]):
            bas += 1
        if bas >= len(belirtecler):
            return
        ad = komut_adi(belirtecler[bas])
        if WINDOWS and ad in ("cmd", "powershell", "pwsh"):
            # `cmd /c "..."` ve `powershell -Command "..."`: iç komut dizisini ayrıca çöz.
            bayrak = {"cmd": ("/c", "/k"), "powershell": ("-command", "-c"), "pwsh": ("-command", "-c")}[ad]
            for i in range(bas + 1, len(belirtecler) - 1):
                if belirtecler[i].lower() in bayrak:
                    kalan = belirtecler[i + 1:]
                    for yol in komut_yollari(kalan[0] if len(kalan) == 1 else birlestir(kalan), depo=depo,
                                            uyarilar=uyarilar, _derinlik=_derinlik + 1):
                        if yol not in sonuc:
                            sonuc.append(yol)
                    return
        if ad in ("sh", "bash"):
            for i in range(bas + 1, len(belirtecler) - 1):
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", belirtecler[i]):
                    for yol in komut_yollari(belirtecler[i + 1], depo=depo, uyarilar=uyarilar,
                                            _derinlik=_derinlik + 1):
                        if yol not in sonuc:
                            sonuc.append(yol)
                    return
        if ad == "make":
            for yol in _make_yollari(belirtecler[bas + 1:], depo, uyarilar, _derinlik):
                if yol not in sonuc:
                    sonuc.append(yol)
            return
        modul_indeksi = uyum_komut.python_modul_indeksi(belirtecler)
        for i, ad in enumerate(belirtecler):
            if i == modul_indeksi:
                if ad not in ("unittest", "pytest", "pip", "venv", "json.tool", "http.server"):
                    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", ad):
                        ekle(ad.replace(".", "/") + ".py")
                        ekle(ad.replace(".", "/") + "/__main__.py")
                continue
            if (modul_indeksi is not None and i > modul_indeksi and
                    belirtecler[modul_indeksi] in ("unittest", "pytest") and
                    not ad.endswith(uzantilar) and
                    re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", ad)):
                bolumler = ad.split(".")
                for uzunluk in range(2, len(bolumler) + 1):
                    ekle("/".join(bolumler[:uzunluk]) + ".py")
            else:
                ekle(ad)

    for ad in parcalar:
        if ad in ("&&", "||", ";", "|"):
            incele(alt)
            alt = []
        else:
            alt.append(ad)
    incele(alt)
    return sonuc


def _make_yollari(args, depo, uyarilar, derinlik):
    """Basit hedef ve reçeteleri okur; değişkenleri genişletmez, make çalıştırmaz."""
    kok = Path(depo).expanduser().resolve() if depo is not None else None
    dizin, dosya, hedefler = Path("."), None, []
    i = 0
    while i < len(args):
        ad = args[i]
        if ad in ("-C", "-f") and i + 1 < len(args):
            if ad == "-C":
                dizin /= args[i + 1]
            else:
                dosya = args[i + 1]
            i += 2
            continue
        if not ad.startswith("-") and "=" not in ad and "$(" not in ad:
            hedefler.append(ad)
        i += 1
    taban = (kok / dizin).resolve() if kok is not None else None
    aday = dizin / (dosya or "Makefile")
    if taban is not None:
        adlar = [dosya] if dosya else ["Makefile", "makefile", "GNUmakefile"]
        bulunan = next((taban / a for a in adlar if (taban / a).is_file()), None)
        if bulunan is not None:
            aday = bulunan
    else:
        bulunan = None

    def goreli(yol):
        if kok is not None:
            try:
                return (kok / yol).resolve().relative_to(kok).as_posix()
            except ValueError:
                return None
        return yol.as_posix() if not yol.is_absolute() and ".." not in yol.parts else None

    make_yolu = goreli(aday)
    sonuc = [make_yolu] if make_yolu else []

    def uyari(metin):
        if uyarilar is not None:
            uyarilar.append(metin)

    if bulunan is None:
        uyari("Makefile yok: make hedefi yolları çıkarılamadı")
        return sonuc
    if make_yolu is None:
        uyari("Makefile depo dışında: make hedefi yolları çıkarılamadı")
        return sonuc
    try:
        satirlar = bulunan.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        uyari("Makefile okunamadı: make hedefi yolları çıkarılamadı")
        return sonuc
    hedef_kayitlari, etkin, ilk = {}, [], None
    for satir in satirlar:
        if satir.startswith("\t"):
            for hedef in etkin:
                hedef_kayitlari[hedef].append(satir.lstrip().lstrip("@-+").lstrip())
            continue
        if not satir.strip() or satir.lstrip().startswith("#"):
            continue
        etkin = []
        es = re.match(r"^([^\s:=]+(?:[ ]+[^\s:=]+)*):(?!=|:=)(.*)$", satir)
        if not es or "$" in es[1]:
            continue
        etkin = es[1].split()
        for hedef in etkin:
            hedef_kayitlari.setdefault(hedef, []).append(es[2])
            if ilk is None and not hedef.startswith("."):
                ilk = hedef
    for hedef in hedefler or ([ilk] if ilk else []):
        if hedef not in hedef_kayitlari:
            uyari(f"Makefile hedefi bulunamadı: {hedef}; yollar çıkarılamadı")
            continue
        for komut in hedef_kayitlari[hedef]:
            for yol in komut_yollari(komut, depo=taban, uyarilar=uyarilar, _derinlik=derinlik + 1):
                yol = goreli(taban / yol)
                if yol and yol not in sonuc:
                    sonuc.append(yol)
    if not hedefler and ilk is None:
        uyari("Makefile varsayılan hedefi bulunamadı; yollar çıkarılamadı")
    return sonuc


def _atalar(plan):
    harita = {g["id"]: g for g in plan["gorevler"]}
    sonuc, etkin = {}, set()

    def gez(gid):
        if gid in etkin:
            raise ValueError("görev bağımlılığında döngü")
        if gid not in harita:
            raise ValueError(f"bilinmeyen bağımlılık: {gid}")
        if gid not in sonuc:
            etkin.add(gid)
            atalar = set()
            for dep in harita[gid]["bagimliliklar"]:
                atalar.add(dep)
                atalar.update(gez(dep))
            etkin.remove(gid)
            sonuc[gid] = atalar
        return sonuc[gid]

    for gid in harita:
        gez(gid)
    return sonuc


def kabul_bagimliliklari(plan):
    """Kabul dosyasının sahipliği ile görev grafiğini salt okunur karşılaştırır."""
    atalar = _atalar(plan)
    sonuc = {"eklenecek": [], "hatalar": [], "uyarilar": []}
    ciftler = set()
    for gorev in plan["gorevler"]:
        gid = gorev["id"]
        if gorev["durum"] == "kabul":
            continue
        for kabul in gorev["kabul"]:
            if kabul["tur"] != "komut":
                continue
            uyarilar = []
            yollar = komut_yollari(kabul["komut"] or "", depo=plan.get("depo", {}).get("yol"),
                                  uyarilar=uyarilar)
            sonuc["uyarilar"].extend({"gorev": gid, "kabul_id": kabul["id"], "uyari": u}
                                      for u in uyarilar)
            for yol in yollar:
                yazanlar = [g["id"] for g in plan["gorevler"]
                            if any(kalip_eslesir(yol, k) for k in g["yazilabilir"])]
                if not yazanlar or gid in yazanlar or atalar[gid].intersection(yazanlar):
                    continue
                adaylar = [w for w in yazanlar if gid not in atalar[w]]
                veri = {"gorev": gid, "yol": yol, "kabul_id": kabul["id"]}
                if not adaylar:
                    sonuc["hatalar"].append({**veri, "yazanlar": yazanlar,
                                             "neden": "yazan görevler bu görevin ardılı"})
                for aday in adaylar:
                    if (gid, aday) not in ciftler:
                        sonuc["eklenecek"].append({**veri, "bagimlilik": aday})
                        ciftler.add((gid, aday))
    return sonuc


def _kabul_hatasi(bulgular):
    hata = bulgular["hatalar"][0]
    return (f"kabul komutu başka görevin yazdığı {hata['yol']} dosyasını çağırıyor; "
            f"yazan görevler ({', '.join(hata['yazanlar'])}) bu görevin ardılı")


def kabul_bagimliliklarini_duzelt(plan):
    """Bağımlılıkları kopyaya ekler; toplu eklemenin döngüsünü de reddeder."""
    yeni = copy.deepcopy(plan)
    bulgular = kabul_bagimliliklari(yeni)
    if bulgular["hatalar"]:
        raise ValueError(_kabul_hatasi(bulgular))
    harita = {g["id"]: g for g in yeni["gorevler"]}
    for ek in bulgular["eklenecek"]:
        deps = harita[ek["gorev"]]["bagimliliklar"]
        if ek["bagimlilik"] not in deps:
            deps.append(ek["bagimlilik"])
    _atalar(yeni)
    return yeni, bulgular


def _tekil(kayitlar, ad):
    ids = [x["id"] for x in kayitlar]
    if any(not isinstance(x, str) or not x.strip() for x in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"{ad}: kimlik boş veya yinelenmiş")
    return set(ids)


def dogrula(plan, sozlesme, kararlar):
    """Şema, referanslar, kapsam ve döngü denetimi; durumları ayrı hesaplar."""
    veri_dogrula(plan, json.loads(SEMA_YOLU.read_text(encoding="utf-8")))
    if plan["sozlesme_revizyon"] != sozlesme["revizyon"] or plan["surum"] != 1:
        raise ValueError("plan sürümü veya sözleşme revizyonu uyuşmuyor")
    if not plan["depo"]["yol"].strip() or not plan["depo"]["gerekce"].strip():
        raise ValueError("depo önerisi eksik")
    gorevler = plan["gorevler"]
    if not gorevler:
        raise ValueError("plan görev içermiyor")
    gorev_ids = _tekil(gorevler, "görev")
    yetki_ids = _tekil(plan["yetki_istekleri"], "yetki")
    kabul_ids = _tekil(sozlesme["kabul_olcutleri"], "sözleşme kabulü")
    karar_ids = {k["id"] for k in kararlar if k.get("durum") == "ertelendi"}
    bagli = set()
    for istek in plan["yetki_istekleri"]:
        if any(not istek[k].strip() for k in ("eylem", "ayrinti", "gerekce")):
            raise ValueError("yetki isteği eksik")
        if istek["durum"] != "acik" or istek["onay_olay_id"] is not None:
            raise ValueError("model yetki veremez")
    ziyaret = set()
    yigin = set()
    harita = {g["id"]: g for g in gorevler}

    def gez(gid):
        if gid in yigin:
            raise ValueError("görev bağımlılığında döngü")
        if gid in ziyaret:
            return
        yigin.add(gid)
        for dep in harita[gid]["bagimliliklar"]:
            if dep not in gorev_ids:
                raise ValueError(f"bilinmeyen bağımlılık: {dep}")
            gez(dep)
        yigin.remove(gid)
        ziyaret.add(gid)

    for g in gorevler:
        if any(not g[k].strip() for k in ("baslik", "amac")):
            raise ValueError("görev açıklaması boş")
        if not g["kabul"]:
            raise ValueError(f"kabulsüz görev: {g['id']}")
        if g["durum"] != "hazir":
            raise ValueError("model başlangıç durumunu hazir yazmalı")
        if g["butce"]["token"] < 0 or g["butce"]["deneme"] < 1:
            raise ValueError("geçersiz görev bütçesi")
        for alan, izinli in (("yetki_istek_ids", yetki_ids),
                             ("bekleyen_kararlar", karar_ids)):
            refs = g[alan]
            if len(refs) != len(set(refs)) or not set(refs) <= izinli:
                raise ValueError(f"görevde geçersiz {alan}")
        gids = [k["id"] for k in g["kabul"]]
        if any(not x.strip() for x in gids) or len(gids) != len(set(gids)):
            raise ValueError("görev kabul kimliği boş veya yinelenmiş")
        for kabul in g["kabul"]:
            ref = kabul["sozlesme_kabul_id"]
            if ref is not None:
                if ref not in kabul_ids:
                    raise ValueError(f"bilinmeyen sözleşme kabulü: {ref}")
                bagli.add(ref)
            if not kabul["beklenen"].strip():
                raise ValueError("beklenen sonuç boş")
            if kabul["tur"] == "komut" and not (kabul["komut"] or "").strip():
                raise ValueError("boş kabul komutu")
            if kabul["tur"] == "insan_incelemesi" and not (kabul["rubrik"] or "").strip():
                raise ValueError("insan incelemesi rubriksiz")
        gez(g["id"])
    kapsanmayan = plan["kapsanmayan_kabul"]
    dis_ids = [x["sozlesme_kabul_id"] for x in kapsanmayan]
    if len(dis_ids) != len(set(dis_ids)) or not set(dis_ids) <= kabul_ids:
        raise ValueError("kapsanmayan kabul kimliği geçersiz")
    if any(not x["gerekce"].strip() for x in kapsanmayan):
        raise ValueError("kapsanmayan kabul gerekçesiz")
    if (bagli & set(dis_ids)) or (bagli | set(dis_ids)) != kabul_ids:
        raise ValueError("sözleşme kabulü kapsanmamış veya çakışmış")
    bulgular = kabul_bagimliliklari(plan)
    if bulgular["hatalar"]:
        raise ValueError(_kabul_hatasi(bulgular))
    if bulgular["eklenecek"]:
        raise ValueError(f"kabul komutu bağımlılıkları eksik: {bulgular['eklenecek']}")
    return plan


def durumlari_hesapla(plan, cozulmus_kararlar=()):
    """Bekleme önceliği: reddedilen yetki, açık yetki, karar, bağımlılık."""
    yetkiler = {x["id"]: x for x in plan["yetki_istekleri"]}
    cozulmus_kararlar = set(cozulmus_kararlar)
    durumlar = {g["id"]: g["durum"] for g in plan["gorevler"]}
    for g in plan["gorevler"]:
        if g["durum"] in ("kosuyor", "inceleme_bekliyor", "girdi_bekliyor", "kabul", "ret", "engelli"):
            continue
        izinler = [yetkiler[x] for x in g["yetki_istek_ids"]]
        if any(i["durum"] == "reddedildi" for i in izinler):
            g["durum"] = "engelli"
        elif any(i["durum"] != "verildi" or not i.get("onay_olay_id") for i in izinler):
            g["durum"] = "yetki_bekliyor"
        elif set(g["bekleyen_kararlar"]) - cozulmus_kararlar:
            g["durum"] = "karar_bekliyor"
        elif any(durumlar[d] != "kabul" for d in g["bagimliliklar"]):
            g["durum"] = "bekliyor"
        else:
            g["durum"] = "hazir"
    return plan
