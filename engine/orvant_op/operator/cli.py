"""Operatörün Türkçe komut satırı."""

import argparse
import json
import shlex
import sys
from pathlib import Path

from orvant_op import iz

from .dongu import Operator, rapor_metni
from .soru_kuyrugu import paket_metni


TANIMLI_HATA_CIKISI = 2


def _komut(*parcalar):
    return "python3 -m orvant_op " + " ".join(shlex.quote(str(parca)) for parca in parcalar)


def _plansiz_sonuc(calisma):
    """Plan öncesi aşamayı ham dosya hatasına düşmeden makinece okunur kıl."""
    calisma = Path(calisma).resolve()
    if (calisma / "plan/plan.json").exists():
        return None
    sozlesme_yolu = calisma / "karsilama/sozlesme.json"
    if not sozlesme_yolu.exists():
        return {"durum": "eksik_dosya", "sonraki_adim": _komut("karsila", "durum", calisma)}
    try:
        sozlesme = json.loads(sozlesme_yolu.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"durum": "eksik_dosya", "sonraki_adim": _komut("karsila", "durum", calisma)}
    onay = sozlesme.get("onay") or {}
    if onay.get("durum") == "onaylandi" and onay.get("revizyon") == sozlesme.get("revizyon"):
        return {"durum": "plan_yok",
                "sonraki_adim": _komut("mimar", "plan", calisma, "--depo") + " <depo>"}
    return {"durum": "eksik_dosya", "sonraki_adim": _komut("karsila", "durum", calisma)}


def _hata_yaz(args, sonuc, mesaj):
    if getattr(args, "json", False):
        print(json.dumps(sonuc, ensure_ascii=False))
    else:
        mesaj += f" Sonraki adım: {sonuc['sonraki_adim']}"
    print(f"Hata: {mesaj}", file=sys.stderr)
    return TANIMLI_HATA_CIKISI


def parser_kur():
    parser = argparse.ArgumentParser(prog="python3 -m orvant_op.operator")
    alt = parser.add_subparsers(dest="eylem", required=True)
    surdur = alt.add_parser("surdur", help="Yetkili işleri sınırlar içinde sürdür")
    surdur.add_argument("calisma")
    surdur.add_argument("--en-fazla-tur", type=int, default=5)
    surdur.add_argument("--tur-basina-kosu", type=int, default=3)
    surdur.add_argument("--kota-esigi", type=float, default=None,
                        help="Kota yüzdesi eşiği; verilmezse ayar ([operator].kota_esigi), yoksa kapalı")
    surdur.add_argument("--yurut-zaman-asimi", type=float, default=3600, help="İşçi süre sınırı (saniye)")
    surdur.add_argument("--kehanet-zaman-asimi", type=float, default=1500, help="Kehanet süre sınırı (saniye)")
    surdur.add_argument("--kuru", action="store_true")
    surdur.add_argument("--json", action="store_true")
    cevapla = alt.add_parser("cevapla", help="Kullanıcı cevabını ilgili kayda işle")
    cevapla.add_argument("calisma")
    cevapla.add_argument("soru_id")
    cevapla.add_argument("cevap")
    cevapla.add_argument("--dakika", type=float)
    sorular = alt.add_parser("sorular", help="Karar sorularını paketler halinde göster")
    sorular.add_argument("calisma")
    sorular.add_argument("--esitle", action="store_true")
    sorular.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = parser_kur().parse_args(argv)
    try:
        if args.eylem == "surdur":
            plansiz = _plansiz_sonuc(args.calisma)
            if plansiz:
                mesaj = ("Plan bulunamadı; sonraki adımı çalıştırın."
                          if plansiz["durum"] == "plan_yok" else
                          "Gerekli çalışma dosyası bulunamadı; karşılama durumunu denetleyin.")
                return _hata_yaz(args, plansiz, mesaj)
        operator = Operator(args.calisma, kota_esigi=getattr(args, "kota_esigi", None),
                            yurut_zaman_asimi=getattr(args, "yurut_zaman_asimi", 3600),
                            kehanet_zaman_asimi=getattr(args, "kehanet_zaman_asimi", 1500))
        if args.eylem == "cevapla":
            soru = next((s for s in operator.sorular.oku() if s["id"] == args.soru_id), {})
            dakika, bekleme = iz.insan_suresi(args.dakika,
                                              acilis=soru.get("acilis_t") or soru.get("t"))
            with iz.insan_suresi_baglami(dakika, bekleme, calisma=args.calisma):
                sonuc = operator.cevapla(args.soru_id, args.cevap)
            print(sonuc["mesaj"])
            if sonuc.get("yeni_soru"):
                print(sonuc["yeni_soru"]["soru"])
            if sonuc.get("hazir_komut"):
                print(sonuc["hazir_komut"])
            if sonuc.get("uyari"):
                print("Uyarı: " + sonuc["uyari"])
            return sonuc["cikis"]
        if args.eylem == "sorular":
            sorular = operator.soru_esitle() if args.esitle else operator.sorular.oku()
            print(json.dumps(sorular, ensure_ascii=False, indent=2) if args.json else paket_metni(sorular))
            return 0
        sonuc = operator.surdur(en_fazla_tur=args.en_fazla_tur,
                                 tur_basina_kosu=args.tur_basina_kosu, kuru=args.kuru)
        print(json.dumps(sonuc, ensure_ascii=False, indent=2) if args.json else rapor_metni(sonuc))
        return 0
    except FileNotFoundError:
        sonuc = {"durum": "eksik_dosya",
                 "sonraki_adim": _komut("karsila", "durum", Path(args.calisma).resolve())}
        return _hata_yaz(args, sonuc, "Gerekli çalışma dosyası bulunamadı.")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1
