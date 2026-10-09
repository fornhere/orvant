"""Kapı sözleşmesine dokunmayan, deterministik arıza sınıflandırması."""

import hashlib
import json
import re
from math import ceil

from orvant_op.mimar.kusurlu import negatif_kontrol

SINIFLAR = (
    "kota_doldu", "butce_modeli", "plan_sozlesmesi", "sozlesme_anlami", "yasam_dongusu", "baglam_eksik", "kurulum",
    "ortam_gozlem", "arac_eksik", "kabul_celiskisi", "girdi_bekleme", "kod_hatasi", "gecici_altyapi",
    "dogrulayici_kusuru", "orvant_kusuru", "gecici_artik", "isci_goal_kapanmadi", "bulut_bos_dondu", "bilinmeyen",
    "urun_karari_eksik", "flaky_test", "outcome_unknown",
)
EYLEMLER = (
    "yeniden_denetle", "yeniden_dene", "yeniden_planla", "girdi_bekle",
    "yetki_bekle", "kota_bekle", "yukselt", "uzlastir",
)


def _ozet(veri):
    return json.dumps(veri, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize(hatalar):
    satirlar = []
    for hata in hatalar:
        for satir in str(hata).splitlines():
            satir = re.sub(r"\x1b\[[0-9;]*m", "", satir).strip().casefold()
            satir = re.sub(r"\s+", " ", satir)
            if satir:
                satirlar.append(satir[:500])
    return sorted(set(satirlar))


def _var(metin, *sozler):
    return any(soz in metin for soz in sozler)


def _istisna_normalize(metin):
    metin = str(metin).casefold()
    for kalip, yerine in (
        (r"[\w.+-]+@[\w.-]+", "<eposta>"),
        (r"(?<!\w)/(?:[^\s\"'<>;:,]+)", "<yol>"),
        (r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "<uuid>"),
        (r"\b(?:0x[0-9a-f]+|[0-9a-f]{7,})\b", "<hex>"),
        (r"\d+", "<sayi>"),
    ):
        metin = re.sub(kalip, yerine, metin)
    return " ".join(metin.split())


def _arac_sinyali(metin):
    if "yerel_yontem_yok" in metin:
        return True
    # Dosya adları araç adı sayılmaz; komşu cümledeki eksiklik de taşınmaz.
    # "yok" yalnız araç sözcüğüne bitişikse sayılır ("komut çıktısında hata yok" araç eksikliği değildir);
    # diğer eksiklik fiilleri araç sözcüğünden sonra, aynı cümlede ve yakında gelmeli.
    arac = r"\b(?:araç\w*|aracı\w*|yöntem\w*|model\w*|tool\w*|method\w*|command\w*|komut\w*|asr|whisper\w*|transkripsiyon\w*)"
    eksik = (r"(?:bulunamad\w*|bulunmad\w*|erişilemiyor|erişilemez|mevcut değil|kurulu değil|not found|"
             r"not installed|permission denied|read-only file system)")
    for cumle in re.split(r"[;\n.!?]", metin):
        if (re.search(arac + r"\s+yok\b", cumle) or
                re.search(arac + r"[^;\n.!?]{0,80}?" + eksik, cumle)):
            return True
    return False


def _arac_adaylari(gorev, envanter):
    # Yerel import, S3 → S4 modül döngüsünü önler; puanlama S3 ile ortaktır.
    from orvant_op.yurutme.akis import _kelimeler, _envanter_puani
    anahtar = _kelimeler(" ".join([gorev.get("baslik", ""), gorev.get("amac", ""),
                                    *gorev.get("yazilabilir", [])]))
    ilgili = sorted(((_envanter_puani(anahtar, k), k) for k in envanter
                     if k.get("mevcut") is not False), key=lambda x: x[0], reverse=True)
    esik = max(2, ceil(ilgili[0][0] / 2)) if ilgili else 2
    return [{alan: k.get(alan) for alan in ("id", "ad", "yol", "komut_ornegi")}
            for puan, k in ilgili if puan >= esik][:3]


def teshis_et(baglam):
    """Salt veri alır; planı, kabulü ve yazma iznini değiştirmez.

    baglam: makbuz, gorev, plan_ozeti, kararlar, izinler, girdi,
    onceki_teshisler, toplam_tokens, envanter, isci_kostu,
    istisna_turu ve orvant_surumu alanlarını taşıyabilir.
    """
    # Yerel import teşhis ↔ yürütme paket başlangıç döngüsünü önler.
    from orvant_op.yurutme.gecis import teshis_eylemi

    makbuz = baglam.get("makbuz") or {}
    gorev = baglam.get("gorev") or {}
    hatalar = list(makbuz.get("hatalar") or [])
    komutlar = makbuz.get("komut_sonuclari") or []
    goal = makbuz.get("goal") or {}
    durum = goal.get("status") or "hedef_yok"
    ozet = " ".join(_normalize([*hatalar, makbuz.get("isci_ozeti") or "", baglam.get("istisna") or ""]))
    ihlaller = makbuz.get("kapsam_ihlalleri") or []
    degisen = makbuz.get("degisen_dosyalar") or []
    oncekiler = [x for x in baglam.get("onceki_teshisler") or []
                if x.get("gorev", gorev.get("id")) == gorev.get("id")]
    onarimlar = [x for x in oncekiler if x.get("sinif") != "orvant_kusuru"]
    surum = baglam.get("orvant_surumu")
    kehanet = makbuz.get("kehanet_sonucu") or {}
    negatif = negatif_kontrol(kehanet) if kehanet.get("gecti") is False else {}
    adaylar = _arac_adaylari(gorev, baglam.get("envanter") or [])
    arac_sinyali = _arac_sinyali("\n".join([*hatalar, makbuz.get("isci_ozeti") or ""]).casefold())
    kotu_komut = [k for k in komutlar if k.get("exit_code") != 0 or k.get("zaman_asimi")]
    komut_metni = " ".join(str(k.get("cikti_kuyrugu") or "").casefold() for k in komutlar)
    hata_metni = " ".join(str(k.get("cikti_kuyrugu") or "").casefold() for k in kotu_komut)
    manifest = baglam.get("yetenek_manifesti") or makbuz.get("yetenek_manifesti") or {}
    gozlem_ortami = manifest.get("gozlem_ortami") or {}
    kapi_ortami = manifest.get("kapi_ortami") or {}
    ortam_farki = (gozlem_ortami.get("ortam") and kapi_ortami.get("ortam")
                   and gozlem_ortami.get("ortam") != kapi_ortami.get("ortam"))
    acik_kararlar = [k for k in baglam.get("kararlar") or []
                     if k.get("durum") != "cozuldu" and k.get("sahip", "kullanici") == "kullanici"]
    bekleyen_kararlar = set(gorev.get("bekleyen_kararlar") or [])
    girdi_istegi = _var(ozet, "girdi", "dosya gerekli", "input required", "provide")
    urun_karari = next((k for k in acik_kararlar if not girdi_istegi and (
        k.get("id") in bekleyen_kararlar
        or (k.get("id") and re.search(
            rf"(?<![\w-]){re.escape(str(k['id']).casefold())}(?![\w-])", ozet)))), None)
    goal = makbuz.get("goal") or {}
    kosu = baglam.get("kosu") or {}
    kararsiz_kontroller = [x for x in makbuz.get("kapi_tekrarlari") or []
                           if len(set(x.get("sonuc_dizisi") or [])) > 1]
    if makbuz.get("outcome_unknown") is True and bool(
            makbuz.get("bulut_gorev_id") or makbuz.get("bulut_gorev_url")):
        sinif, eylem, hipotez = ("outcome_unknown", "uzlastir",
                                  "Yürütücü sonucu kesinleşmeden aynı görev ve deneme yeniden başlatılamaz")
    elif makbuz.get("kota_doldu") is True or goal.get("status") == "usage_limited" or kosu.get("kota_doldu") is True:
        sinif, eylem, hipotez = "kota_doldu", "kota_bekle", "Model sağlayıcısı kullanım kotası doldu"
    elif kararsiz_kontroller:
        sinif, eylem = "flaky_test", "yukselt"
        hipotez = "Aynı ağaç ve kapı komutunda karışık sonuç: " + ", ".join(
            str(x.get("id") or x.get("komut")) for x in kararsiz_kontroller)
    elif (baglam.get("isci_kostu") is False
            and _var(ozet, "workspace setup failed", "çalışma alanı kurulumu başarısız")):
        sinif, eylem, hipotez = "kurulum", "yeniden_dene", "Kurulum komutu işçi başlamadan başarısız oldu"
    elif _var(ozet, "bulut_bos_dondu", "bulut boş döndü"):
        sinif, eylem, hipotez = "bulut_bos_dondu", "yeniden_dene", "Codex Cloud READY durumunda değişiklik üretmedi"
    elif _var(ozet, "claude code oturumu geçersiz", "codex oturumu geçersiz"):
        sinif, eylem, hipotez = "ortam_gozlem", "yetki_bekle", "Model CLI kimlik doğrulaması veya oturumu geçersiz"
    elif (baglam.get("istisna") and baglam.get("isci_kostu") is False
            and baglam.get("istisna_turu") not in ("ValueError", "TimeoutExpired", "YurutucuZamanAsimi")
            and not _var(str(baglam["istisna"]).casefold(), "zaman aşımı", "timed out", "timeout")):
        sinif, eylem, hipotez = "orvant_kusuru", "yukselt", "İşçi başlamadan Orvant iç hatası: " + str(baglam["istisna"])
    elif baglam.get("istisna") and _var(ozet, "conflict", "çakışma", "merge failed"):
        sinif, eylem, hipotez = "yasam_dongusu", "yeniden_planla", "Birleştirme çakışması var"
    elif ihlaller:
        inceleme = baglam.get("artik_incelemesi")
        turetilmis = all("__pycache__" in p.split("/") or p.endswith((".pyc", ".pyo"))
                         for p in ihlaller)
        if inceleme is not None:
            adaylar_artik = {a["yol"] for a in inceleme.get("dosyalar", [])}
            kapsam_hatalari = {f"Kapsam dışı dosya: {p}" for p in ihlaller}
            kabul_ids = {k["id"] for k in gorev.get("kabul", []) if k["tur"] == "komut"}
            if (inceleme.get("uygun") and adaylar_artik == set(ihlaller)
                    and not kotu_komut and kehanet.get("gecti") is not False
                    and kabul_ids <= {k.get("id") for k in komutlar}
                    and set(hatalar) == kapsam_hatalari and durum == "complete"
                    and not baglam.get("istisna")):
                sinif, eylem, hipotez = "gecici_artik", "yeniden_denetle", "Yalnız küçük, izlenmeyen kapsam dışı dosyalar var"
            else:
                sinif, eylem, hipotez = "sozlesme_anlami", "yukselt", "Kapsam ihlalleri güvenle temizlenemiyor veya başka hata var"
        elif turetilmis:
            sinif, eylem, hipotez = "sozlesme_anlami", "yeniden_denetle", "Yalnız türetilmiş dosyalar kapsam dışında"
        else:
            sinif, eylem, hipotez = "sozlesme_anlami", "yeniden_dene", "Gerçek kapsam dışı dosyalar var"
    elif not kotu_komut and negatif.get("durum") == "kehanet_hatasi":
        sinif, eylem, hipotez = "dogrulayici_kusuru", "yeniden_denetle", "Bağımsız kehanetin kendi çalışma hatası"
    elif arac_sinyali and adaylar and (not kotu_komut or _var(ozet, "permission denied", "read-only file system")):
        sinif, eylem, hipotez = "arac_eksik", "yeniden_planla", "Araç envanteri veya kurulum yetkisi gerekli"
    elif _var(ozet, "doğrulanamadı", "görünmüyor", "not visible") and _var(ozet, "sandbox", "aygıt", "device"):
        sinif, eylem = "ortam_gozlem", "yeniden_denetle"
        hipotez = ("işçi/host ortamı ile kapı sandbox ortamı farklıdır; "
                   "kapı gözlemi host kanıtını çürütemez" if ortam_farki else
                   "Sandbox gözlemi dış ortam kanıtı değildir")
    elif _var(ozet, "permission denied", "sandbox denied", "outside writable", "read-only file system", "operation not permitted"):
        sinif, eylem, hipotez = "ortam_gozlem", teshis_eylemi("yetki_bekleme"), "Ortam veya yazma yetkisi engeli"
    elif kotu_komut and (_var(hata_metni, "can't open file", "invalid choice", "unrecognized arguments", "usage:")
                           or re.search(r"(?:\.py|\.sh|/[^\s:]+): (?:no such file or directory|not found)", hata_metni)
                           or ("no such file or directory" in hata_metni and
                               any(re.search(r"(?:\.py|\.sh)(?:\s|$)", str(k.get("komut") or ""))
                                   for k in kotu_komut))):
        sinif, eylem, hipotez = "plan_sozlesmesi", "yeniden_planla", "Kabul komutu planlanan dosya veya arayüzle uyuşmuyor"
    elif _var(komut_metni, "modulenotfounderror", "ran 0 tests", "no tests", "no tests ran", "collected 0 items", "test toplanmadı"):
        sinif, eylem, hipotez = "dogrulayici_kusuru", "yukselt", "Kabul doğrulayıcısı çalışmadı veya test toplamadı"
    elif ((_var(ozet, "tahmin", "estimate") and
           _var(ozet, "ölçülmüş değil", "ölçülmedi", "not measured", "unmeasured")) or
          (_var(ozet, "kabul ölçüt", "acceptance criter") and
           _var(ozet, "kullanıcı karar", "user decision", "user's decision") and
           _var(ozet, "çeliş", "conflict", "contradict"))):
        sinif, eylem, hipotez = "kabul_celiskisi", teshis_eylemi("kabul_celiskisi"), "Kabul ölçütü ile kullanıcı kararı veya ölçüm kanıtı çelişiyor"
    # Tamamlandı diyen, kapı/hata kanıtı taşımayan yalın bir özet onarıma yetmez.
    elif ((arac_sinyali and not kotu_komut and (hatalar or komutlar or durum == "blocked" or kehanet.get("gecti") is False
                                              or "yerel_yontem_yok" in ozet)) or
          durum == "blocked" and _var(ozet, "araç bulunamadı", "aracı bulunamadı", "aracı yok", "araç yok",
                                      "asr aracı", "whisper yok", "command not found", "tool not found",
                                      "not installed", "kurulu değil")):
        sinif, eylem, hipotez = "arac_eksik", "yeniden_planla", "Araç envanteri veya kurulum yetkisi gerekli"
    elif durum == "blocked" and urun_karari:
        sinif, eylem, hipotez = "urun_karari_eksik", teshis_eylemi("girdi_bekleme"), "Açık ürün kararı kullanıcı seçimi bekliyor"
    elif durum == "blocked" and baglam.get("istenen_bagimli_ciktilar"):
        sinif, eylem, hipotez = "baglam_eksik", "yeniden_dene", "İstenen girdi kabul edilmiş bağımlı çıktı olarak mevcut"
    elif durum == "blocked" and _var(ozet, "kullanıcı", "girdi", "dosya gerekli", "karar gerekli", "input required", "provide"):
        sinif, eylem, hipotez = "girdi_bekleme", teshis_eylemi("girdi_bekleme"), "İşçi dış girdi beklediğini bildirdi"
    elif (_var(ozet, "rate limit", "network", "connection", "timed out", "timeout", "zaman aşımı", "temporary failure")
          or re.search(r"\b(?:http|status(?: code)?)[ :#-]*429\b|\b429 too many requests\b", ozet)
          or any(k.get("zaman_asimi") for k in komutlar)):
        sinif, eylem, hipotez = "gecici_altyapi", "yeniden_dene", "Geçici altyapı veya zaman aşımı"
    elif durum == "budget_limited":
        sinif, eylem, hipotez = "butce_modeli", "yeniden_planla", "Kümülatif token bütçesi tükendi"
        # İlerleme yalnız işçi özetinden okunur; kabul hatası veya görev talebi kanıt değildir.
        ilerleme = str(makbuz.get("isci_ozeti") or "").casefold().replace("i\u0307", "i")
        ilerleme_var = any(
            re.search(r"\bkısmen\b|\bilk\s+\d+(?:[.,]\d+)?\s+saniye\w*\b"
                      r"|\bdevam\s+(?:ediyor|edildi|edilecek)\b", cumle)
            and not re.search(r"\b(?:değil|yok|başlamadı|çözülemedi|tamamlanmadı|işlenemedi)\b", cumle)
            for cumle in re.split(r"[;\n]", ilerleme))
        if ilerleme_var:
            hipotez += "; kısmi ilerleme var: yeniden_dene öncesinde butce_artir önerilir"
            # Doğrudan yeniden_dene eski bütçeyle işçiyi başlatır; mevcut S3 yolu
            # önce yeniden_planla ile bütçeyi değiştirir. Onarım sınırları korunur.
    elif _var(ozet, "bağlam eksik", "context missing", "bilinen olgu eksik", "missing fact"):
        sinif, eylem, hipotez = "baglam_eksik", "yeniden_dene", "Göreve gerekli olgu aktarılmamış"
    elif kotu_komut:
        sinif, eylem, hipotez = "kod_hatasi", "yeniden_dene", "Kabul komutu gerçek hata verdi"
    elif (durum in ("active", "paused", "usage_limited", "okuma_hatasi") and degisen
          and not kotu_komut and kehanet.get("gecti") is True and not ihlaller):
        sinif, eylem = "isci_goal_kapanmadi", "yeniden_dene"
        hipotez = "İşçi değişiklik yaptı ve bağımsız kapı geçti; yalnız goal kapanışı eksik"
    elif durum in ("active", "paused", "hedef_yok", "usage_limited", "okuma_hatasi", "blocked"):
        sinif, eylem, hipotez = "yasam_dongusu", "yukselt", "Goal yaşam döngüsü belirsiz veya tamamlanmadı"
    elif durum in ("complete", "bulut_tamamlandi") and negatif.get("durum") == "anlamli":
        # G-171 (gerçek koşu T14-2, örnek T01-1): kehanet çalıştı ve çıktıyı reddetti; red işçi istemine
        # önceki hata olarak gider. Onarım sınırları aşağıda aynen uygulanır.
        sinif, eylem = "kod_hatasi", "yeniden_dene"
        hipotez = "Bağımsız kehanet çıktıyı reddetti: " + ", ".join(negatif["kanit"])
    else:
        sinif, eylem, hipotez = "bilinmeyen", "yukselt", "Kanıtlar güvenli otomatik eylemi belirlemiyor"

    plan_ozeti = baglam.get("plan_ozeti") or {"surum": baglam.get("plan_revizyon")}
    baglanti = {"sinif": sinif, "hatalar": _normalize(hatalar or [ozet]),
                "plan": plan_ozeti, "kararlar": baglam.get("kararlar") or [],
                "izinler": baglam.get("izinler") or []}
    imza_verisi = baglanti
    if sinif == "orvant_kusuru":
        imza_verisi = {"sinif": sinif, "istisna_turu": baglam.get("istisna_turu"),
                       "istisna": _istisna_normalize(baglam["istisna"])}
    elif sinif == "dogrulayici_kusuru" and negatif.get("durum") == "kehanet_hatasi":
        imza_verisi = {"sinif": sinif, "kontroller": sorted(
            (str(k.get("ad", "")), _normalize([k.get("ayrinti", "")]))
            for k in kehanet.get("kontroller") or [] if k.get("gecti") is False),
            "kanit": _normalize(negatif["kanit"])}
    imza = hashlib.sha256(_ozet(imza_verisi).encode("utf-8")).hexdigest()[:16]
    kanit_ozeti = hashlib.sha256(_ozet({"plan": plan_ozeti, "kararlar": baglanti["kararlar"],
                                      "izinler": baglanti["izinler"], "girdi": baglam.get("girdi"),
                                      "degisen": degisen}).encode("utf-8")).hexdigest()[:16]
    if eylem == "yeniden_dene" and (len([x for x in onarimlar if x.get("eylem") == "yeniden_dene"]) >= 2
                                     or (sinif == "sozlesme_anlami" and any(
                                         x.get("sinif") == sinif and x.get("eylem") == "yeniden_dene" for x in onarimlar))):
        eylem = "yukselt"
        hipotez += "; onarım sınırı doldu"
    if eylem == "yeniden_planla" and any(x.get("eylem") == "yeniden_planla" for x in onarimlar):
        eylem = "yukselt"
        hipotez += "; yeniden planlama zaten önerildi"
    if eylem in ("yeniden_dene", "yeniden_planla") and any(
        x.get("imza") == imza and x.get("kanit_ozeti") == kanit_ozeti for x in onarimlar):
        eylem = "yukselt"
        hipotez += "; aynı imza ve yeni kanıt yok"
    if sinif == "dogrulayici_kusuru" and eylem == "yeniden_denetle" and any(
            x.get("sinif") == sinif and x.get("imza") == imza
            and x.get("kehanet_sha256") == kehanet.get("sha256")
            and x.get("orvant_surumu") == surum for x in oncekiler):
        eylem = "yukselt"
        hipotez += "; doğrulayıcı değişmedi"
    if sinif == "arac_eksik" and adaylar:
        hipotez += "; mevcut adaylar: " + ", ".join(f"{a['id']} ({a['yol']})" for a in adaylar)
    kanitlar = [{"kaynak": "makbuz", "alan": "goal", "deger": durum},
                {"kaynak": "makbuz", "alan": "hatalar", "deger": hatalar},
                {"kaynak": "makbuz", "alan": "komut_sonuclari", "deger": kotu_komut},
                {"kaynak": "makbuz", "alan": "kapsam_ihlalleri", "deger": ihlaller}]
    if sinif == "ortam_gozlem" and ortam_farki:
        kanitlar.append({"kaynak": "yetenek_manifesti", "alan": "ortam_farki",
                        "deger": manifest})
    if baglam.get("istisna"):
        kanitlar.append({"kaynak": "s3", "alan": "istisna", "deger": baglam["istisna"]})
    if sinif == "kurulum":
        kanitlar.append({"kaynak": "s3", "alan": "isci_kostu", "deger": False})
    if baglam.get("artik_incelemesi") is not None:
        kanitlar.append({"kaynak": "s3", "alan": "artik_incelemesi", "deger": baglam["artik_incelemesi"]})
    if sinif == "flaky_test":
        kanitlar.append({"kaynak": "makbuz", "alan": "kapi_tekrarlari", "deger": kararsiz_kontroller})
    if sinif == "baglam_eksik" and baglam.get("istenen_bagimli_ciktilar"):
        kanitlar.append({"kaynak": "s3", "alan": "istenen_bagimli_ciktilar",
                        "deger": baglam["istenen_bagimli_ciktilar"]})
    sonuc = {"belirti": "; ".join(hatalar) or ozet or durum, "sinif": sinif,
             "hipotez": hipotez, "kanitlar": kanitlar, "eylem": eylem,
             "gerekce": hipotez, "imza": imza, "kanit_ozeti": kanit_ozeti,
             "orvant_surumu": surum}
    if sinif == "urun_karari_eksik":
        sonuc["karar_id"] = urun_karari["id"]
    if sinif == "kurulum":
        sonuc.update(oneri="kurulum komutunu düzelt ve yeniden dene", deneme_iadesi=True)
    if sinif == "butce_modeli" and ilerleme_var:
        from orvant_op.butce import butce_tabani
        eski = (gorev.get("butce") or {}).get("token", 0)
        taban = butce_tabani(gorev=gorev)
        hedef = 2 * max(eski, taban, goal.get("token_budget") or 0,
                        goal.get("tokens_used") or 0)
        sonuc["oneri"] = {"islem": "butce_artir", "gorev": gorev.get("id"),
                          "token": hedef - eski, "deneme": 1,
                          "gerekce": "Kısmi ilerlemeyi koruyarak daha yüksek bütçeyle devam"}
        sonuc["sonraki_eylem"] = "yeniden_dene"
        sonuc["kanitlar"].append({"kaynak": "makbuz", "alan": "isci_ozeti",
                                  "deger": makbuz["isci_ozeti"]})
    if sinif == "dogrulayici_kusuru":
        sonuc["bakim_onerisi"] = "dogrulayici_bakim"
        sonuc.update(kehanet_sha256=kehanet.get("sha256"), kehanet_kanitlari=negatif.get("kanit", []))
    if sinif == "orvant_kusuru":
        sonuc["deneme_iadesi"] = True
        # Operatör soruyu 240 karakterde keser; iade ve yeniden deneme bilgisi sığmalı.
        kisa_istisna = " ".join(f"{baglam.get('istisna_turu')}: {baglam['istisna']}".split())
        if len(kisa_istisna) > 80:
            kisa_istisna = kisa_istisna[:77] + "..."
        sonuc["kullanici_sorusu"] = (f"Orvant iç hatası ({kisa_istisna}); "
            "işçi koşmadı, deneme hakkı iade edildi. Orvant düzeltilip sürümü değişince görev kendiliğinden yeniden denenir.")
    if sinif == "isci_goal_kapanmadi":
        sonuc["oneri"] = "yeniden_dene_net_talimat"
    if sinif == "arac_eksik":
        sonuc["oneri"] = ({"islem": "yetki_istegi_ekle", "yetki_eylemi": "kurulum",
            "arac_adaylari": adaylar, "ayrinti": ", ".join(str(a["yol"] or a["id"]) for a in adaylar)
            + " konumlarına ve gerekli beceri/model yollarına okuma, gerekli önbelleğe yazma erişimi; ağ yok"}
            if adaylar else {"islem": "gorev_ekle", "tur": "kurulum",
                            "ayrinti": "Eksik araç/model için yerel kurulum ve erişim hazırlığı görevi ekle; ağ yok"})
        sonuc["oneri"]["not"] = "S4/Orvant yetki vermez; kullanıcı onayı gerekir"
        from orvant_op.mimar.arac_yetki import gereksinimler, kapsayan_istek_ids
        aday_ids = {a["id"] for a in adaylar}
        kayitlar = [k for k in baglam.get("envanter") or [] if k["id"] in aday_ids]
        gerekenler = [gereksinimler(k) for k in kayitlar]
        gereksinim = {"yollar": [], "ag": "gerekmez", "eylem": "kurulum"}
        for g in gerekenler:
            for y in g["yollar"]:
                if y not in gereksinim["yollar"]:
                    gereksinim["yollar"].append(y)
            if g["ag"] == "gerekir":
                gereksinim["ag"] = "gerekir"
        # Görev tek bir araca muhtaç: zayıf ikinci aday (T17'de ornek-yayin) birleşik
        # gereksinimi şişirip Y06'yı kapsamaz gösteriyordu; kapsama aday başına sınanır.
        aday_gereksinimleri = [{"envanter_id": k["id"], "yollar": g["yollar"], "ag": g["ag"],
                                "eylem": "kurulum"} for k, g in zip(kayitlar, gerekenler)
                               if g["yollar"] or g["ag"] == "gerekir"]
        izinler = [i for i in baglam.get("izinler") or [] if i.get("durum") != "reddedildi"]
        sonuc["oneri"].update(gereksinim=gereksinim, aday_gereksinimleri=aday_gereksinimleri,
                              mevcut_istek_ids=[i["id"] for i in izinler])
        # Ayrıntısız S3 özeti kapsam kanıtı değildir; kararı S2 tam planla verir.
        for durum, islem, not_ in (
                ("verildi", "yetki_istegi_gereksiz", "İzin var; sorun yetki değil"),
                ("acik", "mevcut_yetkiyi_bekle", "Kapsayan mevcut istek için kullanıcı onayı bekleniyor")):
            adaylar_ = [i for i in izinler if i.get("durum") == durum and "ayrinti" in i]
            kapsayan = next((ids for g in aday_gereksinimleri
                             if (ids := kapsayan_istek_ids(adaylar_, g))), [])
            if kapsayan:
                sonuc["oneri"] = {"islem": islem, "yetki_istek_ids": kapsayan, "not": not_}
                break
    if sinif == "kabul_celiskisi":
        sonuc["kullanici_sorusu"] = "Kabul ölçütü tahmini kabul edecek şekilde değiştirilsin mi, yoksa gerekli ölçüm yapılsın mı?"
    if sinif == "flaky_test":
        sonuc["kullanici_sorusu"] = "Kararsız kapı kontrolünü inceleyin: " + ", ".join(
            str(x.get("id") or x.get("komut")) for x in kararsiz_kontroller)
    if sinif == "outcome_unknown":
        ham_hata = " ".join(map(str, hatalar))
        kimlik = next(iter(re.findall(r"bulut görevi ([\w.-]+)", ham_hata, re.I)), None) or makbuz.get("thread_id") or "bilinmiyor"
        url = makbuz.get("bulut_gorev_url") or next(iter(re.findall(r"https?://\S+", ham_hata)), None)
        komut = makbuz.get("uzlastirma_komutu") or f"codex cloud status {kimlik}"
        sonuc["kullanici_sorusu"] = (f"Bulut görevi {kimlik} uzlaştırılmalı"
            + (f" ({url.rstrip('.,;)')})" if url else "")
            + f". Kontrol: {komut}. Sonucu doğrulayıp görevi açmak ister misiniz?")
        sonuc.update(bulut_gorev_id=kimlik, bulut_gorev_url=url,
                     uzlastirma_komutu=komut)
    if sinif == "gecici_altyapi" and eylem == "yeniden_dene":
        sonuc["bekleme_saniye"] = 1
    return sonuc
