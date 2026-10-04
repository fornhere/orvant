"""Mimar komut satırı."""
import argparse
import json
import shlex

from .durum import Mimar, _oku, _yaz
from .kehanet import Kehanet, kehanet_gecersiz_mi, kehanet_dayanak_degisti, kehanet_yolu


def parser_kur():
    p = argparse.ArgumentParser(prog="python3 -m orvant_op mimar")
    sub = p.add_subparsers(dest="eylem", required=True)
    kayit = sub.add_parser("proje-kaydi", help="Onaylı planı şema-3 proje kaydına aktar")
    kayit.add_argument("calisma")
    kayit.add_argument("--kok", required=True)
    kayit.add_argument("--kuru", action="store_true")
    envanter = sub.add_parser("envanter", help="Yerel skill ve araç envanterini çıkar")
    envanter.add_argument("calisma")
    envanter.add_argument("--skill-dizini", action="append", dest="skill_dizinleri")
    plan = sub.add_parser("plan")
    plan.add_argument("calisma")
    plan.add_argument("--depo")
    plan.add_argument("--yeni-taslak", action="store_true")
    kehanet = sub.add_parser("kehanet")
    kehanet.add_argument("calisma")
    kehanet.add_argument("--gorev")
    kehanet.add_argument("--referans", action="store_true", help="Bağımsız referans üret ve pozitif kontrolü çalıştır")
    kehanet.add_argument("--denetle", action="store_true", help="Model çağırmadan mevcut kehaneti denetle")
    kehanet.add_argument("--yeniden", action="store_true",
                         help="Var olan kehaneti yeniden üret (ör. çıktı sözleşmesi olmayan eski kehanetler)")
    kabul = sub.add_parser("kabul-degistir")
    kabul.add_argument("calisma")
    kabul.add_argument("gorev")
    kabul.add_argument("--kabul-id", required=True)
    kabul.add_argument("--beklenen", required=True)
    kabul.add_argument("--komut")
    kabul.add_argument("--onay-olay", required=True)
    kabul.add_argument("gerekce")
    tekrar = sub.add_parser("yeniden-planla")
    tekrar.add_argument("calisma")
    tekrar.add_argument("neden")
    tekrar.add_argument("--gorev")
    tekrar.add_argument("--teshis-dosyasi")
    for ad in ("yetki", "durum"):
        sub.add_parser(ad).add_argument("calisma")
    izin = sub.add_parser("izin")
    izin.add_argument("calisma")
    izin.add_argument("istek_id")
    izin.add_argument("metin")
    izin.add_argument("--karar", choices=("verildi", "reddedildi"), required=True)
    izin.add_argument("--dakika", type=float)
    izin.add_argument("--yol", action="append", default=[])
    izin_yol = sub.add_parser("izin-yol")
    izin_yol.add_argument("calisma")
    izin_yol.add_argument("istek_id")
    izin_yol.add_argument("yol")
    izin_yol.add_argument("metin")
    karar_kanit = sub.add_parser("karar-kanit")
    karar_kanit.add_argument("calisma")
    karar_kanit.add_argument("karar_id")
    karar_kanit.add_argument("--gorev", action="append", required=True)
    karar_kanit.add_argument("ozet")
    karar = sub.add_parser("karar")
    karar.add_argument("calisma")
    karar.add_argument("karar_id")
    karar.add_argument("metin")
    karar.add_argument("--dakika", type=float)
    karar.add_argument("--yer-tutucu-kabul", action="store_true")
    girdi = sub.add_parser("girdi")
    girdi.add_argument("calisma")
    girdi.add_argument("baslik")
    girdi.add_argument("deger")
    girdi.add_argument("--gorev")
    girdi.add_argument("--dakika", type=float)
    girdi.add_argument("--yer-tutucu-kabul", action="store_true")
    return p


def kehanet_hazirla(calisma, gorev_id=None, *, yeniden=False, referans=False, yurutucu=None, iz_yolu=None, zaman_asimi=1500):
    """CLI ile operatörün ortak kehanet üretimi ve son işlemesi."""
    mimar = Mimar(calisma, yurutucu=yurutucu, iz_yolu=iz_yolu)
    plan = mimar.oku()
    gorevler = [g for g in plan["gorevler"] if gorev_id is None or g["id"] == gorev_id]
    if not gorevler:
        raise ValueError("görev bulunamadı")
    yazar = Kehanet(calisma, yurutucu=yurutucu, iz_yolu=iz_yolu, zaman_asimi=zaman_asimi, referans=referans)
    degisenler = {g["id"]: kehanet_dayanak_degisti(calisma, g["id"]) for g in gorevler}
    gecersizler = {g["id"] for g in gorevler if kehanet_gecersiz_mi(calisma, g["id"])}
    sonuc = [yazar.hazirla(g, yeniden=1 if yeniden or g["id"] in gecersizler else 0)
             for g in gorevler]
    zayif = False
    for kayit in sonuc:
        # Eski sözleşmesizlik uyarısı dış listede tek kalır; ayrıntılı atlanma da olay olur.
        kusur_uyarilari = kayit.get("kusur_denetimi", {}).get("uyarilar", [])
        uyarilar = [u for u in kusur_uyarilari if u not in kayit.get("uyarilar", [])]
        for uyari in dict.fromkeys([*kayit.get("duzeltilen_uyarilar", []), *uyarilar, *kayit.get("uyarilar", [])]):
            mimar._olay("cikti_sozlesmesi_yok" if uyari == "çıktı sözleşmesi yok (eski kehanet)"
                        else "kehanet_sessizlik_esigi_uyarisi" if uyari.startswith("sessizlik yalnız -inf")
                        else "kehanet_asiri_kati_uyarisi" if "aşırı katı" in uyari
                        else "kehanet_sozlesme_yol_uyarisi" if uyari.startswith(("alan adı dosya yolu gibi:", "dosya yolu alan yolu gibi:"))
                        else "kehanet_kusur_uyarisi" if uyari in kayit.get("kusur_denetimi", {}).get("uyarilar", [])
                        else "kehanet_varlik_sarti_uyarisi",
                        veri={"gorev": kayit["gorev"], "kehanet": kayit["kehanet"], "uyari": uyari},
                        is_turu="dogrulama", sonuc="ok")
        gorev = next(g for g in gorevler if g["id"] == kayit["gorev"])
        if ((kayit["zayiflik_denetimi"] == "gecti" or
             (kayit["zayiflik_denetimi"] == "atlandi" and gorev["durum"] == "kabul"))
                and kayit["gorev"] in gecersizler):
            yol = mimar.kok / "kehanet_durumu.json"
            isaretler = _oku(yol) if yol.exists() else {}
            onceki = isaretler.pop(kayit["gorev"], {})
            if yol.exists():
                _yaz(yol, isaretler)
            dayanak = ({"neden": "dayanak_degisti", "degisen_kararlar": degisenler[kayit["gorev"]]}
                       if degisenler[kayit["gorev"]] else {})
            mimar._olay("kehanet_yeniden_uretildi", veri={**kayit,
                        "kabul_degisikligi": onceki.get("kabul_degisikligi"), **dayanak},
                        is_turu="dogrulama")
        if kayit["zayiflik_denetimi"] in ("zayif", "gecersiz"):
            veri = dict(kayit)
            if gorev["durum"] == "kabul":
                veri["gorev_durumu"] = "kabul"
            else:
                zayif = True
                gorev["durum"] = "engelli"
            mimar._olay("kehanet_" + kayit["zayiflik_denetimi"],
                        veri=veri, is_turu="dogrulama", sonuc="ret")
    if zayif:
        _yaz(mimar.kok / "plan.json", plan)
    return sonuc


def kehanet_denetle(calisma, gorev_id=None):
    """Kabul edilmiş görevler dahil salt denetler; plan durumlarını değiştirmez."""
    mimar = Mimar(calisma)
    gorevler = [g for g in mimar.oku()["gorevler"] if
                (g["id"] == gorev_id if gorev_id is not None else
                 kehanet_yolu(calisma, g["id"]).exists())]
    if gorev_id is not None and not gorevler:
        raise ValueError("görev bulunamadı")
    yazar = Kehanet(calisma)
    sonuc = []
    for gorev in gorevler:
        kayit = yazar.denetle(gorev)
        for uyari in kayit.get("uyarilar", []):
            if "aşırı katı" in uyari or uyari.startswith(("alan adı dosya yolu gibi:", "dosya yolu alan yolu gibi:")):
                mimar._olay("kehanet_asiri_kati_uyarisi" if "aşırı katı" in uyari else "kehanet_sozlesme_yol_uyarisi",
                            veri={"gorev": kayit["gorev"], "uyari": uyari}, is_turu="dogrulama", sonuc="ok")
        mimar._olay("kehanet_denetimi", veri=kayit, is_turu="dogrulama",
                    sonuc="bilinmiyor" if kayit["sonuc"] == "denetlenemedi" else
                    "ok" if kayit["sonuc"] in ("saglam", "atlandi") else "ret")
        sonuc.append(kayit)
    return sonuc


def main(argv=None):
    args = parser_kur().parse_args(argv)
    mimar = Mimar(args.calisma)
    try:
        if args.eylem == "proje-kaydi":
            from .proje_kaydi import aktar
            sonuc = aktar(args.calisma, args.kok, kuru=args.kuru)
        elif args.eylem == "plan":
            sonuc = mimar.planla(depo=args.depo, yeni_taslak=args.yeni_taslak)
        elif args.eylem == "envanter":
            sonuc = mimar.envanter(skill_dizinleri=args.skill_dizinleri)
        elif args.eylem == "kehanet":
            if args.denetle and args.referans:
                raise ValueError("--denetle ile --referans birlikte kullanılamaz")
            if args.denetle and args.yeniden:
                raise ValueError("--denetle ile --yeniden birlikte kullanılamaz")
            sonuc = (kehanet_denetle(args.calisma, args.gorev) if args.denetle else
                     kehanet_hazirla(args.calisma, args.gorev, yeniden=args.yeniden, referans=args.referans))
        elif args.eylem == "kabul-degistir":
            sonuc = mimar.kabul_degistir(args.gorev, args.kabul_id, beklenen=args.beklenen,
                                         komut=args.komut, onay_olay_id=args.onay_olay,
                                         gerekce=args.gerekce)
        elif args.eylem == "yeniden-planla":
            sonuc = mimar.yeniden_planla(args.neden, gorev=args.gorev,
                                          teshis_dosyasi=args.teshis_dosyasi)
        elif args.eylem == "yetki":
            yol = mimar.kok / "yetki_onerilen_yollar.json"
            oneriler = _oku(yol) if yol.exists() else {}
            for istek in mimar.yetkiler():
                print(f"## {istek['id']} · {istek['eylem']}\n{istek['ayrinti']}\nNeden: {istek['gerekce']}\nBu kapsam için izin veriyor musunuz?\n")
                if oneriler.get(istek["id"]):
                    print("Önerilen izin yolları: " + " ".join(
                        "--yol " + shlex.quote(yol) for yol in oneriler[istek["id"]]))
            return 0
        elif args.eylem == "izin":
            sonuc = mimar.izin(args.istek_id, args.metin, karar=args.karar, dakika=args.dakika, yollar=args.yol)
        elif args.eylem == "izin-yol":
            sonuc = mimar.izin_yol(args.istek_id, args.yol, args.metin)
        elif args.eylem == "karar-kanit":
            sonuc = mimar.karar_kanit(args.karar_id, args.gorev, args.ozet)
        elif args.eylem == "karar":
            sonuc = mimar.karar(args.karar_id, args.metin, dakika=args.dakika,
                                 yer_tutucu_kabul=args.yer_tutucu_kabul)
        elif args.eylem == "girdi":
            sonuc = mimar.girdi(args.baslik, args.deger, dakika=args.dakika, gorev=args.gorev,
                                 yer_tutucu_kabul=args.yer_tutucu_kabul)
        else:
            sonuc = mimar.oku()
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser_kur().exit(1, f"Hata: {exc}\n")
    print(json.dumps(sonuc, ensure_ascii=False, indent=2))
    return 0
