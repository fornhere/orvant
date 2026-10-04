"""python -m orvant_op giriş noktası."""
import argparse
import sys

from .karsilama.cli import main as karsila_main
from .mimar.cli import main as mimar_main
from .operator.cli import main as operator_main
from .yurutme.cli import main as yurut_main
from .proje import main as proje_main


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "proje":
        return proje_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "karsila":
        return karsila_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "mimar":
        return mimar_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "yurut":
        return yurut_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in ("operator", "surdur"):
        # "surdur" kısayolu: python3 -m orvant_op surdur <calisma>
        argv = sys.argv[2:] if sys.argv[1] == "operator" else ["surdur", *sys.argv[2:]]
        return operator_main(argv)
    parser = argparse.ArgumentParser(prog="python3 -m orvant_op")
    parser.add_argument("yetenek", choices=["karsila", "mimar", "yurut", "operator", "surdur", "proje"])
    parser.parse_args()
    return 1


if __name__ == "__main__":
    sys.exit(main())
