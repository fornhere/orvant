"""Kullanıcı girdisindeki bağımsız yer tutucuları denetler."""
import re


_BELIRTEC = re.compile(
    r"(?<![\w-])(?:X{1,2}|TBD|TODO|\?\?\?)(?![\w-])|<[^<>]+>|\[[^\[\]]+\]",
    re.IGNORECASE,
)


def denetle(metin, *, kabul=False):
    if kabul:
        return
    eslesme = _BELIRTEC.search(metin)
    belirtec = eslesme.group() if eslesme else "..." if metin.rstrip().endswith("...") else None
    if belirtec:
        raise ValueError(f"Yer tutucu var: '{belirtec}'. Gerçek değeri yazın ya da --yer-tutucu-kabul")
