"""Karşılama kalıcılığı ve tek adımlı durum geçişleri."""
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import ayarlar, uyum, uyum_komut
from orvant_op.iz import kaydet
from orvant_op.yer_tutucu import denetle as yer_tutucu_denetle
from .roller import rol_cagir
from .soru_sec import sec
from .erteleme import cevaptan, kayitlar as erteleme_kayitlari
from .sozlesme import kur, kapi, sozlesme_hash, v03_spec
from .kaynaklar import acik_kaynaklar, guncel_gozlemler


def _zaman():
    return datetime.now(timezone.utc)


def _yaz_json(yol, veri):
    _yaz_metin(yol, json.dumps(veri, ensure_ascii=False, indent=2) + "\n")


def _yaz_metin(yol, metin):
    yol = Path(yol)
    # newline="\n": Windows'ta metin kipi `\n`'i `\r\n` yapar; JSON baytları platformdan bağımsız kalsın.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=yol.parent, delete=False) as fh:
        fh.write(metin)
        gecici = fh.name
    uyum.degistir(gecici, yol)


def _oku_json(yol, varsayilan=None):
    path = Path(yol)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else varsayilan


def _iddialari_birlestir(eskiler, gelenler):
    """Aynı kaynaklı birebir olguda ilk kaydı ve kimliğini korur."""
    alanlar = ("iddia", "kaynak_turu", "kaynak", "surum", "kapsam")
    birlesik = {i["id"]: i for i in eskiler}
    for yeni in gelenler:
        if any(all(eski[a] == yeni[a] for a in alanlar) for eski in birlesik.values()):
            continue
        # Farklı olgularda mevcut kimlikle güncelleme davranışı korunur.
        birlesik[yeni["id"]] = yeni
    return list(birlesik.values())


# Alan bağımsız, salt okunur sistem yoklamaları (Opus politika kararı, 2026-09-24):
# karşılama ortam olgularını kullanıcıya sormadan kendisi doğrulayabilsin.
SISTEM_YOKLAMALARI = {
    ("uname", "-a"), ("ldd", "--version"), ("lscpu",), ("nvidia-smi",),
    ("vulkaninfo", "--summary"), ("python3", "--version"), ("df", "-h"), ("free", "-h"),
}
# Windows'ta uname/ldd/lscpu/df/free yoktur; varsayılan yoklama kümesi platforma göre seçilir. Doğrulama kümesi
# (SISTEM_YOKLAMALARI) iki platformda aynıdır: `python --version` gibi Windows biçimleri `_yoklama_argv` ile
# `python3 --version`e kanonikleşir, açık liste genişlemez.
VARSAYILAN_YOKLAMALAR = ({("nvidia-smi",), ("vulkaninfo", "--summary"), ("python", "--version")}
                         if uyum_komut.WINDOWS else SISTEM_YOKLAMALARI)
AZAMI_SORU_TURU = 4
AZAMI_ARASTIRMA_TURU = 3
BOYUTLAR = {
    1: "Başarı ölçüsü ve bugünkü yöntem/süre", 2: "Kalite referansı (iyi/kötü örnek)",
    3: "Negatif ölçütler", 4: "İlk teslim biçimi", 5: "Kullanıcı kontrol noktası",
    6: "Yetki kapsamı", 7: "Bütçe ve durma kuralı", 8: "Çıktı/kurulum konumu",
    9: "Mevcut araç ve yetenekler",
}


def _bilesik_soru_mu(metin):
    """Ayrı yanıt isteyen, noktalama ile birleştirilmiş soru cümlelerini bulur."""
    metin = metin.strip()
    return metin.count("?") > 1 or (";" in metin and metin.endswith("?"))


def _kullanici_ortami_mi(metin):
    """Açık iyelikle belirtilen kişisel ortam, web araştırmasının olgusu değildir."""
    return bool(re.search(
        r"\b(?:cihazınız|ağınız|hesabınız|verileriniz)\w*\b|"
        r"\bveriniz(?:de|den|e|in)\b|"
        r"\bkullanıcının\s+(?:cihaz|ağ|hesap|hesab|veri)\w*\b",
        metin.casefold()))


def _hedef_araclari(hedef):
    adaylar = re.findall(r"`([A-Za-z0-9._+-]+)`|\b([A-Za-z][A-Za-z0-9._+-]*[A-Z][A-Za-z0-9._+-]*)\b", hedef)
    adlar = {ad for cift in adaylar if (ad := next((x for x in cift if x), "")) and re.fullmatch(r"[A-Za-z][A-Za-z0-9._+-]*", ad)}
    adlar.update(ad for ad in re.findall(r"\b([a-z][A-Za-z0-9._+-]*)\s+(?:aracı|uygulaması|CLI|ile)\b", hedef)
                 if ad not in {"bir", "bu", "yeni", "mevcut"})
    adlar.update(re.findall(r"\b(?:omp|ffmpeg|codex)\b", hedef))
    kavramlar = {"saas", "cli", "api", "ui", "ux", "mvp", "sdk", "ai", "llm",
                "ide", "cpu", "gpu", "ram", "sql", "http", "https", "json", "html", "css"}
    return {ad for ad in adlar if ad.casefold() not in kavramlar
            and not re.fullmatch(r"[A-Z]{2,5}", ad)}


def _arac_yoklamasi(ad):
    """Aracın kurulu olup olmadığını soran salt okunur yoklama komutu."""
    return f"where.exe {ad}" if uyum_komut.WINDOWS else f"command -v {ad}"


def _yoklama_argv(komut):
    """Komutu böler. Windows'ta YOLSUZ komut adı kanonikleşir (`GIT.EXE` -> `git`; `python`/`py` ve tam
    `sys.executable` -> `python3`); yol içeren başka ad kanonikleşmez, dolayısıyla açık listeye giremez."""
    args = uyum_komut.bol(komut)
    if uyum_komut.WINDOWS and args:
        ad = uyum_komut.yolsuz_ad(args[0])
        if ad is not None:
            args[0] = "python3" if ad in ("python", "python3", "py") else ad
        elif os.path.normcase(args[0]) == os.path.normcase(sys.executable):
            args[0] = "python3"
    return args


def _yoklama_dogrula(komut):
    args = _yoklama_argv(komut)
    if tuple(args) in SISTEM_YOKLAMALARI:
        return
    if len(args) == 3 and args[:2] == ["command", "-v"] and re.fullmatch(r"[A-Za-z][A-Za-z0-9._+-]*", args[2]):
        return
    # Windows karşılığı: tek bir araç adı soran `where.exe` / `Get-Command` (`command -v` ile aynı yetki).
    if (uyum_komut.WINDOWS and len(args) == 2 and args[0] in ("where", "get-command")
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9._+-]*", args[1])):
        return
    if not args or args[0] not in {"pwd", "ls", "rg", "git"}:
        raise ValueError("yoklama komutu salt okunur açık listede değil")
    if args[0] == "git" and args[1:] not in (["status", "--short"], ["branch", "--show-current"], ["rev-parse", "HEAD"]):
        raise ValueError("git yoklaması açık listede değil")
    if args[0] == "rg" and args[1:] != ["--files"]:
        raise ValueError("rg yoklaması açık listede değil")
    if args[0] == "ls" and args[1:] not in ([], ["-la"]):
        raise ValueError("ls yoklaması açık listede değil")
    if args[0] == "pwd" and len(args) != 1:
        raise ValueError("pwd yoklaması açık listede değil")


class Karsilama:
    def __init__(self, calisma, *, yurutucu=None, iz_yolu=None):
        self.calisma = Path(calisma).resolve()
        self.kok = self.calisma / "karsilama"
        self.yurutucu = yurutucu
        self.iz_yolu = iz_yolu

    def oku(self, ad):
        return _oku_json(self.kok / f"{ad}.json", [] if ad in ("iddialar", "kararlar", "sorular") else {})

    def yaz(self, ad, veri):
        _yaz_json(self.kok / f"{ad}.json", veri)

    def olaylar(self):
        yol = self.kok / "olaylar.jsonl"
        return [json.loads(x) for x in yol.read_text(encoding="utf-8").splitlines() if x] if yol.exists() else []

    def olay(self, tur, *, metin=None, veri=None, is_turu="hedef_netlestirme", aktor="orvant", sonuc="ok", insan_dakika=None):
        entry = {"id": uuid.uuid4().hex, "t": _zaman().isoformat().replace("+00:00", "Z"),
                 "tur": tur, "aktor": aktor, "metin": metin, "veri": veri or {}}
        with (self.kok / "olaylar.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        kaydet(self.iz_yolu, self.calisma.name, is_turu, aktor_tur=aktor,
               kimlik="karsilama" if aktor == "orvant" else "kullanici",
               sonuc=sonuc, ozet=tur, maliyet={"insan_dakika": insan_dakika},
               ham={"olay_id": entry["id"]})
        return entry

    def _karar_haritasi_uygula(self, gelenler):
        eskiler = {k["id"]: k for k in self.oku("kararlar")}
        # Bütün kararları denetlemeden hiçbir karar olayı veya kayıt yazma.
        hatalar = self._karar_haritasi_dogrula(gelenler, list(eskiler.values()))
        if hatalar:
            raise ValueError("karar haritası doğrulanamadı: " + "; ".join(hatalar))
        yeni = dict(eskiler)
        for k in gelenler:
            kimlik = k["id"]
            eski = eskiler.get(kimlik)
            if eski and (eski["durum"] in ("cozuldu", "ertelendi") or eski.get("kaynak_olay_id")):
                self.olay("karar_haritasi_korundu", veri={"karar_id": kimlik}, is_turu="hedef_netlestirme")
                continue
            if eski and {a: b for a, b in eski.items() if a != "son_degisim_olay_id"} == k:
                continue
            entry = self.olay("karar_haritasi_guncellendi", veri={"karar_id": kimlik}, is_turu="hedef_netlestirme")
            yeni[kimlik] = {**k, "son_degisim_olay_id": entry["id"]}
        self._kararlari_koruyarak_yaz(list(yeni.values()))
        return list(yeni.values())

    def _kararlari_koruyarak_yaz(self, kararlar):
        """Değişmeyen nesnelerin JSON baytlarını, boşluklarını da korur."""
        yol = self.kok / "kararlar.json"
        metin = yol.read_bytes().decode("utf-8")
        if json.loads(metin) == kararlar:
            return
        parcalar = {}
        decoder = json.JSONDecoder()
        konum = metin.index("[") + 1
        while True:
            while konum < len(metin) and (metin[konum].isspace() or metin[konum] == ","):
                konum += 1
            if metin[konum] == "]":
                break
            eski, son = decoder.raw_decode(metin, konum)
            parcalar[eski["id"]] = (eski, metin[konum:son])
            konum = son
        cikti = []
        for k in kararlar:
            eski, ham = parcalar.get(k["id"], (None, None))
            cikti.append(ham if eski == k else json.dumps(k, ensure_ascii=False, indent=2).replace("\n", "\n  "))
        _yaz_metin(yol, "[\n  " + ",\n  ".join(cikti) + "\n]\n" if cikti else "[]\n")

    def _karar_delta_birlestir(self, output, kararlar):
        """Kimlikleri doğrular; korunan kayıtlar dahil mevcut sırayı korur."""
        eskiler = {k["id"]: k for k in kararlar}
        guncellenen = output["guncellenen_kararlar"]
        eklenen = output["yeni_kararlar"]
        kaldirilan = output["kaldirilan_karar_ids"]
        ids = [k["id"] for k in guncellenen + eklenen] + kaldirilan
        if len(ids) != len(set(ids)):
            raise ValueError("delta içinde yinelenen/çakışan karar kimliği")
        bilinmeyen = ({k["id"] for k in guncellenen} | set(kaldirilan)) - set(eskiler)
        if bilinmeyen:
            raise ValueError(f"delta içinde bilinmeyen karar kimliği: {sorted(bilinmeyen)}")
        if any(k["id"] in eskiler for k in eklenen):
            raise ValueError("delta yeni karar kimliği mevcut haritada var")
        yeni = dict(eskiler)
        for k in guncellenen:
            eski = eskiler[k["id"]]
            if (eski["durum"] not in ("cozuldu", "ertelendi") and not eski.get("kaynak_olay_id")
                    and {a: b for a, b in eski.items() if a != "son_degisim_olay_id"} != k):
                yeni[k["id"]] = k
        for kimlik in kaldirilan:
            eski = eskiler[kimlik]
            if eski["durum"] not in ("cozuldu", "ertelendi") and not eski.get("kaynak_olay_id"):
                del yeni[kimlik]
        for k in eklenen:
            yeni[k["id"]] = k
        return list(yeni.values())

    def _karar_delta_uygula(self, output):
        kararlar = self.oku("kararlar")
        try:
            yeni = self._karar_delta_birlestir(output, kararlar)
            hatalar = self._karar_haritasi_dogrula(
                output["guncellenen_kararlar"] + output["yeni_kararlar"], kararlar)
            if hatalar:
                raise ValueError("; ".join(hatalar))
        except ValueError as exc:
            self.olay("karar_delta_reddedildi", veri={"neden": str(exc)}, sonuc="ret")
            raise
        # Doğrulama tamamlandı; değişiklik olaylarını yalnız şimdi üret.
        eski = {k["id"]: k for k in kararlar}
        dokunulan = {k["id"] for k in output["guncellenen_kararlar"]} | set(output["kaldirilan_karar_ids"])
        for kimlik in sorted(dokunulan):
            k = eski[kimlik]
            if k["durum"] in ("cozuldu", "ertelendi") or k.get("kaynak_olay_id"):
                self.olay("karar_haritasi_korundu", veri={"karar_id": kimlik})
        for k in yeni:
            once = eski.get(k["id"])
            if once != k:
                entry = self.olay("karar_haritasi_guncellendi", veri={"karar_id": k["id"]})
                k["son_degisim_olay_id"] = entry["id"]
        for kimlik in sorted(set(eski) - {k["id"] for k in yeni}):
            self.olay("karar_haritasi_kaldirildi", veri={"karar_id": kimlik})
        self._kararlari_koruyarak_yaz(yeni)
        return yeni

    def _harita_uygula(self, output):
        if "guncellenen_kararlar" in output:
            return self._karar_delta_uygula(output)
        return self._karar_haritasi_uygula(output["kararlar"])

    def _karar_haritasi_dogrula(self, gelenler, kararlar, *, iddia_ids=None, olay_ids=None):
        hatalar = []
        gorulen = set()
        korunan = {k["id"] for k in kararlar
                   if k["durum"] in ("cozuldu", "ertelendi") or k.get("kaynak_olay_id")}
        for k in gelenler:
            kimlik = k["id"]
            if kimlik in gorulen:
                hatalar.append(f"yinelenen karar kimliği: {kimlik}")
            gorulen.add(kimlik)
            if k["sahip"] == "varsayilan" and (
                    k["durum"] != "cozuldu" or not k.get("deger")
                    or k.get("kaynak") != "onerilen_varsayim"):
                hatalar.append(f"{kimlik}: güvenli varsayılan çözülmüş, değerli ve kaynak=onerilen_varsayim olmalı")
            if kimlik in korunan:
                continue  # Mevcut kullanıcı kararı aynen kalır; modelin yeni dayanağı uygulanmaz.
            if _bilesik_soru_mu(k["soru"]["metin"]):
                hatalar.append(f"{kimlik}: bileşik soru ayrı atomik kararlara bölünmeli")
            if iddia_ids is not None and not set(k["dayanak_iddia_ids"]) <= iddia_ids:
                hatalar.append(f"{kimlik}: karar haritasında bilinmeyen iddia kaynağı: "
                              f"{sorted(set(k['dayanak_iddia_ids']) - iddia_ids)}")
            if olay_ids is not None and k.get("kaynak_olay_id") and k["kaynak_olay_id"] not in olay_ids:
                hatalar.append(f"{kimlik}: karar haritasında bilinmeyen kullanıcı olayı: {k['kaynak_olay_id']}")
        return hatalar

    def _harita_cagir(self, o, iddialar, kararlar):
        delta = bool(kararlar) or o["durum"] in ("kararlar_cikarildi", "cevap_islendi")
        girdi = {**self._baglam(o), "iddialar": iddialar, "onceki_kararlar": kararlar,
                 "harita_bicimi": "delta" if delta else "tam"}
        def eksikler(birlesik):
            gorulen = {b for k in birlesik for b in k.get("kontrol_boyutlari", [])}
            return sorted(set(BOYUTLAR) - gorulen)
        celiskili = [i for i in iddialar if i["dogrulama"] == "celiskili"]
        iddia_ids = {i["id"] for i in iddialar}
        olay_ids = {e["id"] for e in girdi["kullanici_olaylari"]}
        for deneme in range(2):
            output = self._rol("karar_haritasi", girdi, "hedef_netlestirme")
            gelen = (output["guncellenen_kararlar"] + output["yeni_kararlar"]
                     if delta else output["kararlar"])
            for k in gelen:
                if (k["sahip"] == "arastirilabilir" and k["durum"] == "acik"
                        and _kullanici_ortami_mi(k["baslik"] + " " + k["soru"]["metin"])):
                    k["sahip"] = "kullanici"
            output["arama_istekleri"] = [konu for konu in output["arama_istekleri"]
                                        if not _kullanici_ortami_mi(konu)]
            hatalar = []
            if delta:
                gelenler = output["guncellenen_kararlar"] + output["yeni_kararlar"]
                try:
                    birlesik = self._karar_delta_birlestir(output, kararlar)
                except ValueError as exc:
                    hatalar.append(str(exc))
                    self.olay("karar_delta_reddedildi", veri={"neden": str(exc)}, sonuc="ret")
                    birlesik = kararlar
            else:
                gelenler = birlesik = output["kararlar"]
            missing = eksikler(birlesik)
            hatalar += self._karar_haritasi_dogrula(
                gelenler, kararlar, iddia_ids=iddia_ids, olay_ids=olay_ids)
            # Çelişkili özel adlar soru turunun başında açıklığa kavuşturulmalı.
            if celiskili and not any(k["kategori"] == "terimler" and k["sahip"] == "kullanici"
                                    and k["etki"] == "yuksek" and k.get("etkiledigi_kararlar")
                                    for k in birlesik):
                hatalar.append("çelişkili terim için yüksek etkili bağımlı kullanıcı kararı eksik")
            if not hatalar and (not missing or deneme == 1):
                break
            if deneme == 1:
                raise ValueError("karar haritası doğrulanamadı: " + "; ".join(hatalar))
            girdi = {**girdi, "yeniden_istek": True, "onceki_yanit": output,
                     "dogrulama_hatalari": hatalar +
                     ([f"eksik kontrol boyutları: {missing}"] if missing else [])}
            if missing:
                girdi["eksik_boyutlar"] = {str(n): BOYUTLAR[n] for n in missing}
        if missing:
            self.olay("karar_haritasi_eksik_boyut", veri={"boyutlar": missing},
                      is_turu="hedef_netlestirme", sonuc="hata")
        return output, missing

    def _arastirma_cagir(self, o, konular):
        sinir = ayarlar.arastirma_konu_siniri(self.calisma)
        secilen = konular[:sinir]
        # Kaynak ayrımları özette kalır; erişim/doğrulama geçmişi taşınmaz.
        ozetler = [{"id": i["id"], "iddia": i["iddia"], "kaynak": {
            "tur": i["kaynak_turu"], "adres": i["kaynak"],
            "surum": i["surum"], "kapsam": i["kapsam"]}}
            for i in self.oku("iddialar")]
        output = self._rol("baglam_topla", {**self._baglam(o),
            "arastirma_konulari": secilen, "hedefli_aramalar": secilen,
            "yeni_arastirma_konulari": [k for k in secilen if k in o.get("yeni_arastirma_konulari", [])],
            "arastirma_butcesi": {"azami_konu": sinir}, "iddialar": ozetler,
            "izinli_yoklamalar": o["yoklamalar"]}, "arastirma")
        return output, konular[sinir:]

    def _baglam(self, o):
        return {"hedef": o["hedef"], "kaynaklar": o["kaynaklar"],
                "kaynak_icerikleri": guncel_gozlemler(self.oku("kaynak_icerikleri") or []),
                "kullanici_olaylari": [e for e in self.olaylar()
                    if e["aktor"] == "kullanici" and e["tur"] in ("kullanici_hedefi", "kullanici_cevabi")]}

    def _sozlesme_tamamla(self, o):
        """Soru sorulmamış hedef de sözleşmeye kaynak olabilir; onay vermez."""
        islenmis = self.oku("islenmis")
        eksik = [a for a in ("gereksinimler", "kabul_olcutleri") if not islenmis[a]]
        if not eksik:
            return []
        baglam = self._baglam(o)
        output = self._rol("sozlesme_taslagi", {**baglam, "eksik_alanlar": eksik,
            "mevcut": islenmis, "kararlar": self.oku("kararlar")}, "karar_belgesi")
        olaylar = {e["id"]: e for e in baglam["kullanici_olaylari"]}
        for alan, harf in (("gereksinimler", "G"), ("kabul_olcutleri", "A")):
            if alan not in eksik and output[alan]:
                raise ValueError("taslak mevcut sözleşme maddelerini değiştiremez")
            for n, item in enumerate(output[alan], 1):
                olay = olaylar.get(item["dayanak_olay_id"])
                if not olay or not item["dayanak_alinti"].strip() or item["dayanak_alinti"] not in olay["metin"]:
                    raise ValueError("sözleşme taslağında doğrulanamayan kaynak pasajı")
                if alan == "gereksinimler" and (item["kaynak_turu"] != "kullanici" or item["kaynak_id"] != olay["id"]):
                    raise ValueError("sözleşme taslağında uyuşmayan gereksinim kaynağı")
                if not item["metin"].strip():
                    raise ValueError("sözleşme taslağında boş madde")
                islenmis[alan].append({**item, "id": f"{harf}-hedef-{n}", "model_kimligi": item["id"]})
        # Her iki liste doğrulanmadan kalıcı maddeler yazılmaz.
        self.yaz("islenmis", islenmis)
        return output["eksikler"]

    def _yoklama_isteklerini_isle(self, o, output):
        for komut in output["yoklama_istekleri"]:
            try:
                _yoklama_dogrula(komut)
            except (ValueError, IndexError) as exc:
                self.olay("yoklama_istegi_reddedildi", veri={"komut": komut, "neden": str(exc)},
                          is_turu="arastirma", sonuc="ret")
            else:
                if komut not in o["yoklamalar"]:
                    o["yoklamalar"].append(komut)
                    self.olay("yoklama_istegi_eklendi", veri={"komut": komut}, is_turu="arastirma")

    def _soru_turu_ac(self, o, selected):
        o["tur"] += 1
        self.yaz("oturum", o)
        sorular = self.oku("sorular")
        for k in selected:
            sorular.append({"id": uuid.uuid4().hex[:12], "karar_ids": [k["id"]],
                            "revizyon": o["revizyon"] + 1, "tur": o["tur"],
                            **k["soru"], "durum": "acik", "cevap_olay_id": None,
                            "karar_surumleri": {k["id"]: k.get("son_degisim_olay_id")}})
        self.yaz("sorular", sorular)
        self.olay("soru_secimi", veri={"soru_ids": [s["id"] for s in sorular if s["tur"] == o["tur"]]},
                  is_turu="hedef_netlestirme")
        return self.gec("soru_bekliyor", "hedef_netlestirme")

    def _arastirma_erteleyerek_bitir(self, kararlar):
        gerekce = "Araştırma bütçesi içinde doğrulanamadı; sonraki aşamada doğrulanacak."
        degisti = False
        for k in kararlar:
            if k["sahip"] == "arastirilabilir" and k["durum"] == "acik":
                entry = self.olay("arastirma_karari_ertelendi", veri={"karar_id": k["id"], "gerekce": gerekce}, is_turu="arastirma")
                k.update(durum="ertelendi", erteleme_gerekcesi=gerekce,
                         son_degisim_olay_id=entry["id"])
                degisti = True
        if degisti:
            self.yaz("kararlar", kararlar)

    def _onceki_ertelemleri_koru(self, kararlar):
        ertelenen = erteleme_kayitlari(self.oku("sorular"), self.olaylar())
        degisti = False
        for k in kararlar:
            if k["id"] in ertelenen and k["durum"] in ("acik", "ertelendi"):
                kayit = ertelenen[k["id"]]
                entry = self.olay("ertelenen_karar_soru_seciminden_elendi", veri={
                    "karar_id": k["id"], **kayit}, is_turu="hedef_netlestirme")
                if k["durum"] == "acik":
                    k.update(durum="ertelendi", **kayit, son_degisim_olay_id=entry["id"])
                    degisti = True
        if degisti:
            self.yaz("kararlar", kararlar)
        return ertelenen

    def gec(self, yeni, is_turu):
        oturum = self.oku("oturum")
        once = oturum["durum"]
        oturum["durum"] = yeni
        oturum["revizyon"] += 1
        self.yaz("oturum", oturum)
        self.olay("durum_gecisi", veri={"once": once, "sonra": yeni, "revizyon": oturum["revizyon"]}, is_turu=is_turu)
        return oturum

    def baslat(self, hedef, *, kaynaklar=(), yoklamalar=None, butce=None):
        if self.kok.exists():
            raise ValueError("karşılama oturumu zaten var")
        if not hedef.strip():
            raise ValueError("hedef boş olamaz")
        if yoklamalar is None:
            yoklamalar = sorted(" ".join(args) for args in VARSAYILAN_YOKLAMALAR)
            yoklamalar += [_arac_yoklamasi(ad) for ad in sorted(_hedef_araclari(hedef))]
        for komut in yoklamalar:
            _yoklama_dogrula(komut)
        kaynak_icerikleri = acik_kaynaklar(kaynaklar)
        self.kok.mkdir(parents=True)
        self.yaz("kaynak_icerikleri", kaynak_icerikleri)
        self.yaz("oturum", {"hedef": hedef, "durum": "baslatildi", "revizyon": 0,
                             "tur": 0, "butce": butce or {}, "kaynaklar": list(kaynaklar),
                             "yoklamalar": list(yoklamalar), "arastirma_turu": 0,
                             "eksik_boyutlar": [], "yeni_arastirma_konulari": []})
        for ad, veri in (("iddialar", []), ("kararlar", []), ("sorular", []), ("islenmis", {"gereksinimler": [], "kabul_olcutleri": [], "izinler": []}), ("sozlesme", {})):
            self.yaz(ad, veri)
        self.olay("durum_gecisi", veri={"once": None, "sonra": "baslatildi", "revizyon": 0}, is_turu="hedef_netlestirme")
        self.olay("kullanici_hedefi", metin=hedef, aktor="kullanici")
        return self.oku("oturum")

    def _rol(self, ad, girdi, is_turu):
        self.olay("rol_cagrisi", veri={"rol": ad}, is_turu=is_turu)
        try:
            sonuc = rol_cagir(ad, girdi, calisma=self.calisma, iz_yolu=self.iz_yolu,
                              yurutucu=self.yurutucu)
        except Exception as exc:
            self.olay("rol_hatasi", veri={"rol": ad, "hata": str(exc)},
                      is_turu=is_turu, sonuc="hata")
            raise
        self.olay("rol_sonucu", veri={"rol": ad}, is_turu=is_turu)
        return sonuc

    def ilerle(self):
        o = self.oku("oturum")
        durum = o["durum"]
        if durum == "baslatildi":
            output, _ = self._arastirma_cagir(o, [o["hedef"]])
            self.yaz("iddialar", output["iddialar"])
            self._yoklama_isteklerini_isle(o, output)
            o["arastirma_turu"] = 1
            self.yaz("oturum", o)
            return self.gec("baglam_toplandi", "arastirma")
        if durum == "baglam_toplandi":
            output, missing = self._harita_cagir(o, self.oku("iddialar"), self.oku("kararlar"))
            self._harita_uygula(output)
            o = self.gec("kararlar_cikarildi", "hedef_netlestirme")
            o["arama_istekleri"] = output["arama_istekleri"]
            o["eksik_boyutlar"] = missing
            self.yaz("oturum", o)
            return o
        if durum == "soru_bekliyor":
            acik = [s for s in self.oku("sorular") if s["durum"] == "acik"]
            if acik:
                return o
            cevaplar = [e for e in self.olaylar() if e["tur"] == "kullanici_cevabi" and e["veri"].get("tur") == o["tur"]]
            output = self._rol("cevap_isle", {**self._baglam(o), "iddialar": self.oku("iddialar"),
                "mevcut": self.oku("islenmis"), "kararlar": self.oku("kararlar"),
                "cevap_olaylari": cevaplar}, "hedef_netlestirme")
            try:
                self._cevap_uygula(output, cevaplar)
            except ValueError as exc:
                self.olay("rol_hatasi", veri={"rol": "cevap_isle", "hata": str(exc)},
                          is_turu="hedef_netlestirme", sonuc="hata")
                raise
            return self.gec("cevap_islendi", "hedef_netlestirme")
        if durum in ("kararlar_cikarildi", "cevap_islendi"):
            kararlar = self.oku("kararlar")
            ertelenen = self._onceki_ertelemleri_koru(kararlar)
            hedefli = list(dict.fromkeys(o.get("bekleyen_arastirma_konulari", []) +
                o.get("yeni_arastirma_konulari", []) + o.get("arama_istekleri", [])))
            arastirma_gerekli = bool(o.get("bekleyen_arastirma_konulari") or o.get("yeni_arastirma_konulari")) or (
                any(k["sahip"] == "arastirilabilir" and k["durum"] == "acik" for k in kararlar)
                and bool(o.get("arama_istekleri")))
            if arastirma_gerekli and o["arastirma_turu"] < AZAMI_ARASTIRMA_TURU:
                iddialar = self.oku("iddialar")
                output, bekleyen = self._arastirma_cagir(o, hedefli)
                iddialar = _iddialari_birlestir(iddialar, output["iddialar"])
                self.yaz("iddialar", iddialar)
                self._yoklama_isteklerini_isle(o, output)
                o["arastirma_turu"] += 1
                o["bekleyen_arastirma_konulari"] = bekleyen
                o["yeni_arastirma_konulari"] = [k for k in o.get("yeni_arastirma_konulari", []) if k in bekleyen]
                self.yaz("oturum", o)
                map_out, missing = self._harita_cagir(o, iddialar, kararlar)
                self._harita_uygula(map_out)
                o["arama_istekleri"] = map_out["arama_istekleri"]
                o["eksik_boyutlar"] = missing
                self.yaz("oturum", o)
                return o
            self._arastirma_erteleyerek_bitir(kararlar)
            selected = sec(kararlar, sorular=self.oku("sorular"), ertelenen_ids=ertelenen)
            acik_yuksek = [k for k in kararlar if k["sahip"] == "kullanici" and k["etki"] == "yuksek" and k["durum"] == "acik"]
            if o["tur"] >= AZAMI_SORU_TURU:
                gerekce = "Dört soru turunda çözülemedi; fizibilite aşamasına ertelendi."
                for k in kararlar:
                    if k["sahip"] == "kullanici" and k["durum"] == "acik":
                        entry = self.olay("soru_turu_siniri_ertelendi", veri={"karar_id": k["id"], "gerekce": gerekce})
                        k.update(durum="ertelendi", erteleme_gerekcesi=gerekce, son_degisim_olay_id=entry["id"])
                self.yaz("kararlar", kararlar)
                selected = []
                acik_yuksek = []
            if selected:
                return self._soru_turu_ac(o, selected)
            if acik_yuksek:
                for k in acik_yuksek[:5]:
                    k["soru"]["metin"] = k["soru"]["metin"].rstrip() + " Lütfen bu kararı netleştirin."
                    entry = self.olay("acik_karar_guvenlik_sorusu", veri={"karar_id": k["id"]})
                    k["son_degisim_olay_id"] = entry["id"]
                self.yaz("kararlar", kararlar)
                return self._soru_turu_ac(o, acik_yuksek[:5])
            eksikler = self._sozlesme_tamamla(o)
            sozlesme = kur(o, kararlar, self.oku("islenmis"), self.oku("iddialar"),
                            eksik_boyutlar=o.get("eksik_boyutlar", []))
            sozlesme["open_questions"].extend(eksikler)
            self.yaz("sozlesme", sozlesme)
            self.gec("sozlesme_taslak", "karar_belgesi")
            return self.gec("onay_bekliyor", "karar_belgesi")
        return o

    def sorular(self):
        return [s for s in self.oku("sorular") if s["durum"] == "acik"]

    def cevapla(self, soru_id, metin, *, dakika=None, yer_tutucu_kabul=False):
        if self.oku("oturum")["durum"] != "soru_bekliyor":
            raise ValueError("cevap beklenmiyor")
        sorular = self.oku("sorular")
        soru = next((s for s in sorular if s["id"] == soru_id), None)
        if not soru or soru["durum"] != "acik":
            raise ValueError("açık soru bulunamadı")
        yer_tutucu_denetle(metin, kabul=yer_tutucu_kabul)
        if dakika is not None and dakika < 0:
            raise ValueError("aktif dakika negatif olamaz")
        kararlar = {k["id"]: k for k in self.oku("kararlar")}
        if any(kararlar.get(kimlik, {}).get("son_degisim_olay_id") != surum
               for kimlik, surum in soru.get("karar_surumleri", {}).items()):
            soru["durum"] = "iptal"
            yeniler = []
            for kimlik in soru["karar_ids"]:
                k = kararlar.get(kimlik)
                if k and k["durum"] == "acik" and k["sahip"] == "kullanici":
                    yeniler.append({"id": uuid.uuid4().hex[:12], "karar_ids": [kimlik],
                                    "revizyon": self.oku("oturum")["revizyon"], "tur": soru["tur"],
                                    **k["soru"], "durum": "acik", "cevap_olay_id": None,
                                    "karar_surumleri": {kimlik: k.get("son_degisim_olay_id")}})
            sorular.extend(yeniler)
            self.yaz("sorular", sorular)
            self.olay("soru_iptal", veri={"soru_id": soru_id, "yeni_soru_ids": [s["id"] for s in yeniler]}, is_turu="hedef_netlestirme")
            raise ValueError("karar değişti; eski soru iptal edildi")
        entry = self.olay("kullanici_cevabi", metin=metin, veri={"soru_id": soru_id, "tur": soru["tur"], "insan_dakika": dakika},
                           aktor="kullanici", is_turu="hedef_netlestirme", insan_dakika=dakika)
        soru["durum"] = "cevaplandi"
        soru["cevap_olay_id"] = entry["id"]
        self.yaz("sorular", sorular)
        return entry

    def _cevap_uygula(self, output, cevaplar):
        cevap_ids = {e["id"] for e in cevaplar}
        kararlar = {k["id"]: k for k in self.oku("kararlar")}
        sorular = self.oku("sorular")
        ertelemeler = erteleme_kayitlari(sorular, cevaplar)
        # Model açık/çözülmüş dese veya güncellemeyi atlasa da açık kullanıcı ertelemesi korunur.
        guncellemeler = {u["id"]: u for u in output["guncellemeler"]}
        for kimlik, kayit in ertelemeler.items():
            if kimlik not in kararlar:
                continue
            eski = kararlar[kimlik]
            u = guncellemeler.setdefault(kimlik, {"id": kimlik, "soru": eski["soru"]})
            u.update(durum="ertelendi", deger=None, soru=eski["soru"], **kayit)
        output["guncellemeler"] = list(guncellemeler.values())
        yeni_konular = list(output["yeni_arastirma_konulari"])
        for u in output["guncellemeler"]:
            if (u["id"] not in kararlar or
                (u["kaynak_olay_id"] and u["kaynak_olay_id"] not in cevap_ids) or
                (u["durum"] in ("cozuldu", "ertelendi") and u["kaynak_olay_id"] not in cevap_ids)):
                raise ValueError("karar güncellemesinde geçersiz cevap kaynağı")
            eski = kararlar[u["id"]]
            if u["durum"] == "ertelendi":
                cevap = next(e for e in cevaplar if e["id"] == u["kaynak_olay_id"])
                asama = cevaptan(cevap["metin"]) or u.get("erteleme_gerekcesi")
                if not asama or not asama.strip():
                    raise ValueError("ertelenen karar için bağlı aşama gerekçesi gerekli")
                u.update(deger=None, soru=eski["soru"], erteleme_gerekcesi=
                         f"Kullanıcı cevabı: {cevap['metin']} Bağlı aşama: {asama}.")
            if u["durum"] == "acik" and (not u["soru"]["metin"].strip() or
                                            u["soru"]["metin"].strip() == eski["soru"]["metin"].strip()):
                raise ValueError("açık kalan karar için yeni netleştirme sorusu gerekli")
            if (eski["kategori"] in ("terimler", "dis_bagimliliklar") and eski["durum"] == "acik"
                    and u["durum"] == "cozuldu" and u.get("deger")):
                yeni_konular.append(u["deger"])
        for k in output["yeni_kararlar"]:
            if k["id"] in kararlar:
                raise ValueError("yinelenen karar kimliği")
            if k["sahip"] == "varsayilan" and (k["durum"] != "cozuldu" or not k.get("deger")
                                                 or k.get("kaynak") != "onerilen_varsayim"):
                raise ValueError("güvenli varsayılan çözülmüş, değerli ve kaynaklı olmalı")
        islenmis = self.oku("islenmis")
        for alan in ("gereksinimler", "kabul_olcutleri", "izinler"):
            for item in output[alan]:
                if "model_kimligi" not in item:
                    raise ValueError("model kimliği alanı eksik")
        for u in output["guncellemeler"]:
            if any(kararlar[u["id"]].get(alan) != deger for alan, deger in u.items()):
                entry = self.olay("karar_cevapla_guncellendi", veri={"karar_id": u["id"], "cevap_olay_id": u["kaynak_olay_id"]})
                kararlar[u["id"]]["son_degisim_olay_id"] = entry["id"]
            kararlar[u["id"]].update(u)
            if u["durum"] == "ertelendi":
                for soru in sorular:
                    if (u["id"] in soru["karar_ids"] and
                            soru.get("cevap_olay_id") == u["kaynak_olay_id"]):
                        soru["erteleme_asamasi"] = u["erteleme_gerekcesi"]
                self.olay("kullanici_karari_ertelendi", veri={"karar_id": u["id"],
                    "cevap_olay_id": u["kaynak_olay_id"], "gerekce": u["erteleme_gerekcesi"]})
        self.yaz("sorular", sorular)
        for k in output["yeni_kararlar"]:
            entry = self.olay("karar_cevapla_eklendi", veri={"karar_id": k["id"]})
            kararlar[k["id"]] = {**k, "son_degisim_olay_id": entry["id"]}
        tur = self.oku("oturum")["tur"]
        for alan, harf in (("gereksinimler", "G"), ("kabul_olcutleri", "A"), ("izinler", "Y")):
            mevcut = {item["id"] for item in islenmis[alan]}
            n = 1
            for item in output[alan]:
                while f"{harf}-{tur}-{n}" in mevcut:
                    n += 1
                model_id = item["id"]
                yeni = {**item, "id": f"{harf}-{tur}-{n}", "model_kimligi": model_id}
                islenmis[alan].append(yeni)
                mevcut.add(yeni["id"])
                n += 1
        self.yaz("kararlar", list(kararlar.values()))
        self.yaz("islenmis", islenmis)
        if yeni_konular:
            o = self.oku("oturum")
            o["yeni_arastirma_konulari"] = list(dict.fromkeys(o.get("yeni_arastirma_konulari", []) + yeni_konular))
            self.yaz("oturum", o)

    def onayla(self, revizyon, *, dakika=None):
        if dakika is not None and dakika < 0:
            raise ValueError("aktif dakika negatif olamaz")
        if self.oku("oturum")["durum"] != "onay_bekliyor":
            raise ValueError("onay beklenmiyor")
        s = self.oku("sozlesme")
        if revizyon != s["revizyon"]:
            raise ValueError("onay revizyonu sözleşmeyle eşleşmiyor")
        durum, hatalar = kapi(s, self.olaylar())
        self.olay("kapi", veri={"hatalar": hatalar, "en_fazla": durum},
                  is_turu="dogrulama", sonuc="ret" if hatalar else "ok")
        if hatalar:
            self.gec("engelli", "dogrulama")
            raise ValueError("; ".join(hatalar))
        sha = sozlesme_hash(s)
        onay = self.olay("kullanici_onayi", veri={"revizyon": revizyon, "durum": durum, "insan_dakika": dakika,
                         "sozlesme_revizyon": revizyon, "sozlesme_sha256": sha},
                         aktor="kullanici", is_turu="dogrulama", insan_dakika=dakika)
        s["onay"] = {"revizyon": revizyon, "olay_id": onay["id"], "durum": durum,
                     "sozlesme_revizyon": revizyon, "sozlesme_sha256": sha}
        self.yaz("sozlesme", s)
        self.gec(durum, "dogrulama")
        if durum == "onaylandi":
            self.yaz("v03_spec", v03_spec(s))
        return durum

    def durum(self):
        o = self.oku("oturum")
        o["toplam_insan_dakika"] = sum(e["veri"].get("insan_dakika") or 0 for e in self.olaylar()
                                         if e["tur"] in ("kullanici_cevabi", "kullanici_onayi"))
        return o
