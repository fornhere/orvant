"""S3 komut satırı."""
import argparse
import json

from orvant_op.uyum_komut import konsolu_utf8_yap, prog
from .zamanlayici import Yurutme


def parser_kur(eylem=None):
    p = argparse.ArgumentParser(prog=prog("yurut", *([eylem] if eylem else [])))
    p.add_argument("calisma")
    if eylem == "incele":
        p.add_argument("gorev")
        p.add_argument("kabul_id")
        p.add_argument("not_metni")
        p.add_argument("--sonuc", required=True, choices=("gecti", "kaldi"))
        p.add_argument("--dakika", type=float)
    elif eylem == "ac":
        p.add_argument("gorev")
        p.add_argument("gerekce")
        p.add_argument("--ek-deneme", type=int, required=True,
                       help="Engelli/ret görevi yeniden açar; hazır veya girdi/yetki bekleyen görevin yalnız hakkını artırır")
    elif eylem in ("kapi", "serbest"):
        p.add_argument("gorev")
    elif eylem in ("geri-al", "iptal", "karantina-kaldir"):
        p.add_argument("gorev")
        p.add_argument("gerekce")
        if eylem == "geri-al":
            p.add_argument("--yanlis-kabul", action="store_true")
    elif eylem == "yeniden-denetle":
        p.add_argument("gorev", nargs="?")
        p.add_argument("--hepsi-isaretli", action="store_true")
    elif eylem is None:
        p.add_argument("--gorev")
        p.add_argument("--en-fazla", type=int)
        p.add_argument("--paralel", type=int, default=1)
    return p


def main(argv=None):
    konsolu_utf8_yap()
    argv = list(argv or [])
    eylem = argv.pop(0) if argv and argv[0] in ("durum", "incele", "ac", "kapi", "iptal", "karantina-kaldir", "geri-al", "serbest", "yeniden-denetle") else None
    parser = parser_kur(eylem)
    args = parser.parse_args(argv)
    if eylem == "yeniden-denetle" and bool(args.gorev) == args.hepsi_isaretli:
        parser.error("görev veya --hepsi-isaretli seçeneklerinden yalnız biri gerekli")
    y = Yurutme(args.calisma)
    try:
        if eylem == "durum":
            result = y.durum()
        elif eylem == "incele":
            result = y.incele(args.gorev, args.kabul_id, args.sonuc, args.not_metni,
                              dakika=args.dakika)
        elif eylem == "ac":
            result = y.ac(args.gorev, args.ek_deneme, args.gerekce)
        elif eylem == "kapi":
            result = y.kapi(args.gorev)
        elif eylem == "geri-al":
            result = y.geri_al(args.gorev, args.gerekce, yanlis_kabul=args.yanlis_kabul)
        elif eylem == "karantina-kaldir":
            result = y.karantina_kaldir(args.gorev, args.gerekce)
        elif eylem == "iptal":
            result = y.iptal(args.gorev, args.gerekce)
        elif eylem == "serbest":
            result = y.serbest(args.gorev)
        elif eylem == "yeniden-denetle":
            result = y.yeniden_denetle_isaretliler() if args.hepsi_isaretli else y.yeniden_denetle(args.gorev)
        else:
            result = y.yurut(gorev_id=args.gorev, en_fazla=args.en_fazla, paralel=args.paralel)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise SystemExit(f"Hata: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
