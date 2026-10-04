"""Operatörün Türkçe komut satırı."""

import argparse
import json
import sys

from .dongu import Operator, rapor_metni
from .soru_kuyrugu import paket_metni


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
    sorular = alt.add_parser("sorular", help="Karar sorularını paketler halinde göster")
    sorular.add_argument("calisma")
    sorular.add_argument("--esitle", action="store_true")
    sorular.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = parser_kur().parse_args(argv)
    try:
        operator = Operator(args.calisma, kota_esigi=getattr(args, "kota_esigi", None),
                            yurut_zaman_asimi=getattr(args, "yurut_zaman_asimi", 3600),
                            kehanet_zaman_asimi=getattr(args, "kehanet_zaman_asimi", 1500))
        if args.eylem == "cevapla":
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
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1
