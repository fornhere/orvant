"""Kehanet iddialarının kabul ölçütüne ve kapı ortamına deterministik bağı (G-131/G-132).

Model "bağlıdır" dese de yetmez: kimlik görevde var mı, alıntı o kabulün metni mi,
kontrol adı betikte mi, betik sözleşmede olmayan tolerans/eşitlik ya da kapıda
gözlenemeyen kanıt istiyor mu — hepsi koddan okunur."""
import ast
from collections import Counter
import re
from pathlib import Path

ALINTI_EN_AZ = 8
TARIHSEL_GIT = frozenset(("log", "reflog", "rev-list", "blame", "whatchanged", "shortlog"))
SUBPROCESS_CAGRILARI = frozenset(("run", "Popen", "call", "check_call", "check_output"))
ESITLIK_SOZCUKLERI = re.compile(r"eşit|aynı|eşleş|uyuş|özdeş|\bsame\b|\bequal|\bmatch|identical")
ATLANDI = re.compile(r"\batland[ıi]\b|\batlan[ıi]yor\b|\bskipped\b|doğrulanamad[ıi]")
KAPI_IZLERI = ("kehanet yalnız salt okunur araç çağırabilir", "kehanet ağ veya sistem komutu kullanamaz",
               "kehanet dosya yazamaz", "kehanet dosya değiştiremez",
               "kehanet araç çıktısını dosyaya yazamaz")


def normal(metin):
    return re.sub(r"\s+", " ", str(metin).casefold().replace("i̇", "i")).strip()


def kabul_metinleri(veri):
    """Kimlik → bu kimliğe bağlı kabul metinleri (görev kabulü, sözleşme ölçütü, onaylı değişiklik)."""
    metinler = {}

    def ekle(kimlik, *parcalar):
        if isinstance(kimlik, str) and kimlik.strip():
            metinler.setdefault(kimlik, []).extend(
                normal(p) for p in parcalar if isinstance(p, str) and p.strip())

    for k in veri["gorev"].get("kabul", []):
        ekle(k.get("id"), k.get("beklenen"), k.get("rubrik"))
        ekle(k.get("sozlesme_kabul_id"), k.get("beklenen"), k.get("rubrik"))
    for k in veri.get("kabul_olcutleri", []):
        ekle(k.get("id"), k.get("metin"), k.get("kehanet"), k.get("rubrik"))
    for d in veri.get("kullanici_onayli_kabul_degisiklikleri", []):
        ekle(d.get("kabul_id"), (d.get("yeni") or {}).get("beklenen"),
             (d.get("yeni") or {}).get("rubrik"))
    return {k: v for k, v in metinler.items() if v}


def _sabit_metinler(agac):
    """Betiğin bildirdiği kontrol adları ({'ad': ...}); bildirim yoksa bütün metin sabitleri."""
    adlar = {d.values[i].value for d in ast.walk(agac) if isinstance(d, ast.Dict)
             for i, k in enumerate(d.keys) if isinstance(k, ast.Constant) and k.value == "ad"
             and isinstance(d.values[i], ast.Constant) and isinstance(d.values[i].value, str)}
    return adlar or {d.value for d in ast.walk(agac) if isinstance(d, ast.Constant) and isinstance(d.value, str)}


def iddialari_denetle(iddialar, betik, veri):
    """Bağlanamayan iddia → ValueError. Kabul metni hiçbir yerde değiştirilmez."""
    metinler = kabul_metinleri(veri)
    if not metinler:
        raise ValueError("görevde iddiaların bağlanacağı kabul ölçütü yok")
    if not iddialar:
        raise ValueError("kehanet iddiaları boş; her kontrol bir kabul kimliğine bağlanmalı "
                         "(geçerli kimlikler: " + ", ".join(sorted(metinler)) + ")")
    sabitler = _sabit_metinler(ast.parse(betik))
    hatalar, gorulen = [], set()
    for iddia in iddialar:
        kimlik = iddia["kimlik"]
        if kimlik in gorulen:
            hatalar.append(f"iddia {kimlik}: kimlik tekrarlı")
        gorulen.add(kimlik)
        kabul = iddia["kabul_kimligi"]
        if kabul not in metinler:
            hatalar.append(f"iddia {kimlik}: kabul kimliği '{kabul}' görevin kabul ölçütlerinde yok "
                           "(geçerli: " + ", ".join(sorted(metinler)) + ")")
            continue
        alinti = normal(iddia["kabul_alintisi"])
        if len(alinti) < min(ALINTI_EN_AZ, *(len(m) for m in metinler[kabul])) or not any(
                alinti in m for m in metinler[kabul]):
            hatalar.append(f"iddia {kimlik}: kabul_alintisi '{iddia['kabul_alintisi'][:80]}' "
                           f"{kabul} kabul metninin bire bir parçası değil")
        if iddia["kontrol"] not in sabitler:
            hatalar.append(f"iddia {kimlik}: kontrol adı '{iddia['kontrol']}' betikte yok")
    if hatalar:
        raise ValueError("izlenebilirlik: " + "; ".join(hatalar[:8]))


def _sayi(d):
    if isinstance(d, ast.Constant) and type(d.value) in (int, float):
        return d.value
    if isinstance(d, ast.UnaryOp) and isinstance(d.op, (ast.USub, ast.UAdd)):
        deger = _sayi(d.operand)
        return None if deger is None else (-deger if isinstance(d.op, ast.USub) else deger)
    return None


def _cagri_adi(d):
    if isinstance(d, ast.Call):
        return d.func.id if isinstance(d.func, ast.Name) else (
            d.func.attr if isinstance(d.func, ast.Attribute) else "")
    return ""


def _sayi_metinleri(deger):
    deger = abs(deger)
    yazimlar = {repr(deger), f"{deger:g}"}
    yazimlar.update(y.replace(".", ",") for y in list(yazimlar))
    if isinstance(deger, float) and 0 < deger < 1:
        yazimlar.update(y[1:] for y in list(yazimlar) if y.startswith("0"))
    return yazimlar


def _alintida_sayi(deger, alintilar, olcek=1, yuzde=True):
    """Sayı alıntıda sözcük sınırlı geçiyor mu (G-121: "2023" 3'ü, "1.62x" 0.62'yi muaf tutmaz).
    olcek=100: `oran*100 >= 62` eşiği alıntıdaki 0.62 ile de izlenir; oran eşiği "%62"/"yüzde 62" ile.
    yuzde=False: yüzde yazımı izlenmez (G-152: tolerans muafiyeti yalnız sayının kendisiyle)."""
    def ara(yazimlar, on=r"(?<![\w.,])", son=r"(?![\w]|[.,]\d)"):
        # Sondaki sıfırlar aynı sayıdır: "0.60" 0.6'yı, "3.0" 3'ü izler (w67 gorev-2).
        desen = "|".join(re.escape(y) + ("0*" if any(c in y for c in ".,") else r"(?:[.,]0+)?")
                         for y in sorted(yazimlar, key=len, reverse=True))
        return re.search(f"{on}(?:{desen}){son}", alintilar) is not None

    yazimlar = set(_sayi_metinleri(deger))
    if olcek == 100:
        yazimlar |= _sayi_metinleri(deger / 100)
    if ara(yazimlar):
        return True
    if yuzde and olcek == 1 and 0 < abs(deger) < 1:
        yuzde = _sayi_metinleri(round(abs(deger) * 100, 6))
        return ara(yuzde, on=r"(?:%\s*|yüzde\s+)") or ara(yuzde, son=r"\s*%")
    return False


def _alan_anahtari(d):
    """d['a']['b'], d.get('b'), float(...)/round(...) sarmalı içindeki son alan adı."""
    while (isinstance(d, ast.Call) and _cagri_adi(d) in ("float", "int", "round") and d.args):
        d = d.args[0]
    if isinstance(d, ast.Subscript) and isinstance(d.slice, ast.Constant) and isinstance(d.slice.value, str):
        return d.slice.value
    if (isinstance(d, ast.Call) and _cagri_adi(d) == "get" and isinstance(d.func, ast.Attribute)
            and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)):
        return d.args[0].value
    return None


def ek_olcutler(betik, iddialar, sozlesme):
    """Sözleşmede yazmayan iki çıktı alanı eşitliği; toleranslı/yuvarlamalı biçimi de (G-132).

    Çıktı alanını bağımsız ölçümle (ör. ffprobe süresi) toleransla uzlaştırmak ek ölçüt
    değildir: tam eşitlik dayatmak doğru işi reddeder. Yalnız iki ÇIKTI değerinin birbirine
    SAYISAL eşitliği, kabul metni bunu (eşitlik sözcüğü ya da tolerans sayısıyla) söylemiyorsa ek ölçüttür."""
    agac = ast.parse(betik)
    alintilar = " | ".join(normal(i["kabul_alintisi"]) for i in iddialar)
    # Yol/SHA gibi kimlik alanlarının dosyalar arası eşitliği bütünlük kontrolüdür; ek ölçüt sayısal alanda.
    cikti_alanlari = {a["ad"].split(".")[-1].replace("[]", "") for d in (sozlesme or {}).get("dosyalar", [])
                      for a in d.get("alanlar", []) if a.get("tip") in ("number", "integer")}
    adli_sayilar = {}
    for d in ast.walk(agac):
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            argumanlar = d.args.args[len(d.args.args) - len(d.args.defaults):]
            for arg, varsayilan in zip(argumanlar, d.args.defaults):
                if _sayi(varsayilan) is not None:
                    adli_sayilar[arg.arg] = _sayi(varsayilan)
        elif isinstance(d, ast.Assign) and _sayi(d.value) is not None:
            for hedef in d.targets:
                if isinstance(hedef, ast.Name):
                    adli_sayilar[hedef.id] = _sayi(d.value)

    def sayi(t):
        deger = _sayi(t)
        if deger is None and isinstance(t, ast.UnaryOp) and isinstance(t.op, (ast.USub, ast.UAdd)):
            # G-146: -T (adlı sınırın negatifi) da sayıdır.
            ic = sayi(t.operand)
            return None if ic is None else (-ic if isinstance(t.op, ast.USub) else ic)
        return adli_sayilar.get(t.id) if deger is None and isinstance(t, ast.Name) else deger

    def cikti_cifti(sol, sag):
        # Aynı yaprak adlı iki ayrı çıktı (T14: aday['bas'] ile olay['bas']) da eşitliktir;
        # yalnız aynı ifadenin kendisiyle karşılaştırılması değildir.
        if ast.dump(sol) == ast.dump(sag):
            return None
        sol_ad, sag_ad = _alan_anahtari(sol), _alan_anahtari(sag)
        if sol_ad in cikti_alanlari and sag_ad in cikti_alanlari:
            return (ast.unparse(sol), ast.unparse(sag)) if sol_ad == sag_ad else (sol_ad, sag_ad)
        return None

    # G-132 kalanı: ara değişkene atanmış fark (fark = a - b; abs(fark) <= t) da aynı eşitliktir.
    # Yalnız tek kez bağlanan ad çözülür; yeniden atanan ya da parametre olarak da geçen ad belirsizdir.
    baglamalar = Counter(d.id for d in ast.walk(agac) if isinstance(d, ast.Name) and isinstance(d.ctx, ast.Store))
    baglamalar.update(a.arg for d in ast.walk(agac) if isinstance(d, ast.arguments)
                      for a in d.posonlyargs + d.args + d.kwonlyargs + [d.vararg, d.kwarg] if a)
    farklar = {d.targets[0].id: d.value for d in ast.walk(agac)
               if isinstance(d, ast.Assign) and len(d.targets) == 1 and isinstance(d.targets[0], ast.Name)
               and baglamalar[d.targets[0].id] == 1
               and isinstance(d.value, ast.BinOp) and isinstance(d.value.op, ast.Sub)}

    def fark_cifti(t):
        t = farklar.get(t.id, t) if isinstance(t, ast.Name) else t
        if isinstance(t, ast.BinOp) and isinstance(t.op, ast.Sub):
            return cikti_cifti(t.left, t.right)
        return None

    kucuk, buyuk = (ast.Lt, ast.LtE), (ast.Gt, ast.GtE)
    esitlikler = {}  # "a == b" → tolerans (None: tam eşitlik)
    for d in ast.walk(agac):
        if _cagri_adi(d) == "isclose" and len(d.args) >= 2 and (cift := cikti_cifti(*d.args[:2])):
            tol = next((sayi(k.value) for k in d.keywords if k.arg in ("rel_tol", "abs_tol")), None)
            esitlikler[f"{cift[0]} ≈ {cift[1]}"] = tol
        elif isinstance(d, ast.Compare) and len(d.ops) == 1:
            sol, sag = d.left, d.comparators[0]
            if isinstance(d.ops[0], (ast.Eq, ast.NotEq)) and (cift := cikti_cifti(sol, sag)):
                esitlikler[f"{cift[0]} == {cift[1]}"] = None
            elif isinstance(d.ops[0], kucuk + buyuk):
                fark, sinir = (sol, sag) if isinstance(d.ops[0], kucuk) else (sag, sol)
                if _cagri_adi(fark) == "abs" and fark.args and (cift := fark_cifti(fark.args[0])):
                    esitlikler[f"{cift[0]} ≈ {cift[1]}"] = sayi(sinir)
        elif isinstance(d, ast.Compare):
            terimler = [d.left, *d.comparators]
            # Zincir eşitlik: a == b == c içindeki her ardışık çift ayrı karşılaştırmadır.
            for i, islec in enumerate(d.ops):
                if isinstance(islec, (ast.Eq, ast.NotEq)) and (cift := cikti_cifti(terimler[i], terimler[i + 1])):
                    esitlikler[f"{cift[0]} == {cift[1]}"] = None
            # İki taraflı aralık: lo <= a - b <= hi yalnız sıfırı iki yandan kapsıyorsa eşitliktir;
            # 0 <= bitis - bas <= sure sıralama/süre sınırıdır, eşitlik değil.
            if (len(d.ops) == 2 and (all(isinstance(o, kucuk) for o in d.ops) or all(isinstance(o, buyuk) for o in d.ops))
                    and (cift := fark_cifti(terimler[1]))):
                sinirlar = [sayi(terimler[0]), sayi(terimler[2])]
                # G-146: dar taraf geniş tarafın %10'undan küçükse titreşim paylı sıralama/süre sınırıdır
                # (-0.5 <= bitis - bas <= 60), eşitlik değil.
                if (None not in sinirlar and min(sinirlar) < 0 < max(sinirlar)
                        and min(map(abs, sinirlar)) >= 0.1 * max(map(abs, sinirlar))):
                    esitlikler[f"{cift[0]} ≈ {cift[1]}"] = max(abs(x) for x in sinirlar)
    hatalar = []
    for ad, tol in sorted(esitlikler.items()):
        if ESITLIK_SOZCUKLERI.search(alintilar):
            continue
        if tol is not None and _alintida_sayi(tol, alintilar, yuzde=False):
            continue
        hatalar.append("sözleşmede olmayan çıktı alanı eşitliği: " + ad +
                       (f" (tolerans {tol:g})" if tol is not None else ""))
    return hatalar


BENZERLIK_ORANLARI = ("ratio", "quick_ratio", "real_quick_ratio")


def gizli_esikler(betik, iddialar):
    """Sözleşmede yazmayan metin benzerliği eşiği (G-121): T13-4 kehaneti `ratio() >= 0.62` ve
    `len(set(x) & set(y)) >= 3` ile tek bir algoritmayı doğru saydı; farklı geçerli yöntem reddedilir.
    Yalnız benzerlik oranı, küme kesişimi büyüklüğü ve get_close_matches cutoff'u; genel sayı yasağı değildir."""
    agac = ast.parse(betik)
    alintilar = " | ".join(normal(i["kabul_alintisi"]) for i in iddialar)
    islevler = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

    def oran_mi(t):
        return isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute) and t.func.attr in BENZERLIK_ORANLARI

    def dis_parcalar(f):
        """Fonksiyonun dış kapsamda değerlendirilen parçaları: dekoratör, varsayılan, açıklamalar (w75 RET-2)."""
        a = f.args
        argumanlar = a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
        return [*getattr(f, "decorator_list", []), *a.defaults, *(v for v in a.kw_defaults if v),
                *(x.annotation for x in argumanlar if x and x.annotation), *([f.returns] if getattr(f, "returns", None) else [])]

    def kendi_dugumleri(ust):
        # İç içe fonksiyon/lambda gövdesine inmeden yalnız bu kapsamın düğümleri; fonksiyonun kendi kapsamı
        # yalnız argüman adları ve gövdedir.
        if isinstance(ust, islevler):
            yield ust.args
            bekleyen = list(ust.body) if isinstance(ust.body, list) else [ust.body]
        else:
            bekleyen = list(ast.iter_child_nodes(ust))
        while bekleyen:
            d = bekleyen.pop()
            yield d
            if isinstance(d, islevler):
                bekleyen.extend(dis_parcalar(d))
            else:
                bekleyen.extend(ast.iter_child_nodes(d))

    # G-155: ad bağlamaları kapsam başınadır; kardeş fonksiyondaki `esik = 5` buradaki eşiğe aday olmaz.
    # Kapsam başına: bağlama sayısı, (konum, değer) atamaları, benzerlik oranı içerip içermediği.
    dugum_kapsami, ust_kapsam, baglamalar, atamalar, oranli = {}, {}, {}, {}, {}
    oznitelikler, kapsam_dugumleri, globaller, yerel_olmayanlar = {}, {}, {}, {}
    varsayilan_kapsami = {}  # varsayılan ifade → tanımın yapıldığı (dış) kapsam (w77)
    kapsamlar = [agac, *(d for d in ast.walk(agac) if isinstance(d, islevler))]
    for ust in kapsamlar:
        baglamalar[id(ust)], atamalar[id(ust)] = Counter(), {}
        kapsam_dugumleri[id(ust)] = dugumler = list(kendi_dugumleri(ust))
        oranli[id(ust)] = any(oran_mi(d) for d in dugumler)
        globaller[id(ust)] = {a for d in dugumler if isinstance(d, ast.Global) for a in d.names}
        yerel_olmayanlar[id(ust)] = {a for d in dugumler if isinstance(d, ast.Nonlocal) for a in d.names}
        for d in dugumler:
            dugum_kapsami[id(d)] = ust
            if isinstance(d, islevler):
                ust_kapsam[id(d)] = ust

    def sahip(ad, kapsam):
        """Bağlamanın yazıldığı kapsam: `global` modüle, `nonlocal` dıştaki fonksiyona gider (w75 RET-1)."""
        while kapsam is not agac:
            if ad in globaller[id(kapsam)]:
                return agac
            if ad not in yerel_olmayanlar[id(kapsam)]:
                return kapsam
            kapsam = ust_kapsam[id(kapsam)]
        return agac

    for ust in kapsamlar:
        for d in kapsam_dugumleri[id(ust)]:
            if isinstance(d, ast.Name) and isinstance(d.ctx, ast.Store):
                baglamalar[id(sahip(d.id, ust))][d.id] += 1
            elif isinstance(d, ast.arguments):
                baglamalar[id(ust)].update(a.arg for a in d.posonlyargs + d.args + d.kwonlyargs
                                           + [d.vararg, d.kwarg] if a)
                # w75 RET-3: varsayılanlı parametre, değeri dış kapsamda çözülen bir atamadır (`ESIK=ESIK`).
                konumsal = d.posonlyargs + d.args
                for arg, varsayilan in [*zip(konumsal[len(konumsal) - len(d.defaults):], d.defaults),
                                        *zip(d.kwonlyargs, d.kw_defaults)]:
                    if varsayilan is not None:
                        varsayilan_kapsami[id(varsayilan)] = ust_kapsam[id(ust)]
                        atamalar[id(ust)].setdefault(arg.arg, []).append(((arg.lineno, arg.col_offset), varsayilan))
            konum = (getattr(d, "lineno", 0), getattr(d, "col_offset", 0))
            # Tek hedefli atama, değerli tip açıklamalı atama ve eşit uzunluklu demet ataması çözülür.
            ciftler = []
            if isinstance(d, ast.Assign) and len(d.targets) == 1:
                hedef = d.targets[0]
                if isinstance(hedef, (ast.Name, ast.Attribute)):
                    ciftler = [(hedef, d.value)]
                elif (isinstance(hedef, (ast.Tuple, ast.List)) and isinstance(d.value, (ast.Tuple, ast.List))
                      and len(hedef.elts) == len(d.value.elts)
                      and not any(isinstance(e, ast.Starred) for e in hedef.elts + d.value.elts)):
                    ciftler = list(zip(hedef.elts, d.value.elts))
            elif isinstance(d, ast.AnnAssign) and d.value is not None:
                ciftler = [(d.target, d.value)]
            elif isinstance(d, (ast.AugAssign, ast.AnnAssign)) and isinstance(d.target, ast.Attribute):
                oznitelikler.setdefault(d.target.attr, []).append(None)  # sabit sayılmaz
            for hedef, deger in ciftler:
                if isinstance(hedef, ast.Name):
                    atamalar[id(sahip(hedef.id, ust))].setdefault(hedef.id, []).append((konum, deger))
                elif isinstance(hedef, ast.Attribute):
                    oznitelikler.setdefault(hedef.attr, []).append(deger)
            if isinstance(d, ast.ClassDef):
                # Sınıf gövdesindeki `ESIK = 0.62`, `cfg.ESIK` / `self.ESIK` ile okunur.
                for ic in d.body:
                    for hedef in (ic.targets if isinstance(ic, ast.Assign) else
                                  [ic.target] if isinstance(ic, (ast.AugAssign, ast.AnnAssign)) else []):
                        if isinstance(hedef, ast.Name):
                            oznitelikler.setdefault(hedef.id, []).append(
                                ic.value if isinstance(ic, ast.Assign) and len(ic.targets) == 1 else None)

    def cozum(ad, kapsam):
        """Adı bağlayan en içteki kapsam (kendi kapsamı, sonra dıştakiler; `global`/`nonlocal` bildirimi gözetilir)."""
        while kapsam is not None:
            if ad in globaller[id(kapsam)]:
                return agac if baglamalar[id(agac)][ad] else None
            if ad not in yerel_olmayanlar[id(kapsam)] and baglamalar[id(kapsam)][ad]:
                return kapsam
            kapsam = ust_kapsam.get(id(kapsam))
        return None

    def ad_atamalari(ad, kapsam):
        """Ad bütün bağlamaları atama ise atamaları; döngü hedefi, `+=` vb. varsa None (çözülmez)."""
        if (k := cozum(ad, kapsam)) is None:
            return None, None
        liste = atamalar[id(k)].get(ad, [])
        return (k, liste) if len(liste) == baglamalar[id(k)][ad] else (k, None)

    def sayilar(t, kapsam):
        """Eşiğin olası değerleri; biri bile sabit sayı değilse çözülmez (boş liste)."""
        if (deger := _sayi(t)) is not None:
            return [deger]
        if isinstance(t, ast.UnaryOp) and isinstance(t.op, (ast.USub, ast.UAdd)):
            return [-v if isinstance(t.op, ast.USub) else v for v in sayilar(t.operand, kapsam)]
        if (isinstance(t, ast.Call) and _cagri_adi(t) == "round" and 1 <= len(t.args) <= 2
                and not t.keywords):
            basamak = 0 if len(t.args) == 1 else _sayi(t.args[1])
            if type(basamak) is int:
                return [round(v, basamak) for v in sayilar(t.args[0], kapsam)]
        if isinstance(t, ast.BinOp) and isinstance(t.op, ast.Mult):
            sol, sag = sayilar(t.left, kapsam), sayilar(t.right, kapsam)
            if sol and sag:
                return list(dict.fromkeys(a * b for a in sol for b in sag))
        adaylar = (oznitelikler.get(t.attr) if isinstance(t, ast.Attribute) else None) or []
        if isinstance(t, ast.Name):
            adaylar = [v for _, v in (ad_atamalari(t.id, kapsam)[1] or [])]
        degerler = []
        for v in adaylar:
            if v is not None and id(v) in varsayilan_kapsami:
                degerler.extend(sayilar(v, varsayilan_kapsami[id(v)]) or [None])
            else:
                degerler.append(None if v is None else _sayi(v))
        return [] if None in degerler else list(dict.fromkeys(degerler))

    def kume_boyu(t, islec, yontem):
        """len(a & b) / len(a.intersection(b)) (ya da | / union)."""
        if not (isinstance(t, ast.Call) and _cagri_adi(t) == "len" and len(t.args) == 1):
            return False
        ic = t.args[0]
        return ((isinstance(ic, ast.BinOp) and isinstance(ic.op, islec))
                or (isinstance(ic, ast.Call) and isinstance(ic.func, ast.Attribute) and ic.func.attr == yontem))

    ada_onbellek, olcek_onbellek = {}, {}

    def ada_bagli(t, kapsam, oranli, konum, goruldu):
        """Aynı ad zincirini kök ifade ve ölçek aramaları arasında yeniden çözme (G-158)."""
        anahtar = (id(t), id(kapsam), oranli, konum, goruldu)
        if anahtar not in ada_onbellek:
            ada_onbellek[anahtar] = _ada_bagli(t, kapsam, oranli, konum, goruldu)
        return ada_onbellek[anahtar]

    def _ada_bagli(t, kapsam, oranli, konum, goruldu):
        """Adın benzerlik ifadesi olan bağlaması ve ölçeği. Aynı kapsamda kullanımdan önceki en son benzerlik
        bağlaması (G-155: `o = o * 100` ölçeği değiştirir); önceki yoksa ilk benzerlik bağlaması."""
        if (t.id, konum) in goruldu or (k := cozum(t.id, kapsam)) is None:
            return None
        goruldu = (*goruldu, (t.id, konum))
        liste = sorted(atamalar[id(k)].get(t.id, []), key=lambda a: a[0])  # kaynak sırası
        sirali = [*reversed([a for a in liste if a[0] < konum])] if k is kapsam and konum else []
        for yer, deger in sirali + [a for a in liste if a not in sirali]:
            # Varsayılan argüman tanımın yapıldığı kapsamda, tanım konumunda değerlendirilir.
            dk, dy = ((varsayilan_kapsami[id(deger)], (deger.lineno, deger.col_offset))
                      if id(deger) in varsayilan_kapsami else (k, yer))
            if (o := olcek(deger, dk, oranli, dy, goruldu)) is not None:
                # Bulgu kök benzerlik ifadesini adlandırır (`o = round(o, 2)` → sm.ratio()); ölçek bu bağlamadan.
                kok = next((b[0] for ic in ast.walk(deger) if isinstance(ic, ast.Name)
                            and (b := ada_bagli(ic, dk, oranli, dy, goruldu)) is not None), deger)
                return kok, o
        return None

    def olcek(t, kapsam, oranli, konum=None, goruldu=()):
        anahtar = (id(t), id(kapsam), oranli, konum, goruldu)
        if anahtar not in olcek_onbellek:
            olcek_onbellek[anahtar] = _olcek(t, kapsam, oranli, konum, goruldu)
        return olcek_onbellek[anahtar]

    def _olcek(t, kapsam, oranli, konum=None, goruldu=()):
        """Benzerlik ifadesinin ölçeği (oran 1, yüzde 100); benzerlik değilse None."""
        if isinstance(t, ast.Name):
            bulgu = ada_bagli(t, kapsam, oranli, konum, goruldu)
            return None if bulgu is None else bulgu[1]
        if oran_mi(t):
            return 1
        if isinstance(t, ast.Call) and _cagri_adi(t) in ("round", "float", "int", "floor", "ceil", "trunc") and t.args:
            return olcek(t.args[0], kapsam, oranli, konum, goruldu)
        if isinstance(t, ast.BinOp) and isinstance(t.op, ast.Mult):
            for ic, carpan in ((t.left, t.right), (t.right, t.left)):
                if _sayi(carpan) == 100 and olcek(ic, kapsam, oranli, konum, goruldu) == 1:
                    return 100
        if (isinstance(t, ast.BinOp) and isinstance(t.op, ast.Div)
                and kume_boyu(t.left, ast.BitAnd, "intersection") and kume_boyu(t.right, ast.BitOr, "union")):
            return 1  # Jaccard: kapsamda oran olmasa da metin benzerliği ölçütüdür
        if oranli and kume_boyu(t, ast.BitAnd, "intersection"):
            return 1
        return None

    def benzerlik(t, kapsam, konum):
        # Kesişim büyüklüğü tek başına yapısal bağdır (T14: aday en az 2 konuşma bölümüne bağlı); yalnız aynı
        # fonksiyonda benzerlik oranıyla birlikte metin benzerliği ölçütüdür (T13 tekrar()).
        if isinstance(t, ast.Name):
            return ada_bagli(t, kapsam, oranli[id(kapsam)], konum, ())
        if (o := olcek(t, kapsam, oranli[id(kapsam)], konum)) is None:
            return None
        return t, o

    bulunan = {}
    for d in ast.walk(agac):
        if isinstance(d, ast.Compare):
            kapsam, konum = dugum_kapsami[id(d)], (d.lineno, d.col_offset)
            terimler = [d.left, *d.comparators]
            for i, islec in enumerate(d.ops):
                if not isinstance(islec, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    continue
                for oran, sinir in ((terimler[i], terimler[i + 1]), (terimler[i + 1], terimler[i])):
                    if ((bulgu := benzerlik(oran, kapsam, konum)) is not None
                            and (esikler := sayilar(sinir, kapsam))):
                        bulunan.setdefault(ast.unparse(bulgu[0]), (esikler, bulgu[1]))
        elif _cagri_adi(d) == "get_close_matches":
            cutoff = next((k.value for k in d.keywords if k.arg == "cutoff"), None)
            if cutoff is None and len(d.args) >= 4:
                cutoff = d.args[3]
            # cutoff verilmezse difflib varsayılanı 0.6 da gizli eşiktir.
            if esikler := [0.6] if cutoff is None else sayilar(cutoff, dugum_kapsami[id(d)]):
                bulunan.setdefault(ast.unparse(d), (esikler, 1))
    return [f"sözleşmede olmayan gizli benzerlik eşiği: {ifade} (eşik {esik:g})"
            for ifade, (esikler, carpan) in sorted(bulunan.items()) for esik in esikler
            if not _alintida_sayi(esik, alintilar, carpan)]


def _basari_cikisi(ifade):
    """raise SystemExit() / SystemExit(0) başarıyla çıkıştır, koşulsuz başarısızlık değil."""
    e = ifade.exc
    if isinstance(e, ast.Name):
        return e.id == "SystemExit"
    return (isinstance(e, ast.Call) and _cagri_adi(e) == "SystemExit"
            and (not e.args or (isinstance(e.args[0], ast.Constant) and e.args[0].value in (0, None))))


def kapida_gozlenemez(betik, izinli_araclar):
    """Kapı ortamında çalışamayan veya hiç geçemeyen kontrol (G-131)."""
    agac = ast.parse(betik)
    hatalar = []
    for d in ast.walk(agac):
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in SUBPROCESS_CAGRILARI and d.args
                and isinstance(d.args[0], (ast.List, ast.Tuple)) and d.args[0].elts):
            ilk, *kalan = d.args[0].elts
            if isinstance(ilk, ast.Constant) and isinstance(ilk.value, str):
                arac = Path(ilk.value).name
                if arac not in izinli_araclar:
                    hatalar.append(f"izinli olmayan komut: {arac}")
                elif arac == "git":
                    alt = next((k.value for k in kalan if isinstance(k, ast.Constant)
                                and isinstance(k.value, str) and not k.value.startswith("-")), None)
                    if alt in TARIHSEL_GIT:
                        hatalar.append(f"kapıda gözlenemeyen tarihsel kanıt: git {alt}")
            elif isinstance(ilk, ast.Attribute) and ilk.attr == "executable":
                hatalar.append("izinli olmayan komut: Python yorumlayıcısı")
        elif isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                d.args.args or d.args.posonlyargs or d.args.kwonlyargs or d.args.vararg or d.args.kwarg):
            for ifade in d.body:
                if isinstance(ifade, (ast.Expr)) and isinstance(ifade.value, ast.Constant):
                    continue
                if isinstance(ifade, ast.Raise) and not _basari_cikisi(ifade):
                    hatalar.append(f"koşulsuz başarısız kontrol (hiç geçemez): {d.name}")
                break
    return list(dict.fromkeys(hatalar))


def uretim_denetimi(betik, iddialar, sozlesme, veri, izinli_araclar):
    """Üretimin her turunda deterministik denetim; tutmazsa ValueError."""
    iddialari_denetle(iddialar, betik, veri)
    hatalar = (kapida_gozlenemez(betik, izinli_araclar) + ek_olcutler(betik, iddialar, sozlesme)
               + gizli_esikler(betik, iddialar))
    if hatalar:
        raise ValueError("; ".join(hatalar))


def calisma_denetimi(sonuc, iddialar):
    """Doğru referans üzerindeki koşuda kapı kısıtı ihlali ve atlanan kontroller (başarı sayılmaz)."""
    metin = "\n".join(str(s or "") for s in (sonuc.get("hata"), sonuc.get("stderr_kuyrugu"),
                                             sonuc.get("cikti_kuyrugu")))
    hatalar = [f"kapı kısıtı: {iz}" for iz in KAPI_IZLERI if iz in metin]
    kontroller = {k["ad"]: k for k in sonuc.get("kontroller") or []}
    for iddia in iddialar or []:
        if iddia["kontrol"] not in kontroller:
            hatalar.append(f"atlanan kontrol: {iddia['kontrol']} referans koşusunda çalışmadı")
    for ad, k in kontroller.items():
        if k.get("gecti") is True and ATLANDI.search(normal(k.get("ayrinti", ""))):
            hatalar.append(f"atlanan kontrol başarı sayılmaz: {ad}")
    return list(dict.fromkeys(hatalar))
