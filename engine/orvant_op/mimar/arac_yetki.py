"""Envanter ipuçlarından görev yetki isteği üretir; onay vermez, dosya yazmaz."""
import copy
import os
import re
from pathlib import Path

try:
    import pwd
except ImportError:  # Windows gibi pwd modülü olmayan sistemler.
    pwd = None

from .envanter import gereksinim_cikar


GENEL_KELIMELER = frozenset({
    "video", "audio", "ses", "dosya", "yerel", "model", "python", "skill", "beceri",
    "arac", "araç", "komut", "çıktı", "girdi", "kaynak", "gerçek", "olarak", "için",
    "veya", "içerik", "üretmek", "oluştur", "kontrol", "test", "json", "script", "betik",
    "yoksa", "olan", "bildirmek", "eksiklik", "erişim", "kullan", "çalıştır",
})
ATLANAN_DURUMLAR = frozenset({"kabul", "kosuyor", "inceleme_bekliyor", "ret"})


def _ev_koku(ev=None):
    if ev is not None:
        return Path(ev)
    # HOME farklıysa mutlak envanter yoluyla aynı gerçek kullanıcı evini kullan.
    if pwd is not None and hasattr(os, "getuid"):
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError:
            pass  # Hesap kaydı yoksa platformun ev dizini çözümüne dön.
    return Path.home()


def gereksinimler(kayit):
    """Eski envanter kayıtlarını da aynı kurallarla yorumlar."""
    return kayit["gereksinimler"] if "gereksinimler" in kayit else gereksinim_cikar(kayit)


def _kelimeler(metin):
    return sorted({k for k in re.findall(r"[^\W_]{5,}", metin.casefold())
                   if not any(k.startswith(g) for g in GENEL_KELIMELER)})


def _ortaklar(sol, sag):
    # Aynı kökten türeyen çoğullar bağımsız iki içerik kanıtı sayılmaz.
    ortak = []
    for sol_kelime in sol:
        for sag_kelime in sag:
            # çekimi/çekimleri: çoğul eki kalkınca yine önek karşılaştırılır.
            a = re.sub(r"(?:lar|ler)([ıiuü]?)$", r"\1", sol_kelime)
            b = re.sub(r"(?:lar|ler)([ıiuü]?)$", r"\1", sag_kelime)
            if min(len(a), len(b)) < 5:
                continue
            if a.startswith(b) or b.startswith(a):
                kok = min((a, b), key=len)
                if not any(kok.startswith(k) or k.startswith(kok) for k in ortak):
                    ortak.append(kok)
    return sorted(ortak)


def _gorev_metni(gorev):
    alanlar = [gorev.get("baslik"), gorev.get("amac"), *gorev.get("yazilabilir", [])]
    for kabul in gorev.get("kabul", []):
        alanlar.extend(kabul.get(k) for k in ("komut", "beklenen", "rubrik"))
    return " ".join(str(a) for a in alanlar if a).casefold()


def eslesmeler(gorev, kayitlar):
    """Kayıt sırasını koruyan bağımsız ve muhafazakâr metin eşleştirici."""
    metin = _gorev_metni(gorev)
    kelimeler = _kelimeler(metin)
    sonuc = []
    for kayit in kayitlar:
        if kayit.get("mevcut") is False:
            continue
        g = gereksinimler(kayit)
        if not g["yollar"] and g["ag"] == "gerekmez" and g["gpu"] == "gerekmez":
            continue
        ad = kayit.get("ad", "").casefold()
        if not ad:
            continue
        guc, kanit = None, []
        if kayit.get("tur") == "arac":
            desen = r"uv(?:\s+run|x)?" if ad == "uv" else re.escape(ad)
            es = re.search(r"(?<![\w-])(?:" + desen + r")(?![\w-])", metin)
            if es:
                guc, kanit = "kuvvetli", [es.group()]
        elif kayit.get("tur") == "skill":
            if ad in metin:
                guc, kanit = "kuvvetli", [ad]
            else:
                parcalar = [p for p in re.split(r"[-_./]", ad) if p in _kelimeler(p)]
                ad_es = sorted({p for p in parcalar if any(k.startswith(p) for k in kelimeler)})
                ortak = _ortaklar(_kelimeler(kayit.get("aciklama") or ""), kelimeler)
                ek = [k for k in ortak if not any(k.startswith(p) or p.startswith(k) for p in ad_es)]
                if ad_es and ek:
                    guc, kanit = "kuvvetli", ad_es + ek
                else:
                    ortak = _ortaklar(_kelimeler((kayit.get("aciklama") or "") + " " +
                                               (kayit.get("komut_ornegi") or "")), kelimeler)
                    if len(ortak) >= 2:
                        guc, kanit = "zayif", ortak
        if guc:
            sonuc.append({"envanter_id": kayit["id"], "ad": kayit["ad"], "guc": guc,
                          "kanit": kanit})
    return sonuc


def _genislet(yol, ev):
    yol = yol.replace("$HOME/", "~/", 1)
    return Path(os.path.normpath(ev / yol[2:] if yol.startswith("~/") else ev if yol == "~" else yol))


def _anilan_yollar(metin, ev):
    # Sözcük sınırı /uv ile /uvx'i karıştırmaz; tırnaklı boşluklu yollar da korunur.
    desen = r'''["'`]((?:~/|\$HOME/|/)[^"'`]+)["'`]|(?<![\w/:])((?:~/|\$HOME/|/)[^\s,;:()\[\]{}<>"'`]+)'''
    return [_genislet(a or b.rstrip(".!?"), ev) for a, b in re.findall(desen, metin)]


def _yol_karsilandi(yol, anilanlar):
    # Y06 emsali: belirli model alt dizini, genel önbellek isteğini de karşılar.
    return any(yol == a or yol.is_relative_to(a) or a.is_relative_to(yol) for a in anilanlar)


def kapsanir_mi(istek, gereksinim, *, ev=None):
    """Y06 ile aynı yol kapsamı; ağ ancak açık bir ağ eylemiyle karşılanır."""
    if istek.get("durum") == "reddedildi":
        return False
    ev = _ev_koku(ev)
    anilanlar = _anilan_yollar(istek.get("ayrinti") or "", ev)
    return (all(_yol_karsilandi(_genislet(y["yol"], ev), anilanlar)
                for y in gereksinim.get("yollar", [])) and
            (gereksinim.get("ag") != "gerekir" or istek.get("eylem") in ("ag_erisimi", "indirme")))


def kapsam_eksigi(istekler, gereksinim, *, ev=None):
    istekler = list(istekler)
    return {"yollar": [y for y in gereksinim.get("yollar", []) if not any(
                kapsanir_mi(i, {"yollar": [y], "ag": "gerekmez"}, ev=ev) for i in istekler)],
            "ag": gereksinim.get("ag") == "gerekir" and not any(
                kapsanir_mi(i, {"yollar": [], "ag": "gerekir"}, ev=ev) for i in istekler)}


def istek_gereksinimi(istek, *, ev=None):
    ev = _ev_koku(ev)
    return {"yollar": [{"yol": str(y), "erisim": "okuma"}
                       for y in _anilan_yollar(istek.get("ayrinti") or "", ev)],
            "ag": "gerekir" if istek.get("eylem") in ("ag_erisimi", "indirme") else "gerekmez",
            "eylem": istek.get("eylem")}


def kapsayan_istek_ids(istekler, gereksinim, *, ev=None):
    """Birleşik kapsam tam ise yalnız katkı veren istekleri bildirir."""
    istekler = [i for i in istekler if i.get("durum") != "reddedildi"]
    if not gereksinim.get("yollar") and gereksinim.get("ag") != "gerekir":
        return [i["id"] for i in istekler if i.get("eylem") == gereksinim.get("eylem")]
    eksik = kapsam_eksigi(istekler, gereksinim, ev=ev)
    if eksik["yollar"] or eksik["ag"]:
        return []
    parcalar = [{"yollar": [y]} for y in gereksinim.get("yollar", [])]
    if gereksinim.get("ag") == "gerekir":
        parcalar.append({"ag": "gerekir"})
    return [i["id"] for i in istekler if any(kapsanir_mi(i, g, ev=ev) for g in parcalar)]


def _onerilen_yol(yol, ev):
    # durum -> planlayıcı -> arac_yetki bağımlılığına karşı durum import edilmez.
    # izin_yolu_dogrula'nın depo verilmemiş kuralı; sentetik ev açıkça taşınır.
    hedef, ev = yol.resolve(), ev.resolve()
    orvant = Path(__file__).resolve().parents[2]
    yasak = (ev / ".ssh", ev / ".gnupg", orvant)
    if (not yol.is_absolute() or not hedef.is_relative_to(ev) or hedef == ev or
            hedef == ev / ".config" or any(hedef.is_relative_to(k) for k in yasak)):
        return None
    return str(hedef)


def _yeni_id(istekler):
    ids = {i["id"] for i in istekler}
    sayilar = [es.group(1) for kimlik in ids if (es := re.fullmatch(r"Y(\d+)", kimlik))]
    sayi = max(map(int, sayilar), default=0) + 1
    genislik = max(map(len, sayilar), default=2)
    while (kimlik := f"Y{sayi:0{genislik}d}") in ids:
        sayi += 1
    return kimlik


def _ayrinti(gid, eslesme, yollar, g):
    alanlar = []
    for erisim in ("okuma", "yazma"):
        adlar = [y["yol"] for y in yollar if y["erisim"] == erisim]
        # Tırnak yalnız yolun boşluk taşıdığı durumda ayrıştırmayı korur.
        alanlar.append(erisim + ": " + (", ".join('"' + a + '"' if " " in a else a
                                                   for a in adlar) or "yok"))
    gpu = "gerekebilir (yoksa CPU)" if g["gpu"] == "gerekebilir" else "gerekmez"
    return (f"{gid} için {eslesme['ad']} ({eslesme['envanter_id']}) çalıştırma erişimi: "
            + "; ".join(alanlar) + f"; ağ: {g['ag']}; GPU: {gpu}.")


def yetki_denetimi(plan, envanter, *, gorev_ids=None, ev=None):
    """Plan kopyasına açık istek ekler; GPU/belirsiz ağ tek başına istek yaratmaz."""
    sonuc = {"plan": copy.deepcopy(plan), "eklenen": [], "uyarilar": []}
    if not envanter:
        return sonuc
    ev = _ev_koku(ev)
    kayitlar = envanter.get("kayitlar", [])
    kayit_by_id = {k["id"]: k for k in kayitlar}
    secilen = set(gorev_ids) if gorev_ids is not None else None
    istekler = sonuc["plan"].setdefault("yetki_istekleri", [])
    for gorev in sonuc["plan"].get("gorevler", []):
        gid = gorev["id"]
        if gorev.get("durum") in ATLANAN_DURUMLAR or (secilen is not None and gid not in secilen):
            continue
        for es in eslesmeler(gorev, kayitlar):
            g = gereksinimler(kayit_by_id[es["envanter_id"]])
            yollar = [dict(y, yol=str(_genislet(y["yol"], ev))) for y in g["yollar"]]
            if es["guc"] == "zayif":
                sonuc["uyarilar"].append({"gorev": gid, "envanter_id": es["envanter_id"],
                                          "guc": es["guc"], "neden": "yetki gerekebilir",
                                          "yollar": yollar, "ag": g["ag"], "gpu": g["gpu"]})
                continue
            mevcut = [i for i in istekler if i["id"] in gorev.get("yetki_istek_ids", [])
                      and i["durum"] != "reddedildi"]
            kalan = kapsam_eksigi(mevcut, dict(g, yollar=yollar), ev=ev)
            eksik, ag_eksik = kalan["yollar"], kalan["ag"]
            eylemler = (["kurulum"] if eksik else []) + (["ag_erisimi"] if ag_eksik else [])
            for eylem in eylemler:
                istek_yollari = eksik if eylem == "kurulum" else []
                istek = {"id": _yeni_id(istekler), "eylem": eylem,
                         "ayrinti": _ayrinti(gid, es, istek_yollari, g),
                         "gerekce": "Deterministik envanter denetimi; eşleşme kanıtı: "
                         + ", ".join(es["kanit"]) + "; gereksinim kanıtı: "
                         + (", ".join(g["kanit"]) or "kayıtta bildirilmiş gereksinimler")
                         + "; host gözlemi; işçi sandbox'ında bu yollar erişilemeyebilir.",
                         "durum": "acik", "onay_olay_id": None}
                istekler.append(istek)
                gorev.setdefault("yetki_istek_ids", []).append(istek["id"])
                oneriler = [_onerilen_yol(Path(y["yol"]), ev) for y in istek_yollari]
                sonuc["eklenen"].append({"istek": istek, "gorev": gid,
                                         "envanter_id": es["envanter_id"],
                                         "onerilen_yollar": list(dict.fromkeys(y for y in oneriler if y))})
    return sonuc


def envanter_ozeti(envanter, *, sinir=20):
    """Mevcut becerilere öncelik veren kısa planlayıcı girdisi."""
    kayitlar = [k for k in (envanter or {}).get("kayitlar", [])
                if k.get("mevcut") is not False and k.get("tur") in ("skill", "arac")]
    kayitlar.sort(key=lambda k: k["tur"] != "skill")
    return [{"id": k["id"], "ad": k["ad"], "aciklama": (k.get("aciklama") or "")[:200],
             "gereksinimler": copy.deepcopy(gereksinimler(k))} for k in kayitlar[:max(0, sinir)]]
