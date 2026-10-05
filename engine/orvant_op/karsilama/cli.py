"""Karşılama komut satırı."""
import argparse
import json

from orvant_op.uyum_komut import konsolu_utf8_yap, prog
from .durum import Karsilama


def parser_kur():
    p = argparse.ArgumentParser(prog=prog("karsila"))
    sub = p.add_subparsers(dest="eylem", required=True)
    baslat = sub.add_parser("baslat", help="Karşılama oturumu aç")
    baslat.add_argument("calisma")
    baslat.add_argument("--hedef", required=True)
    baslat.add_argument("--kaynak", action="append", default=[])
    baslat.add_argument("--yoklama", action="append", default=None)
    for ad, yardim in (("ilerle", "Sonraki otomatik adımı yürüt"),
                       ("sorular", "Açık soruları göster"),
                       ("sozlesme", "Sözleşmeyi göster"),
                       ("durum", "Oturum durumunu göster")):
        c = sub.add_parser(ad, help=yardim)
        c.add_argument("calisma")
    cevap = sub.add_parser("cevapla", help="Kullanıcı cevabını aynen kaydet")
    cevap.add_argument("calisma")
    cevap.add_argument("soru_id")
    cevap.add_argument("metin")
    cevap.add_argument("--dakika", type=float, default=None, help="Bu cevap için harcanan aktif dakika")
    cevap.add_argument("--yer-tutucu-kabul", action="store_true")
    onay = sub.add_parser("onayla", help="Tam sözleşme revizyonunu onayla")
    onay.add_argument("calisma")
    onay.add_argument("revizyon", type=int)
    onay.add_argument("--dakika", type=float, default=None, help="Bu onay için harcanan aktif dakika")
    return p


def main(argv=None):
    konsolu_utf8_yap()
    args = parser_kur().parse_args(argv)
    k = Karsilama(args.calisma)
    try:
        if args.eylem == "baslat":
            result = k.baslat(args.hedef, kaynaklar=args.kaynak, yoklamalar=args.yoklama)
        elif args.eylem == "ilerle":
            result = k.ilerle()
        elif args.eylem == "sorular":
            for s in k.sorular():
                print(f"## {s['id']} · {s['metin']}\n\n{s['neden_onemli']}\n")
                for secenek in s["secenekler"]:
                    print(f"- {secenek}")
                print(f"\nÖnerilen: {s['onerilen']}\n")
            return 0
        elif args.eylem == "cevapla":
            result = k.cevapla(args.soru_id, args.metin, dakika=args.dakika,
                               yer_tutucu_kabul=args.yer_tutucu_kabul)
        elif args.eylem == "sozlesme":
            result = k.oku("sozlesme")
            for kabul in result.get("kabul_olcutleri", []):
                yapisal = kabul.get("yapisal")
                if yapisal is not None:
                    print(f"Ölçüt {kabul['id']}: {yapisal['alan_yolu']} {yapisal['islem']} {yapisal['beklenen_json']}")
        elif args.eylem == "onayla":
            result = {"durum": k.onayla(args.revizyon, dakika=args.dakika)}
        else:
            result = k.durum()
    except (ValueError, FileNotFoundError) as exc:
        parser_kur().exit(1, f"Hata: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
