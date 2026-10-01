"""Kullanıcının sonraki aşamaya bağladığı cevabı soru tekrarından korur."""
import re


def cevaptan(metin):
    """Yalnız karar vermeyi erteleyen ifadeler; koşullu bir izin/değer değildir."""
    metin = metin.casefold().replace("i̇", "i")
    if not re.search(r"\b(söylerim|belirtirim|belirlerim|karar veririm|netleştiririm|"
                     r"cevaplarım|seçerim|erteleyelim|erteliyorum|sonra karar|henüz söyleyemem)", metin):
        return None
    if not re.search(r"(görünce|gördükten|sonra|aşama|s[2-7]\b|fizibilite|planlama|"
                     r"bekleyelim|erteleyelim|erteliyorum)", metin):
        return None
    asama = re.search(r"\bs[2-7]\b", metin)
    if asama:
        return asama.group().upper()
    if re.search(r"(aralı[ğk]|efor|maliyet|fizibilite|plan)", metin):
        return "S2 planlama/fizibilite"
    return "sonraki aşama (kullanıcının belirttiği koşul sağlanınca)"


def kayitlar(sorular, cevaplar):
    """Soru kimliği üzerinden aynı kök karara ait geçmiş ertelemeleri bulur."""
    olaylar = {e["id"]: e for e in cevaplar if e["tur"] == "kullanici_cevabi"}
    bulunan = {}
    for soru in sorular:
        olay = olaylar.get(soru.get("cevap_olay_id"))
        if soru.get("durum") != "cevaplandi" or not olay:
            continue
        asama = soru.get("erteleme_asamasi") or cevaptan(olay["metin"])
        if asama:
            for kimlik in soru["karar_ids"]:
                bulunan[kimlik] = {"kaynak_olay_id": olay["id"],
                    "erteleme_gerekcesi": f"Kullanıcı cevabı: {olay['metin']} Bağlı aşama: {asama}."}
    return bulunan
