"""Onaylı, yapılandırılmış kabul ölçütlerini KAT v1'e derler.

Kabul metni yalnız insan-okur izidir. İşlem veya değer metinden çıkarılmaz;
yapılandırılmış ölçüt yoksa derleme kapalı kalır.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from orvant_op.kabul_kat import KatHatasi, _tipli_esit, dogrula, kat_hash, yorumla


def _kabuller(kabuller):
    if not isinstance(kabuller, list):
        raise KatHatasi("kabuller liste olmalı")
    sonuc = {}
    for kabul in kabuller:
        if (not isinstance(kabul, dict) or not {"id", "metin"} <= set(kabul)
                or not isinstance(kabul["id"], str) or not isinstance(kabul["metin"], str)
                or not kabul["id"] or kabul["id"] in sonuc):
            raise KatHatasi("kabul kimlikleri ve metinleri geçerli ve benzersiz olmalı")
        sonuc[kabul["id"]] = kabul
    return sonuc


def _ciktilar(sozlesme):
    if not isinstance(sozlesme, dict) or set(sozlesme) != {"dosyalar", "arac_alanlari"}:
        raise KatHatasi("çıktı sözleşmesi kehanet şeması biçiminde olmalı")
    if not isinstance(sozlesme["dosyalar"], list) or not isinstance(sozlesme["arac_alanlari"], list):
        raise KatHatasi("çıktı sözleşmesi listeleri geçersiz")
    sonuc = {}
    for dosya in sozlesme["dosyalar"]:
        if not isinstance(dosya, dict) or set(dosya) != {"yol", "bicim", "aciklama", "alanlar"}:
            raise KatHatasi("çıktı dosyası kehanet şeması biçiminde olmalı")
        yol = dosya["yol"]
        if not isinstance(yol, str) or not yol or yol in sonuc or not isinstance(dosya["alanlar"], list):
            raise KatHatasi("çıktı yolu geçersiz veya yinelenmiş")
        alanlar = {}
        for alan in dosya["alanlar"]:
            if not isinstance(alan, dict) or set(alan) != {"ad", "tip", "zorunlu", "aciklama"}:
                raise KatHatasi("çıktı alanı kehanet şeması biçiminde olmalı")
            if not isinstance(alan["ad"], str) or not alan["ad"] or alan["ad"] in alanlar:
                raise KatHatasi("çıktı alanı geçersiz veya yinelenmiş")
            alanlar[alan["ad"]] = alan["tip"]
        sonuc[yol] = alanlar
    return sonuc


def _gozlem_anahtari(gozlem, ciktilar):
    if not isinstance(gozlem, dict) or set(gozlem) != {"kaynak", "yol", "alan"}:
        raise KatHatasi("gözlem tipli kaynak, yol ve alan taşımalı")
    if gozlem["kaynak"] != "cikti":
        raise KatHatasi("yalnız sözleşmeli çıktı gözlemi derlenebilir")
    yol, alan = gozlem["yol"], gozlem["alan"]
    if yol not in ciktilar or not isinstance(alan, str) or not alan.startswith("/"):
        raise KatHatasi("gözlem sözleşmeli bir çıktı alanına bağlı değil")
    alan_adi = alan[1:].replace("~1", "/").replace("~0", "~")
    if not alan_adi or "/" in alan_adi or alan_adi not in ciktilar[yol]:
        raise KatHatasi("gözlem alanı çıktı sözleşmesinde yok")
    return f"cikti:{yol}#{alan}"


def _yapisal(kabul, ciktilar):
    yapisal = kabul.get("yapisal")
    if not isinstance(yapisal, dict):
        raise KatHatasi("yapısal ölçüt yok — eski kehanet yolu")
    if set(yapisal) != {"alan_yolu", "islem", "beklenen_json"} or "#" not in yapisal["alan_yolu"]:
        raise KatHatasi("yapısal ölçüt alanları geçersiz")
    yol, alan_yolu = yapisal["alan_yolu"].split("#", 1)
    try: beklenen = json.loads(yapisal["beklenen_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc: raise KatHatasi("beklenen_json geçersiz") from exc
    anahtar = _gozlem_anahtari({"kaynak": "cikti", "yol": yol, "alan": alan_yolu}, ciktilar)
    return anahtar, yapisal["islem"], beklenen


def derle(gorev, kabuller, cikti_sozlesmesi, kontroller, referans_gozlemleri, *, oturum_dizini,
          isci_yazilabilir_kokler=None):
    """Kontrolleri yalnız yapılandırılmış sözleşme ölçütleriyle derler."""
    if not isinstance(gorev, str) or not gorev:
        raise KatHatasi("görev kimliği dolu metin olmalı")
    try:
        from orvant_op.karsilama.sozlesme import sozlesme_hash
        from orvant_op.yurutme.kat_kapi import _onay_oku, oturum_dosyalari_dogrula
        try:
            oturum, icerikler = oturum_dosyalari_dogrula(
                Path(oturum_dizini), isci_yazilabilir_kokler)
        except (OSError, TypeError, ValueError) as exc:
            raise KatHatasi(str(exc)) from exc
        sozlesme = json.loads(icerikler["sozlesme.json"])
        onay_kaydi = _onay_oku(icerikler["olaylar.jsonl"])
        sozlesme_sha, sozlesme_rev = sozlesme_hash(sozlesme), sozlesme.get("revizyon")
        if (onay_kaydi.get("sozlesme_sha256") != sozlesme_sha
                or onay_kaydi.get("sozlesme_revizyon") != sozlesme_rev):
            raise KatHatasi("onaylı sözleşme hash/revizyon uyuşmazlığı")
        kabuller = sozlesme.get("kabul_olcutleri")
    except KatHatasi:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise KatHatasi(f"onaylı sözleşme okunamadı: {exc}") from exc
    kabul_nesneleri = _kabuller(kabuller)
    ciktilar = _ciktilar(cikti_sozlesmesi)
    if not isinstance(kontroller, list) or not kontroller:
        raise KatHatasi("en az bir kontrol gerekli")
    derlenenler = []
    for kontrol in copy.deepcopy(kontroller):
        alanlar = {"id", "dayanak_ids", "kabul_alintisi", "gozlem", "islem", "beklenen"}
        if not isinstance(kontrol, dict) or set(kontrol) != alanlar:
            raise KatHatasi("kontrol taslağında eksik veya fazla alan")
        dayanaklar, alinti = kontrol["dayanak_ids"], kontrol["kabul_alintisi"]
        if (not isinstance(dayanaklar, list) or len(dayanaklar) != 1
                or dayanaklar[0] not in kabul_nesneleri or not isinstance(alinti, str)
                or not alinti or alinti not in kabul_nesneleri[dayanaklar[0]]["metin"]):
            raise KatHatasi("kontrol var olan kabulde bire bir alıntıya bağlı değil")
        gozlem = _gozlem_anahtari(kontrol["gozlem"], ciktilar)
        yapisal_gozlem, yapisal_islem, yapisal_beklenen = _yapisal(kabul_nesneleri[dayanaklar[0]], ciktilar)
        if (gozlem != yapisal_gozlem or kontrol["islem"] != yapisal_islem
                or not _tipli_esit(kontrol["beklenen"], yapisal_beklenen)):
            raise KatHatasi("kontrol yapılandırılmış ölçütle eşleşmiyor")
        derlenenler.append({"id": kontrol["id"], "dayanak_ids": dayanaklar,
                            "alinti": alinti, "gozlem": gozlem,
                            "islem": kontrol["islem"], "beklenen": kontrol["beklenen"],
                            "uygulayici": "kat-stdlib-v1"})
    kat = {"kat_surumu": 1, "gorev": gorev, "sozlesme_sha256": sozlesme_sha,
           "sozlesme_revizyon": sozlesme_rev,
           "kapsam": {"yazilabilir": [{"yol": yol, "anlam": "dosya"} for yol in sorted(ciktilar)]},
           "kontroller": derlenenler}
    kat["kat_sha256"] = kat_hash(kat)
    dogrula(kat)
    if not isinstance(referans_gozlemleri, dict) or not yorumla(kat, referans_gozlemleri)["gecti"]:
        raise KatHatasi("pozitif referans bütün kontrolleri geçmedi")
    return kat
