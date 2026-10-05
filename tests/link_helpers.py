"""Sembolik bağ ayrıcalığı olmayan Windows koşucuları için test yardımcısı."""
import unittest


def sembolik_bag_olustur(bag, hedef, *, dizin=False):
    try:
        bag.symlink_to(hedef, target_is_directory=dizin)
    except (OSError, NotImplementedError) as hata:
        raise unittest.SkipTest(f"Sembolik bağ oluşturulamıyor: {hata}") from hata
