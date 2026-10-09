"""Karşılama komut satırı."""
import argparse
import json

from .. import iz
from .durum import Karsilama


def _bekleme_acilisi(karsilama, durum):
    for olay in reversed(karsilama.olaylar()):
        if olay["tur"] == "durum_gecisi" and olay["veri"].get("sonra") == durum:
            return olay["t"]
    return None


def parser_kur():
    p = argparse.ArgumentParser(prog="python3 -m orvant_op karsila")
    sub = p.add_subparsers(dest="eylem", required=True)
    baslat = sub.add_parser("baslat", help="Karşılama oturumu aç")
    baslat.add_argument("calisma")
    baslat.add_argument("--hedef", required=True)
    baslat.add_argument("--kaynak", action="append", default=[])
    baslat.add_argument("--yoklama", action="append", default=None)
    komutlar = {}
    for ad, yardim in (("ilerle", "Sonraki otomatik adımı yürüt"),
                       ("sorular", "Açık soruları göster"),
                       ("sozlesme", "Sözleşmeyi göster"),
                       ("durum", "Oturum durumunu göster")):
        c = sub.add_parser(ad, help=yardim)
        c.add_argument("calisma")
        komutlar[ad] = c
    komutlar["sorular"].add_argument("--json", action="store_true", dest="json_cikti")
    cevap = sub.add_parser("cevapla", help="Kullanıcı cevabını aynen kaydet")
    cevap.add_argument("calisma")
    cevap.add_argument("soru_id")
    cevap.add_argument("metin", nargs="?")
    cevap.add_argument("--oneriyi-kabul", action="store_true")
    cevap.add_argument("--dakika", type=float, default=None, help="Bu cevap için harcanan aktif dakika")
    cevap.add_argument("--yer-tutucu-kabul", action="store_true")
    onay = sub.add_parser("onayla", help="Tam sözleşme revizyonunu onayla")
    onay.add_argument("calisma")
    onay.add_argument("revizyon", type=int)
    onay.add_argument("--dakika", type=float, default=None, help="Bu onay için harcanan aktif dakika")
    return p


def main(argv=None):
    args = parser_kur().parse_args(argv)
    k = Karsilama(args.calisma)
    try:
        if args.eylem == "baslat":
            result = k.baslat(args.hedef, kaynaklar=args.kaynak, yoklamalar=args.yoklama)
        elif args.eylem == "ilerle":
            result = k.ilerle()
        elif args.eylem == "sorular":
            sorular = k.sorular()
            if args.json_cikti:
                alanlar = ("id", "metin", "secenekler", "onerilen", "durum")
                print(json.dumps([{alan: s.get(alan) for alan in alanlar} for s in sorular],
                                 ensure_ascii=False, indent=2))
                return 0
            for s in sorular:
                print(f"## {s['id']} · {s['metin']}\n\n{s['neden_onemli']}\n")
                for secenek in s["secenekler"]:
                    print(f"- {secenek}")
                print(f"\nÖnerilen: {s['onerilen']}\n")
            return 0
        elif args.eylem == "cevapla":
            if args.oneriyi_kabul:
                if args.metin is not None:
                    raise ValueError("metin ile --oneriyi-kabul birlikte kullanılamaz")
                soru = next((s for s in k.sorular() if s["id"] == args.soru_id), None)
                if not soru or not soru.get("onerilen"):
                    raise ValueError("açık sorunun kabul edilebilir önerisi yok")
                metin, kaynak = soru["onerilen"], "oneri_kabulu"
            else:
                if args.metin is None:
                    raise ValueError("cevap metni veya --oneriyi-kabul gerekli")
                metin, kaynak = args.metin, None
            args.dakika, bekleme = iz.insan_suresi(
                args.dakika, acilis=_bekleme_acilisi(k, "soru_bekliyor"))
            with iz.insan_suresi_baglami(args.dakika, bekleme, calisma=args.calisma):
                result = k.cevapla(args.soru_id, metin, dakika=args.dakika,
                                   yer_tutucu_kabul=args.yer_tutucu_kabul, kaynak=kaynak)
        elif args.eylem == "sozlesme":
            result = k.oku("sozlesme")
            for kabul in result.get("kabul_olcutleri", []):
                yapisal = kabul.get("yapisal")
                if yapisal is not None:
                    print(f"Ölçüt {kabul['id']}: {yapisal['alan_yolu']} {yapisal['islem']} {yapisal['beklenen_json']}")
        elif args.eylem == "onayla":
            args.dakika, bekleme = iz.insan_suresi(
                args.dakika, acilis=_bekleme_acilisi(k, "onay_bekliyor"))
            with iz.insan_suresi_baglami(args.dakika, bekleme, calisma=args.calisma):
                result = {"durum": k.onayla(args.revizyon, dakika=args.dakika)}
        else:
            result = k.durum()
    except (ValueError, FileNotFoundError) as exc:
        parser_kur().exit(1, f"Hata: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
