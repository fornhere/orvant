"""Windows dosya kapısı, kavşak ve UTF-8 yol denemeleri."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "engine"))

from orvant_op import uyum  # noqa: E402
from orvant_op.teshis.gecici_artik import _kimlik, artik_incele, artik_temizle  # noqa: E402
from orvant_op.yurutme.kat_kapi import gozlem_topla, oturum_dosyalari_dogrula  # noqa: E402


class DosyaGuvenligiTesti(unittest.TestCase):
    def setUp(self):
        alan = KOK / ".scratch"
        alan.mkdir(exist_ok=True)
        gecici = tempfile.TemporaryDirectory(prefix="dosya-", dir=alan)
        self.addCleanup(gecici.cleanup)
        self.kok = Path(gecici.name)

    def _git(self, depo, *args):
        sonuc = subprocess.run(["git", "-C", str(depo), *args], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr)

    def _kavsak(self, yol, hedef):
        if not uyum.WINDOWS:
            os.symlink(hedef, yol, target_is_directory=True)
            return
        sonuc = subprocess.run(["cmd", "/c", "mklink", "/J", str(yol), str(hedef)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=10)
        if sonuc.returncode:
            self.skipTest("mklink /J kullanılamıyor: " + sonuc.stderr)

    def test_oturum_dosyalari_turkce_icerik(self):
        oturum = self.kok / "oturum-çalışma"
        karsilama = oturum / "karsilama"
        karsilama.mkdir(parents=True)
        (karsilama / "olaylar.jsonl").write_text('{"metin":"çağrı şüphe ölçü"}\n', encoding="utf-8")
        (karsilama / "sozlesme.json").write_text('{"başlık":"Işık"}\n', encoding="utf-8")
        isci = self.kok / "isci"
        isci.mkdir()
        _, icerikler = oturum_dosyalari_dogrula(oturum, [isci])
        self.assertIn("çağrı şüphe ölçü", icerikler["olaylar.jsonl"])
        self.assertIn("Işık", icerikler["sozlesme.json"])

    def test_oturum_kavsagi_reddedilir(self):
        isci = self.kok / "isci"
        isci.mkdir()
        (isci / "olaylar.jsonl").write_text("{}\n", encoding="utf-8")
        (isci / "sozlesme.json").write_text("{}\n", encoding="utf-8")
        oturum = self.kok / "oturum"
        oturum.mkdir()
        self._kavsak(oturum / "karsilama", isci)
        with self.assertRaises(ValueError):
            oturum_dosyalari_dogrula(oturum, [isci])

    def test_artik_turkce_yol_ve_temizlik(self):
        depo = self.kok / "depo"
        depo.mkdir()
        self._git(depo, "init", "-q")
        self._git(depo, "-c", "user.name=Deneme", "-c", "user.email=deneme@example.invalid",
                  "commit", "-q", "--allow-empty", "-m", "başlangıç")
        ad = "çağrı-ölçü.txt"
        (depo / ad).write_text("Türkçe içerik\n", encoding="utf-8")
        gorev = {"yazilabilir": ["izinli.txt"]}
        inceleme = artik_incele(depo, gorev, [ad])
        self.assertTrue(inceleme["uygun"], inceleme)
        silinen = []
        artik_temizle(depo, gorev, inceleme, lambda *_: (None, [ad]), silinen.append)
        self.assertEqual(silinen, [ad])
        self.assertFalse((depo / ad).exists())

    def test_artik_kavsak_reddedilir(self):
        depo = self.kok / "depo"
        depo.mkdir()
        self._git(depo, "init", "-q")
        self._git(depo, "-c", "user.name=Deneme", "-c", "user.email=deneme@example.invalid",
                  "commit", "-q", "--allow-empty", "-m", "başlangıç")
        (depo / "kaçak.txt").write_text("x", encoding="utf-8")
        bag = self.kok / "bag"
        self._kavsak(bag, depo)
        sonuc = artik_incele(bag, {"yazilabilir": []}, ["kaçak.txt"])
        self.assertFalse(sonuc["uygun"], sonuc)

    @unittest.skipUnless(uyum.WINDOWS, "üst dizin kavşağı Windows'a özgü")
    def test_artik_ust_dizin_kavsagi_reddedilir(self):
        gercek = self.kok / "gercek"
        depo = gercek / "depo"
        depo.mkdir(parents=True)
        self._git(depo, "init", "-q")
        self._git(depo, "-c", "user.name=Deneme", "-c", "user.email=deneme@example.invalid",
                  "commit", "-q", "--allow-empty", "-m", "başlangıç")
        (depo / "kaçak.txt").write_text("x", encoding="utf-8")
        bag = self.kok / "bag"
        self._kavsak(bag, gercek)
        sonuc = artik_incele(bag / "depo", {"yazilabilir": []}, ["kaçak.txt"])
        self.assertFalse(sonuc["uygun"], sonuc)
        self.assertIn("bağ", sonuc["hata"])

    def test_degistir_hedef_acikken_yeniden_dener(self):
        kaynak, hedef = self.kok / "yeni", self.kok / "eski"
        kaynak.write_text("yeni", encoding="utf-8")
        hedef.write_text("eski", encoding="utf-8")
        asil = os.replace
        sayac = 0
        tutamak = hedef.open("rb")
        self.addCleanup(tutamak.close)

        def gecici_hata(a, b):
            nonlocal sayac
            sayac += 1
            if sayac <= 2:
                raise PermissionError("hedef açık")
            tutamak.close()
            return asil(a, b)

        with patch("orvant_op.uyum.os.replace", side_effect=gecici_hata):
            uyum.degistir(kaynak, hedef, deneme=3, bekleme=0)
        self.assertEqual(sayac, 3)
        self.assertEqual(hedef.read_text(encoding="utf-8"), "yeni")

    def test_duzenli_dosya_kimligi_ve_coklu_bag(self):
        yol = self.kok / "kimlik.txt"
        yol.write_text("içerik", encoding="utf-8")
        with yol.open("rb") as akis:
            self.assertTrue(stat.S_ISREG(os.fstat(akis.fileno()).st_mode))
            self.assertEqual(_kimlik(os.lstat(yol)), _kimlik(os.fstat(akis.fileno())))
        self.assertEqual(os.stat(yol).st_nlink, 1)
        bag = self.kok / "ikinci.txt"
        try:
            os.link(yol, bag)
        except OSError as exc:
            self.skipTest(f"dosya sistemi sabit bağ desteklemiyor: {exc}")
        self.assertEqual(os.stat(yol).st_nlink, 2)

    @unittest.skipUnless(uyum.WINDOWS, "çıktı kavşağı Windows'a özgü")
    def test_gozlem_ciktisi_kavsagi_okunmaz(self):
        kok = self.kok / "cikti"
        dis = self.kok / "dis"
        kok.mkdir()
        dis.mkdir()
        (dis / "gizli.json").write_text('{"sır":"okunmamalı"}', encoding="utf-8")
        self._kavsak(kok / "bag", dis)
        kat = {"kontroller": [{"gozlem": "cikti:bag/gizli.json#/sır"}]}
        with patch("orvant_op.yurutme.kat_kapi.dogrula"):
            sonuc = gozlem_topla(kat, kok)
        self.assertEqual(sonuc["cikti:bag/gizli.json#/sır"], {"durum": "okunamadi"})


if __name__ == "__main__":
    unittest.main()
