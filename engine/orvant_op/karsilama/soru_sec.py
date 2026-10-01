"""Kullanıcıya gidecek soruları salt veri üzerinden seçer."""


def sec(kararlar, *, sorular=(), en_fazla=5, ertelenen_ids=()):
    etki = {"yuksek": 3, "orta": 2, "dusuk": 1}
    belirsizlik = {"yuksek": 3, "orta": 2, "dusuk": 1}
    acik_ids = {k["id"] for k in kararlar if k["durum"] == "acik"}
    def sorulabilir(k):
        if k["id"] in ertelenen_ids or any(
                k["id"] in s["karar_ids"] and s.get("erteleme_asamasi") for s in sorular):
            return False
        if k["sahip"] != "kullanici" or k["durum"] != "acik":
            return False
        if any(k["id"] in s["karar_ids"] and s["durum"] == "acik" for s in sorular):
            return False
        oncekiler = [s for s in sorular if k["id"] in s["karar_ids"] and s["durum"] == "cevaplandi"]
        return not oncekiler or k.get("son_degisim_olay_id") != oncekiler[-1].get("karar_surumleri", {}).get(k["id"])
    adaylar = [k for k in kararlar if sorulabilir(k)]
    def siralama(k):
        bagimlilar = len(set(k.get("etkiledigi_kararlar", [])) & acik_ids)
        oncelik = 2 if k["etki"] == "yuksek" and bagimlilar else 1 if k["kategori"] in ("yetki", "veri_aktarimi") else 0
        return (-oncelik, -bagimlilar if oncelik == 2 else 0,
                -etki[k["etki"]] * belirsizlik[k["belirsizlik"]], k["id"])
    adaylar.sort(key=siralama)
    return adaylar[:min(5, max(0, en_fazla))]
