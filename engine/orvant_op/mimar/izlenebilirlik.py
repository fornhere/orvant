"""Üretim iddialarını onaylı metne ve betiğin gözlenebilir kontrollerine bağlar."""
import ast
import os
import re
from pathlib import Path


def kaynaklar(veri):
    """Görev kabulünün güncel metni ve bağlı sözleşme maddeleri; model beyanı değil."""
    sonuc = {("kabul", k["id"]): k["metin"] for k in veri["kabul_olcutleri"]}
    sonuc.update({("gereksinim", k["id"]): k["metin"]
                  for k in veri.get("gereksinimler", [])})
    degisiklikler = {k.get("kabul_id") for k in veri["kullanici_onayli_kabul_degisiklikleri"]}
    for k in veri["gorev"]["kabul"]:
        bag = ("kabul", k.get("sozlesme_kabul_id"))
        metin = sonuc.get(bag, k["beklenen"])
        if k["id"] in degisiklikler:
            metin = k["beklenen"]
        sonuc[("kabul", k["id"])] = metin
        if k.get("sozlesme_kabul_id") and k["id"] in degisiklikler:
            sonuc[("kabul", k["sozlesme_kabul_id"])] = k["beklenen"]
    return [{"kaynak_turu": tur, "kaynak_id": kid, "kaynak_metin": metin}
            for (tur, kid), metin in sonuc.items()]


def _metin(metin):
    return re.sub(r"\s+", " ", metin.casefold().replace("i\u0307", "i"))


def _sinir(dugum):
    return bool(re.search(r"baslang|biti[sş]|start|end|sinir|sınır|boundary",
                          _metin(ast.unparse(dugum))))


def _tolerans(dugum):
    return any(isinstance(d, ast.Call) and (
        isinstance(d.func, ast.Name) and d.func.id in ("abs", "isclose", "assertAlmostEqual") or
        isinstance(d.func, ast.Attribute) and d.func.attr in ("isclose", "assertAlmostEqual"))
        for d in ast.walk(dugum)) or bool(re.search(
            r"toleran|yuvarla|epsilon|rel_tol|abs_tol", ast.unparse(dugum), re.I))


def denetle(betik, cevap, veri, *, depo=None):
    """Kimlik/tam metin/alınan iddia ve somut alan kapsamını deterministik denetler."""
    izler = cevap["izlenebilirlik"]
    kaynak = {(k["kaynak_turu"], k["kaynak_id"]): k["kaynak_metin"]
              for k in kaynaklar(veri)}
    girdiler = set(veri["okunabilir_girdiler"])
    if depo is not None:
        # Mevcut açık girdi yolunun aynı depo içi göreli yazımı; yeni okuma yetkisi değil.
        for yol in tuple(girdiler):
            p = Path(yol)
            if p.is_relative_to(depo):
                girdiler.add(str(p.relative_to(depo)))
    kapsamlar = set(cevap["sozlesme"]["arac_alanlari"]) | girdiler
    dosyalar = list(cevap["sozlesme"]["dosyalar"])
    dosyalar.extend(d for b in veri["bagimli_ciktilar"] for d in b["dosyalar"])
    depo_kaynaklari = veri.get("kaynak_icerikleri", [])
    kapsamlar.update(k["yol"] for k in depo_kaynaklari)
    kapsamlar.update(s for k in depo_kaynaklari for s in k.get("sutunlar", []))
    for d in dosyalar:
        kapsamlar.add(d["yol"])
        for alan in d["alanlar"]:
            kapsamlar.add(alan["ad"])
            kapsamlar.update(p.replace("[]", "") for p in alan["ad"].split("."))
    adlar = [i["kontrol"] for i in izler]
    if len(set(adlar)) != len(adlar):
        raise ValueError("izlenebilirlik kontrol adı tekrarlı")
    for iz in izler:
        metin = kaynak.get((iz["kaynak_turu"], iz["kaynak_id"]))
        if metin is None or iz["kaynak_metin"] != metin:
            raise ValueError("izlenebilirlik kaynak kimliği/tam metni sözleşmeyle uyuşmuyor")
        if not iz["iddia"].strip() or iz["iddia"] not in metin:
            raise ValueError("kehanet iddiası kabul/gereksinim metninin kapsamında değil")
        if any(k not in kapsamlar for k in iz["kapsam"]):
            raise ValueError("izlenebilirlik kapsamı çıktı/bağımlı girdi sözleşmesinde yok")
        if iz["karsilastirma"] == "tarihsel":
            raise ValueError("gözlenemeyen tarihsel mutlak kanıt kehanette sınanamaz")
        if iz["karsilastirma"] == "esitlik" and not re.search(
                r"eşit|esit|eşleş|esles|birebir|aynı|ayni|equal|exact|match|==", _metin(metin)):
            raise ValueError("sözleşme dışında eşitlik ölçütü")
        if iz["karsilastirma"] == "tolerans" and not re.search(
                r"toleran|yuvarla|±|epsilon|round", _metin(metin)):
            raise ValueError("sözleşme dışında tolerans ölçütü")
    agac = ast.parse(betik)
    atamalar = {}
    for d in ast.walk(agac):
        if isinstance(d, ast.Assign):
            for hedef in d.targets:
                if isinstance(hedef, ast.Name):
                    atamalar.setdefault(hedef.id, []).append(d.value)
        elif isinstance(d, ast.AnnAssign) and isinstance(d.target, ast.Name) and d.value is not None:
            atamalar.setdefault(d.target.id, []).append(d.value)
    path_adlari = {"Path", "PurePath"}
    yakin_adlari = {"isclose"}
    for d in ast.walk(agac):
        if isinstance(d, ast.ImportFrom):
            if d.module == "pathlib":
                path_adlari.update(a.asname or a.name for a in d.names if a.name in path_adlari)
            if d.module == "math":
                yakin_adlari.update(a.asname or a.name for a in d.names if a.name == "isclose")

    def bagli(dugum):
        bekleyen = [dugum]
        gorulen = set()
        adlar = set()
        while bekleyen:
            d = bekleyen.pop()
            if id(d) in gorulen:
                continue
            gorulen.add(id(d))
            yield d
            for ad in ast.walk(d):
                if isinstance(ad, ast.Name) and ad.id in atamalar and ad.id not in adlar:
                    adlar.add(ad.id)
                    bekleyen.extend(atamalar[ad.id])

    def sabit_metin(dugum, gorulen=()):
        if isinstance(dugum, ast.Constant) and isinstance(dugum.value, str):
            return dugum.value
        if isinstance(dugum, ast.Name) and dugum.id in atamalar and dugum.id not in gorulen:
            degerler = {sabit_metin(d, (*gorulen, dugum.id)) for d in atamalar[dugum.id]}
            return degerler.pop() if len(degerler) == 1 else None
        if isinstance(dugum, ast.BinOp) and isinstance(dugum.op, ast.Add):
            sol, sag = sabit_metin(dugum.left, gorulen), sabit_metin(dugum.right, gorulen)
            return sol + sag if sol is not None and sag is not None else None
        return None

    def sabit_yol(d, gorulen=()):
        metin = sabit_metin(d, gorulen)
        if metin is not None:
            return os.path.normpath(metin)
        if isinstance(d, ast.Name) and d.id in atamalar and d.id not in gorulen:
            degerler = {sabit_yol(a, (*gorulen, d.id)) for a in atamalar[d.id]}
            return degerler.pop() if len(degerler) == 1 else None
        if isinstance(d, ast.Attribute) and d.attr == "parent":
            temel = sabit_yol(d.value, gorulen)
            return str(Path(temel).parent) if temel is not None else None
        if isinstance(d, ast.BinOp) and isinstance(d.op, ast.Div):
            sol, sag = sabit_yol(d.left, gorulen), sabit_metin(d.right, gorulen)
            return os.path.normpath(str(Path(sol) / sag)) if sol is not None and sag is not None else None
        if isinstance(d, ast.Call):
            ad = d.func.id if isinstance(d.func, ast.Name) else (
                d.func.attr if isinstance(d.func, ast.Attribute) else "")
            if ad in ("str", "fspath") and len(d.args) == 1:
                return sabit_yol(d.args[0], gorulen)
            if isinstance(d.func, ast.Attribute) and ad in ("as_posix", "resolve", "absolute") and not d.args:
                return sabit_yol(d.func.value, gorulen)
            if ad in path_adlari:
                parcalar = [sabit_yol(a, gorulen) for a in d.args]
                if all(p is not None for p in parcalar):
                    return os.path.normpath(str(Path(*parcalar)))
            if isinstance(d.func, ast.Attribute) and ad in ("joinpath", "with_name", "with_suffix"):
                temel = sabit_yol(d.func.value, gorulen)
                parcalar = [sabit_metin(a, gorulen) for a in d.args]
                if temel is not None and parcalar and all(p is not None for p in parcalar):
                    try:
                        return os.path.normpath(str(getattr(Path(temel), ad)(*parcalar)))
                    except (ValueError, TypeError):
                        raise ValueError("koddaki dosya yolu çözümlenemiyor") from None
        return None

    def sabit_sayi(d, gorulen=()):
        if isinstance(d, ast.Constant) and type(d.value) in (int, float):
            return float(d.value)
        if isinstance(d, ast.UnaryOp) and isinstance(d.op, (ast.USub, ast.UAdd)):
            deger = sabit_sayi(d.operand, gorulen)
            return (-1 if isinstance(d.op, ast.USub) else 1) * deger if deger is not None else None
        if isinstance(d, ast.Name) and d.id in atamalar and d.id not in gorulen:
            degerler = {sabit_sayi(a, (*gorulen, d.id)) for a in atamalar[d.id]}
            return degerler.pop() if len(degerler) == 1 else None
        return None  # Bileşik eşik ifadeleri kaynakta bir sabit gibi onaylanmış sayılmaz.

    yollar = {os.path.normpath(y) for y in ({d["yol"] for d in dosyalar} | girdiler |
                                         {k["yol"] for k in depo_kaynaklari})}
    ebeveynler = {str(p) for yol in yollar for p in Path(yol).parents}
    for d in ast.walk(agac):
        if not isinstance(d, ast.Call):
            continue
        ad = d.func.id if isinstance(d.func, ast.Name) else (
            d.func.attr if isinstance(d.func, ast.Attribute) else "")
        yol = None
        hedef = None
        if isinstance(d.func, ast.Attribute) and ad in (
                "read_text", "read_bytes", "open", "is_file", "is_dir", "exists",
                "stat", "lstat", "iterdir", "glob", "rglob"):
            hedef = d.func.value
            yol = sabit_yol(hedef)
        if yol is None and d.args and (ad in ("isfile", "isdir", "exists", "stat", "lstat") or
                ad == "open" and (isinstance(d.func, ast.Name) or
                    isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name)
                    and d.func.value.id in ("io", "builtins"))):
            hedef = d.args[0]
            yol = sabit_yol(hedef)
        if yol is None and hedef is not None:
            for b in bagli(hedef):
                for a in ast.walk(b):
                    if isinstance(a, ast.Call) and (
                            isinstance(a.func, ast.Name) and a.func.id in path_adlari or
                            isinstance(a.func, ast.Attribute) and a.func.attr in path_adlari):
                        sabit = sabit_yol(a)
                        if sabit is not None and sabit not in yollar and sabit not in ebeveynler:
                            raise ValueError("koddaki sabit dosya yolu dönüşümü kapsam dışında: " + sabit)
        if yol is not None and yol not in yollar and not (
                ad in ("is_dir", "isdir", "exists") and yol in ebeveynler):
            raise ValueError("koddaki dosya kontrolü sözleşme/girdi kapsamı dışında: " + yol)
    metinler = [d.value for d in ast.walk(agac)
                if isinstance(d, ast.Constant) and isinstance(d.value, str)]
    if any(re.search(r"(?:geçmiş|gecmis|histor|ever).*(?:hiç|hic|asla|never|üzerine|uzerine)|"
                     r"(?:hiçbir zaman|hicbir zaman|never).*(?:üzerine|uzerine|overwrit)",
                     _metin(m)) for m in metinler):
        raise ValueError("gözlenemeyen tarihsel mutlak kanıt kehanette sınanamaz")
    # Her koşul, onu stdout'ta raporlayan kontrolün kendi dayanağıyla sınırlıdır.
    kontrol_dugumleri = {i["kontrol"]: set() for i in izler}
    for d in ast.walk(agac):
        if not isinstance(d, ast.Dict):
            continue
        alanlar = {k.value: v for k, v in zip(d.keys, d.values)
                   if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        ad = alanlar.get("ad")
        if isinstance(ad, ast.Constant) and ad.value in kontrol_dugumleri and "gecti" in alanlar:
            kontrol_dugumleri[ad.value].update(
                id(a) for b in bagli(alanlar["gecti"]) for a in ast.walk(b))

    def kaynak_metinleri(d):
        sahipler = [i["kaynak_metin"] for i in izler if id(d) in kontrol_dugumleri[i["kontrol"]]]
        if not sahipler:
            raise ValueError("koddaki eşitlik/tolerans kontrolü kendi kaynak maddesine bağlanamıyor")
        return sahipler

    def tolerans_sayilari(metin):
        metin = _metin(metin)
        sayi = r"(?<![\w.])([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)(?![\w.])"
        terim = r"\b(?:toleran\w*|yuvarla\w*|epsilon|round\w*|abs_tol|rel_tol)\b"
        birim = r"(?:(?:sn|saniye(?:lik)?|s|sec|seconds?)\s+)?"
        bag = r"(?:(?:değeri|degeri|sınırı|siniri|en fazla|en çok|azami|maksimum|maximum|is|of)\s+)?"
        # Açık sayı-terim bağı gerekir; aradaki başka alan/yüklemin sayısı ödünç alınmaz.
        kaliplar = (sayi + r"\s*" + birim + terim,
                    terim + r"\s*" + bag + r"(?:[:=]\s*)?" + sayi,
                    r"±\s*" + sayi)
        sonuc = set()
        for kalip in kaliplar:
            sonuc.update(float(es[1].replace(",", ".")) for es in re.finditer(kalip, metin))
        if len(sonuc) != 1:
            raise ValueError("kaynak tolerans değeri açık ve tek anlamlı değil")
        return sonuc

    for d in ast.walk(agac):
        if (isinstance(d, ast.Call) and
                (isinstance(d.func, ast.Name) and d.func.id in yakin_adlari or
                 isinstance(d.func, ast.Attribute) and d.func.attr == "isclose")):
            degerler = {k.arg: k.value for k in d.keywords if k.arg in ("rel_tol", "abs_tol")}
            for metin in kaynak_metinleri(d):
                if (not re.search(r"toleran|yuvarla|±|epsilon|round|abs_tol|rel_tol", _metin(metin)) or
                        set(degerler) != {"rel_tol", "abs_tol"}):
                    raise ValueError("sözleşme dışında veya örtük tolerans ölçütü")
                for deger in degerler.values():
                    sayi = sabit_sayi(deger)
                    # Sıfır kullanılmayan tolerans türünü kapatır; yeni bir eşik değildir.
                    if sayi is None or sayi not in tolerans_sayilari(metin) | {0.0}:
                        raise ValueError("sözleşme dışında sayısal tolerans ölçütü")
        if not isinstance(d, ast.Compare):
            continue
        if any(_sinir(b) for b in bagli(d)) and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in d.ops):
            if any(not re.search(r"eşit|esit|eşleş|esles|birebir|aynı|ayni|equal|exact|match|==", _metin(m))
                   for m in kaynak_metinleri(d)):
                raise ValueError("sözleşme dışında sınır eşitliği ölçütü")
        if any(_tolerans(b) for b in bagli(d)):
            for metin in kaynak_metinleri(d):
                if not re.search(r"toleran|yuvarla|±|epsilon|round", _metin(metin)):
                    raise ValueError("sözleşme dışında tolerans ölçütü")
                for esik in (d.left, *d.comparators):
                    olcum = any(
                        isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "abs"
                        and any(isinstance(k, ast.Subscript) or (
                            isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute) and k.func.attr == "get")
                            for arg in a.args for b in bagli(arg) for k in ast.walk(b))
                        for b in bagli(esik) for a in ast.walk(b))
                    if olcum:
                        continue
                    sayi = sabit_sayi(esik)
                    if sayi is None or sayi not in tolerans_sayilari(metin):
                        raise ValueError("sözleşme dışında sayısal tolerans ölçütü")
    return izler


def pozitif_denetle(sonuc, izler):
    """Atlanan, eksik veya beyan edilmemiş kontrol gerçek başarı değildir."""
    kontroller = sonuc.get("kontroller", [])
    adlar = [k["ad"] for k in kontroller]
    if not sonuc.get("gecti"):
        ayrinti = "\n".join(str(sonuc.get(k) or "") for k in
                            ("hata", "cikti_kuyrugu", "stderr_kuyrugu")) or "referans reddedildi"
        raise ValueError("pozitif kontrol doğru referansta başarısız: " + str(ayrinti)[-1200:])
    if (len(set(adlar)) != len(adlar) or set(adlar) != {i["kontrol"] for i in izler}
            or any(k.get("gecti") is not True or k.get("durum") not in (None, "gecti")
                   or re.search(r"\batlandı\b|\batlandi\b|\bskipped\b", _metin(k["ayrinti"]))
                   for k in kontroller)):
        raise ValueError("pozitif kontrolde atlanan/eksik/izlenemeyen kontrol var")
    return {"durum": "gecti", "kontroller": adlar, "kanit": [],
            "kehanet_sha256": sonuc["sha256"]}
