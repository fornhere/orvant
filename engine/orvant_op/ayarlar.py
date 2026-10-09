"""Orvant ayar katmanı: model rolleri, yollar ve codex ikili adı (W40).

Çözüm sırası (ilk bulunan kazanır):
  1. Ortam değişkeni: ``ORVANT_MODEL_<ROL>`` > ``ORVANT_MODEL`` (tüm roller),
     ``ORVANT_CODEX``, ``ORVANT_DEPO``, ``ORVANT_CEKIRDEK``.
  2. ``orvant.toml``: ``ORVANT_AYAR`` açık dosyası varsa o, yoksa çalışma dizininden
     köke doğru ilk ``orvant.toml`` (proje); ardından
     ``$XDG_CONFIG_HOME/orvant/orvant.toml`` (varsayılan ``~/.config/orvant/``, kullanıcı).
     Proje dosyası kullanıcı dosyasını anahtar anahtar ezer.
  3. Varsayılan (bütün roller için gpt-6.1-sol).

Dosya biçimi::

    [modeller]
    varsayilan = "gpt-6.1-sol"   # isteğe bağlı: rol özel değeri yoksa
    isci = "gpt-6.1-sol"
    arastirma = "gpt-6.1-sol"

    [codex]
    ikili = "codex"

    [yurutucu]               # yoksa PATH'teki tek Codex/Claude otomatik seçilir
    tur = "claude"

    [kehanet]
    yalitim = "auto"         # auto | bwrap | codex

    [yollar]                   # göreli yol, dosyanın bulunduğu dizine göredir
    depo = "."
    cekirdek = "../orvant-beceri"

    [karsilama]
    arastirma_konu_siniri = 3   # ortam: ORVANT_ARASTIRMA_KONU_SINIRI

    [operator]
    kota_esigi = 80             # isteğe bağlı; yoksa kapalı (ortam: ORVANT_KOTA_ESIGI, "kapali")

    [kat]
    kip = "golge"               # "kapali" veya "golge"; "otorite" adım 6a'da yasak

    [olcer]
    proje_oturumlari = [        # glob, bu dosyanın dizinine göre çözülür
      {proje = "ornek", yol = "../oturumlar/ornek-*"},
    ]

Modül yalnız standart kitaplığı kullanır ve orvant_op/orvant_gelisim içinden hiçbir şey
içe aktarmaz; böylece her katman (orvant_gelisim.kayit dahil) döngüsüz kullanabilir.
Çağrı yerleri bu modülden çalışma anında çözülür.
"""
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tomllib

# Rol → varsayılan model. Model adları ortam veya orvant.toml ile değiştirilebilir.
ROLLER = {
    "isci": "gpt-6.1-sol",             # yurutucu.hedef_calistir (goal modu işçi)
    "sema": "gpt-6.1-sol",             # yurutucu.calistir şemalı tek çağrı (genel)
    "karsilama": "gpt-6.1-sol",        # S1 karsilama/roller.py
    "mimar": "gpt-6.1-sol",            # S2 mimar/roller.py, yeniden_planlama.py
    "kehanet": "gpt-6.1-sol",          # S2 mimar/kehanet.py kehanet yazımı
    "arastirma": "gpt-6.1-sol",       # duman.py (aramalı duman testi)
    "kor_teshis": "gpt-6.1-sol",       # orvant_gelisim/kor_teshis.py kos
    "kor_teshis_puan": "gpt-6.1-sol",  # orvant_gelisim/kor_teshis.py puanlama
    "gelistir_oneri": "gpt-6.1-sol",    # orvant_gelisim/gelistir.py öneren
    "gelistir_isci": "gpt-6.1-sol",   # orvant_gelisim/gelistir.py ayrı işçi
}
CODEX_IKILI = "codex"
DOSYA_ADI = "orvant.toml"
_PAKET = Path(__file__).resolve().parent


class AyarHatasi(ValueError):
    """Ayar dosyası veya değeri geçersiz."""


class YurutucuSecimHatasi(AyarHatasi):
    """Otomatik algılama tek bir yerel yürütücü seçemedi."""


def _metin(deger, ad):
    if not isinstance(deger, str) or not deger.strip() or any(c.isspace() for c in deger):
        raise AyarHatasi(f"{ad} boşluksuz, boş olmayan metin olmalı: {deger!r}")
    return deger


def _oku(yol):
    try:
        with Path(yol).open("rb") as fh:
            veri = tomllib.load(fh)
    except OSError as exc:
        raise AyarHatasi(f"ayar dosyası okunamadı: {yol}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AyarHatasi(f"ayar dosyası geçersiz TOML: {yol}: {exc}") from exc
    for bolum in ("modeller", "codex", "codex_cloud", "claude", "yurutucu", "kehanet", "yollar", "karsilama", "koordinasyon", "olcer", "kat", "inceleme"):
        if bolum in veri and not isinstance(veri[bolum], dict):
            raise AyarHatasi(f"{yol}: [{bolum}] tablo olmalı")
    return veri


def _proje_dosyasi(baslangic=None):
    dizin = Path(baslangic or Path.cwd()).resolve()
    for aday in (dizin, *dizin.parents):
        yol = aday / DOSYA_ADI
        if yol.is_file():
            return yol
    return None


def _kullanici_dosyasi():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    kok = Path(xdg) if xdg else Path.home() / ".config"
    yol = kok / "orvant" / DOSYA_ADI
    return yol if yol.is_file() else None


def ayar_dosyalari(baslangic=None):
    """Öncelik sırasıyla (güçlüden zayıfa) okunacak ayar dosyaları."""
    acik = os.environ.get("ORVANT_AYAR")
    if acik:
        yol = Path(acik).expanduser()
        if not yol.is_file():
            raise AyarHatasi(f"ORVANT_AYAR dosyası yok: {yol}")
        proje = yol
    else:
        proje = _proje_dosyasi(baslangic)
    kullanici = _kullanici_dosyasi()
    dosyalar = [p for p in (proje, kullanici) if p is not None]
    # Aynı dosya iki kez okunmasın (ör. proje dizini ~/.config/orvant ise).
    tekil = []
    for p in dosyalar:
        if all(p.resolve() != q.resolve() for q in tekil):
            tekil.append(p)
    return tekil


def _dosyadan(bolum, anahtar, baslangic=None):
    """(değer, dosya) ya da (None, None): ilk dosyada bulunan değer kazanır."""
    for yol in ayar_dosyalari(baslangic):
        tablo = _oku(yol).get(bolum) or {}
        if anahtar in tablo:
            return tablo[anahtar], yol
    return None, None


def _yol_coz(deger, dosya, ad):
    if not isinstance(deger, str) or not deger.strip():
        raise AyarHatasi(f"{ad} boş olmayan yol olmalı: {deger!r}")
    yol = Path(deger).expanduser()
    if not yol.is_absolute() and dosya is not None:
        yol = Path(dosya).resolve().parent / yol
    return yol.resolve()


def model_kaynakli(rol, baslangic=None):
    """(model, kaynak) döndürür; kaynak 'ortam:<AD>', dosya yolu veya 'varsayilan'."""
    if rol not in ROLLER:
        raise AyarHatasi(f"bilinmeyen model rolü: {rol!r} (bilinen: {', '.join(ROLLER)})")
    for ad in (f"ORVANT_MODEL_{rol.upper()}", "ORVANT_MODEL"):
        if os.environ.get(ad):
            return _metin(os.environ[ad], ad), f"ortam:{ad}"
    # Dosya önceliği anahtardan güçlüdür: proje dosyasının `varsayilan`ı kullanıcı
    # dosyasının rol anahtarını da ezer.
    for dosya in ayar_dosyalari(baslangic):
        tablo = _oku(dosya).get("modeller") or {}
        for anahtar in (rol, "varsayilan"):
            if anahtar in tablo:
                return _metin(tablo[anahtar], f"{dosya} [modeller].{anahtar}"), str(dosya)
    return ROLLER[rol], "varsayilan"


def model(rol, baslangic=None):
    """Rol için model adı (ör. ``model("isci")`` → ``"gpt-6.1-sol"``)."""
    return model_kaynakli(rol, baslangic)[0]


def codex_ikili_kaynakli(baslangic=None):
    if os.environ.get("ORVANT_CODEX"):
        return _metin(os.environ["ORVANT_CODEX"], "ORVANT_CODEX"), "ortam:ORVANT_CODEX"
    deger, dosya = _dosyadan("codex", "ikili", baslangic)
    if deger is not None:
        return _metin(deger, f"{dosya} [codex].ikili"), str(dosya)
    return CODEX_IKILI, "varsayilan"


def codex_ikili(baslangic=None):
    """Codex CLI komut adı veya yolu (varsayılan ``codex``)."""
    return codex_ikili_kaynakli(baslangic)[0]


YURUTUCU_TURLERI = ("codex", "claude", "codex-cloud")
CLAUDE_IKILI = "claude"


def yurutucu_turu_kaynakli(baslangic=None):
    """İşçi yürütücüsü: ortam > dosya > kurulu ikililerin tek anlamlı seçimi."""
    if os.environ.get("ORVANT_YURUTUCU"):
        deger, kaynak = os.environ["ORVANT_YURUTUCU"], "ortam:ORVANT_YURUTUCU"
    else:
        deger, dosya = _dosyadan("yurutucu", "tur", baslangic)
        if deger is None:
            bulunan = [tur for tur, ikili in (("codex", codex_ikili(baslangic)),
                                               ("claude", claude_ikili(baslangic)))
                       if shutil.which(ikili)]
            secim = ("ORVANT_YURUTUCU=codex|claude ya da orvant.toml [yurutucu] "
                     "tur=... ile seç; komut: export ORVANT_YURUTUCU=claude "
                     "(kaynak: otomatik algılama)")
            if len(bulunan) == 1:
                return bulunan[0], f"algilandi:{bulunan[0]}"
            if bulunan:
                raise YurutucuSecimHatasi(f"iki yürütücü bulundu: {secim}")
            raise YurutucuSecimHatasi(
                f"yürütücü bulunamadı (codex/claude PATH'te veya ayarlı ikili yolunda yok): {secim}")
        kaynak = str(dosya)
    if deger not in YURUTUCU_TURLERI:
        raise AyarHatasi(f"{kaynak}: yürütücü türü {YURUTUCU_TURLERI} içinden olmalı: {deger!r}")
    return deger, kaynak


def yurutucu_turu(baslangic=None):
    """Açıkça seçilmiş veya otomatik algılanmış ``codex``/``claude``."""
    return yurutucu_turu_kaynakli(baslangic)[0]


def codex_cloud_ayarlari(baslangic=None):
    """Bulut işçisi için doğrulanmış ortam, taban dal ve boş-diff geri düşüşü."""
    ortam = os.environ.get("ORVANT_CODEX_CLOUD_ORTAM")
    if ortam:
        ortam = _metin(ortam, "ORVANT_CODEX_CLOUD_ORTAM")
    else:
        ortam, dosya = _dosyadan("codex_cloud", "ortam", baslangic)
        if ortam is not None:
            ortam = _metin(ortam, f"{dosya} [codex_cloud].ortam")
    dal, dosya = _dosyadan("codex_cloud", "dal", baslangic)
    uzak, uzak_dosya = _dosyadan("codex_cloud", "uzak", baslangic)
    alt_dizin, alt_dosya = _dosyadan("codex_cloud", "alt_dizin", baslangic)
    attempts, attempts_dosya = _dosyadan("codex_cloud", "attempts", baslangic)
    yoklama, yoklama_dosya = _dosyadan("codex_cloud", "yoklama_saniye", baslangic)
    geri, geri_dosya = _dosyadan("codex_cloud", "geri_dusus", baslangic)
    dal = _metin(dal, f"{dosya} [codex_cloud].dal") if dal is not None else "main"
    uzak = _metin(uzak, f"{uzak_dosya} [codex_cloud].uzak") if uzak is not None else "origin"
    if alt_dizin is None:
        alt_dizin = ""
    elif not isinstance(alt_dizin, str) or Path(alt_dizin).is_absolute() or ".." in Path(alt_dizin).parts:
        raise AyarHatasi(f"{alt_dosya} [codex_cloud].alt_dizin güvenli göreli yol olmalı")
    if attempts is None:
        attempts = 1
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise AyarHatasi(f"{attempts_dosya} [codex_cloud].attempts pozitif tamsayı olmalı")
    if yoklama is None:
        yoklama = 30
    if (type(yoklama) not in (int, float) or not math.isfinite(yoklama)
            or yoklama <= 0):
        raise AyarHatasi(f"{yoklama_dosya} [codex_cloud].yoklama_saniye pozitif sayı olmalı")
    geri = _metin(geri, f"{geri_dosya} [codex_cloud].geri_dusus") if geri is not None else None
    if geri not in (None, "codex"):
        raise AyarHatasi("[codex_cloud].geri_dusus yalnız 'codex' olabilir")
    if not ortam:
        raise AyarHatasi("codex-cloud için [codex_cloud].ortam veya ORVANT_CODEX_CLOUD_ORTAM gerekli")
    return {"ortam": ortam, "dal": dal, "uzak": uzak, "alt_dizin": alt_dizin.strip("/"),
            "attempts": attempts, "yoklama_saniye": yoklama, "geri_dusus": geri}


def kehanet_yalitimi(baslangic=None):
    """Kehanet OS yalıtım arka ucu: ``auto``, ``bwrap`` veya ``codex``."""
    deger, dosya = _dosyadan("kehanet", "yalitim", baslangic)
    if deger is None:
        return "auto"
    if deger not in ("auto", "bwrap", "codex"):
        raise AyarHatasi(f"{dosya} [kehanet].yalitim auto|bwrap|codex olmalı: {deger!r}")
    return deger


def claude_ikili_kaynakli(baslangic=None):
    if os.environ.get("ORVANT_CLAUDE"):
        return _metin(os.environ["ORVANT_CLAUDE"], "ORVANT_CLAUDE"), "ortam:ORVANT_CLAUDE"
    deger, dosya = _dosyadan("claude", "ikili", baslangic)
    if deger is not None:
        return _metin(deger, f"{dosya} [claude].ikili"), str(dosya)
    return CLAUDE_IKILI, "varsayilan"


def claude_ikili(baslangic=None):
    """Claude Code CLI komut adı veya yolu (varsayılan ``claude``)."""
    return claude_ikili_kaynakli(baslangic)[0]


def claude_model(baslangic=None):
    """Claude yürütücüsünün modeli; ayarlı değilse ``None`` (Claude Code varsayılanı)."""
    if os.environ.get("ORVANT_CLAUDE_MODEL"):
        return _metin(os.environ["ORVANT_CLAUDE_MODEL"], "ORVANT_CLAUDE_MODEL")
    deger, dosya = _dosyadan("claude", "model", baslangic)
    return None if deger is None else _metin(deger, f"{dosya} [claude].model")


def arastirma_konu_siniri(baslangic=None):
    """Çağrı başına konu bütçesi: ortam > [karsilama].arastirma_konu_siniri > 3."""
    ad = "ORVANT_ARASTIRMA_KONU_SINIRI"
    if ad in os.environ:
        try:
            deger = int(os.environ[ad])
        except ValueError as exc:
            raise AyarHatasi(f"{ad} pozitif tam sayı olmalı") from exc
    else:
        deger, _ = _dosyadan("karsilama", "arastirma_konu_siniri", baslangic)
        if deger is None:
            deger = 3
    if type(deger) is not int or deger < 1:
        raise AyarHatasi("araştırma konu sınırı pozitif tam sayı olmalı")
    return deger


def inceleme_bekleyen_siniri(baslangic=None):
    """Yeni üretim sınırı: [inceleme].bekleyen_sinir; varsayılan 2, 0 kapatır."""
    deger, _ = _dosyadan("inceleme", "bekleyen_sinir", baslangic)
    if deger is None:
        return 2
    if type(deger) is not int or deger < 0:
        raise AyarHatasi("[inceleme].bekleyen_sinir negatif olmayan tam sayı olmalı")
    return deger


def kota_esigi(baslangic=None):
    """G-162: surdur kota eşiği (yüzde): ortam > [operator].kota_esigi > None (kapalı; kullanıcı kararı)."""
    ad = "ORVANT_KOTA_ESIGI"
    if ad in os.environ:
        ham = os.environ[ad].strip()
        if ham.casefold() == "kapali":
            return None
        try:
            deger = float(ham)
        except ValueError as exc:
            raise AyarHatasi(f"{ad} 0 ile 100 arası sayı ya da 'kapali' olmalı") from exc
    else:
        deger, _ = _dosyadan("operator", "kota_esigi", baslangic)
        if deger is None:
            return None
        if type(deger) not in (int, float):
            raise AyarHatasi("[operator].kota_esigi 0 ile 100 arası sayı olmalı")
        deger = float(deger)
    if not 0 < deger <= 100:
        raise AyarHatasi("kota eşiği 0 ile 100 arası (0 hariç) olmalı")
    return deger


def kat_kipi(baslangic=None):
    """KAT geçiş kipi; 6a'da yalnız kapalı veya karar vermeyen gölge kip vardır."""
    deger, dosya = _dosyadan("kat", "kip", baslangic)
    if deger is None:
        return "golge"
    if deger == "otorite":
        raise AyarHatasi(f"{dosya} [kat].kip='otorite' adım 6a'da reddedilir")
    if deger not in ("kapali", "golge"):
        raise AyarHatasi(f"{dosya} [kat].kip 'kapali' veya 'golge' olmalı: {deger!r}")
    return deger


def depo_koku_kaynakli(baslangic=None):
    if os.environ.get("ORVANT_DEPO"):
        return _yol_coz(os.environ["ORVANT_DEPO"], None, "ORVANT_DEPO"), "ortam:ORVANT_DEPO"
    deger, dosya = _dosyadan("yollar", "depo", baslangic)
    if deger is not None:
        return _yol_coz(deger, dosya, f"{dosya} [yollar].depo"), str(dosya)
    # Kaynak ağaçta paket üstü, yayın yerleşiminde engine/ üstü git köküdür.
    motor = _PAKET.parent
    return (motor.parent if motor.name == "engine" else motor), "varsayilan"


def depo_koku(baslangic=None):
    """Orvant motorunun git deposu kökü (terfi karşılaştırması bunu kullanır)."""
    return depo_koku_kaynakli(baslangic)[0]


def _cekirdek_adaylari():
    return (_PAKET.parent.parent, _PAKET.parent)


def cekirdek_yolu_kaynakli(baslangic=None):
    if os.environ.get("ORVANT_CEKIRDEK"):
        return (_yol_coz(os.environ["ORVANT_CEKIRDEK"], None, "ORVANT_CEKIRDEK"),
                "ortam:ORVANT_CEKIRDEK")
    deger, dosya = _dosyadan("yollar", "cekirdek", baslangic)
    if deger is not None:
        return _yol_coz(deger, dosya, f"{dosya} [yollar].cekirdek"), str(dosya)
    for aday in _cekirdek_adaylari():
        if (aday / "skills" / "orvant" / "SKILL.md").is_file():
            return aday, "varsayilan:bulundu"
    return None, "varsayilan:yok"


def cekirdek_yolu(baslangic=None):
    """Orvant proje kaydı becerisinin (``skills/orvant``) depo kökü; bulunamazsa None."""
    return cekirdek_yolu_kaynakli(baslangic)[0]


def ozet(baslangic=None):
    """Etkin ayarlar ve her değerin kaynağı (tanı için)."""
    modeller = {}
    for rol in ROLLER:
        deger, kaynak = model_kaynakli(rol, baslangic)
        modeller[rol] = {"deger": deger, "kaynak": kaynak}
    ikili, ikili_k = codex_ikili_kaynakli(baslangic)
    depo, depo_k = depo_koku_kaynakli(baslangic)
    cekirdek, cekirdek_k = cekirdek_yolu_kaynakli(baslangic)
    try:
        yurutucu = dict(zip(("deger", "kaynak"), yurutucu_turu_kaynakli(baslangic)))
    except AyarHatasi as exc:
        # Tanı komutu tam da bozuk/belirsiz ayarı gösterebilmelidir.
        yurutucu = {"deger": None, "kaynak": "hata", "hata": str(exc)}
    return {
        "dosyalar": [str(p) for p in ayar_dosyalari(baslangic)],
        "modeller": modeller,
        "codex_ikili": {"deger": ikili, "kaynak": ikili_k},
        "yurutucu": yurutucu,
        "claude_ikili": dict(zip(("deger", "kaynak"), claude_ikili_kaynakli(baslangic))),
        "depo_koku": {"deger": str(depo), "kaynak": depo_k},
        "cekirdek_yolu": {"deger": str(cekirdek) if cekirdek else None, "kaynak": cekirdek_k},
    }


def proje_kaydi_script():
    """Depo ve yayın yerleşimindeki tek proje kaydı yazıcısını bulur."""
    kok = depo_koku()
    for yol in (kok / 'araclar/yayin/sablon/skills/orvant/scripts/project.py',
                kok / 'skills/orvant/scripts/project.py'):
        if yol.is_file():
            return yol
    raise ValueError('proje kaydı skill yazıcısı bulunamadı')


def main(argv=None):
    """``python3 -m orvant_op.ayarlar``: etkin ayarları JSON olarak yazar."""
    del argv
    try:
        print(json.dumps(ozet(), ensure_ascii=False, indent=2))
    except AyarHatasi as exc:
        print(f"hata: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())


def koordinasyon_kaynaklari(baslangic=None):
    """[koordinasyon].kaynaklar girdilerini doğrular ve yollarını ayar dosyasına göre çözer."""
    deger, dosya = _dosyadan("koordinasyon", "kaynaklar", baslangic)
    if deger is None:
        return []
    if not isinstance(deger, list):
        raise AyarHatasi("[koordinasyon].kaynaklar tablo listesi olmalı")
    zorunlu = {"yol", "bicim", "id_alani", "dosyalar_alani", "durum_alani",
               "aktif_durumlar", "yok_sayilan_durumlar"}
    sonuc = []
    for no, kaynak in enumerate(deger):
        if not isinstance(kaynak, dict) or not zorunlu.issubset(kaynak):
            raise AyarHatasi(f"[koordinasyon].kaynaklar[{no}] eksik/geçersiz")
        if kaynak["bicim"] not in ("json", "jsonl"):
            raise AyarHatasi("koordinasyon biçimi json veya jsonl olmalı")
        for ad in ("id_alani", "dosyalar_alani", "durum_alani"):
            _metin(kaynak[ad], f"koordinasyon.{ad}")
        for ad in ("aktif_durumlar", "yok_sayilan_durumlar"):
            if not isinstance(kaynak[ad], list) or not all(isinstance(x, str) for x in kaynak[ad]):
                raise AyarHatasi(f"koordinasyon.{ad} metin listesi olmalı")
        kayit = dict(kaynak)
        kayit["yol"] = _yol_coz(kaynak["yol"], dosya, "koordinasyon.yol")
        sonuc.append(kayit)
    return sonuc
