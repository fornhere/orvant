"""python -m orvant_op giriş noktası."""
import argparse
import sys

from .uyum_komut import konsolu_utf8_yap, prog


def main():
    # Windows'ta stdout/stderr/stdin'i UTF-8 yap (boru/konsol cp1254 olsa da Türkçe çıktı düşmesin).
    konsolu_utf8_yap()
    # Alt komut modülleri yalnız seçilince içe aktarılır: biri yüklenemese bile `--help` ve diğerleri çalışır.
    if len(sys.argv) > 1 and sys.argv[1] == "proje":
        from .proje import main as proje_main
        return proje_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "karsila":
        from .karsilama.cli import main as karsila_main
        return karsila_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "mimar":
        from .mimar.cli import main as mimar_main
        return mimar_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "yurut":
        from .yurutme.cli import main as yurut_main
        return yurut_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in ("operator", "surdur"):
        from .operator.cli import main as operator_main
        # "surdur" kısayolu: python3 -m orvant_op surdur <calisma>
        argv = sys.argv[2:] if sys.argv[1] == "operator" else ["surdur", *sys.argv[2:]]
        return operator_main(argv)
    parser = argparse.ArgumentParser(prog=prog())
    parser.add_argument("yetenek", choices=["karsila", "mimar", "yurut", "operator", "surdur", "proje"])
    parser.parse_args()
    return 1


if __name__ == "__main__":
    sys.exit(main())
