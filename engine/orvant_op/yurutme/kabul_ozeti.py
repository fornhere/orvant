"""Makbuzdaki mevcut kanıtların insan için deterministik özeti; karar vermez."""
from copy import deepcopy


def _komut_sonucu(komut):
    if komut.get("zaman_asimi") is True:
        return False
    kod = komut.get("exit_code")
    return kod == 0 if type(kod) is int else None


def kabul_ozeti(gorev, makbuz):
    """Bilinmeyeni null bırakır; kanıtı ve başarısız kontrolleri aynen taşır."""
    ihtiyac = {"hedef": gorev.get("amac"),
               "gereksinim_ids": gorev.get("gereksinim_ids")}
    kanitlar = []
    kehanet = makbuz.get("kehanet_sonucu")
    if isinstance(kehanet, dict):
        kanitlar.append({"olcut": kehanet.get("kehanet"),
                         "sonuc": kehanet.get("gecti"), "kanit": kehanet})
        for kontrol in kehanet.get("kontroller") or []:
            kanitlar.append({"olcut": kontrol.get("ad", kontrol.get("komut", kontrol.get("command"))),
                             "sonuc": kontrol.get("gecti") if "gecti" in kontrol
                             else _komut_sonucu(kontrol), "kanit": kontrol})
    for komut in makbuz.get("komut_sonuclari") or []:
        kanitlar.append({"olcut": komut.get("id"),
                         "sonuc": _komut_sonucu(komut), "kanit": komut})
    insan = {"insan_incelemeleri": makbuz.get("insan_incelemeleri"),
             "kapsam_ihlalleri": makbuz.get("kapsam_ihlalleri")}
    surum_bagi = {alan: makbuz.get(alan) for alan in
                  ("tested_commit", "tested_tree", "merged_commit")}
    return deepcopy({"ihtiyac": ihtiyac if any(v is not None for v in ihtiyac.values()) else None,
                     "kabul_kanit": kanitlar or None,
                     "degisen": makbuz.get("degisen_dosyalar"),
                     "insan_karari": insan if any(insan.values()) else None,
                     "surum_bagi": surum_bagi})
