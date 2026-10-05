import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from orvant_op import uyum  # noqa: E402

KILITLE = (
    "import sys; sys.path.insert(0, sys.argv[1]); from orvant_op import uyum; import os\n"
    "fd = os.open(sys.argv[2], os.O_RDWR | os.O_CREAT, 0o666)\n"
    "try:\n"
    "    uyum.kilitle(fd, paylasimli=sys.argv[3] == 'sh', bekle=False)\n"
    "    print('ALINDI')\n"
    "except BlockingIOError:\n"
    "    print('DOLU')\n"
)


class KilitTesti(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.TemporaryDirectory()
        self.addCleanup(self.kok.cleanup)
        self.dosya = Path(self.kok.name) / "kilit"
        self.fd = os.open(self.dosya, os.O_RDWR | os.O_CREAT, 0o666)
        self.addCleanup(lambda: os.close(self.fd) if self.fd is not None else None)

    def baska_surec(self, kip="ex"):
        engine = str(Path(uyum.__file__).resolve().parents[1])
        cikti = subprocess.run([sys.executable, "-c", KILITLE, engine, str(self.dosya), kip],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(cikti.returncode, 0, cikti.stderr)
        return cikti.stdout.strip()

    def test_ozel_kilit_baska_surecte_dolu(self):
        uyum.kilitle(self.fd)
        self.assertEqual(self.baska_surec("ex"), "DOLU")
        self.assertEqual(self.baska_surec("sh"), "DOLU")
        uyum.kilit_birak(self.fd)
        self.assertEqual(self.baska_surec("ex"), "ALINDI")

    def test_paylasimli_kilitler_birlikte(self):
        uyum.kilitle(self.fd, paylasimli=True)
        self.assertEqual(self.baska_surec("sh"), "ALINDI")
        self.assertEqual(self.baska_surec("ex"), "DOLU")

    def test_bekleme_yok_hatasi_blockingioerror(self):
        uyum.kilitle(self.fd)
        fd2 = os.open(self.dosya, os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                uyum.kilitle(fd2, bekle=False)
            with self.assertRaises(BlockingIOError):
                uyum.kilitle(fd2, zaman_asimi=0.2)
        finally:
            os.close(fd2)

    def test_kilit_dosya_verisini_engellemez(self):
        uyum.kilitle(self.fd)
        self.dosya.write_text("veri", encoding="utf-8")  # farklı tutamaktan yazılabilmeli
        self.assertEqual(self.dosya.read_text(encoding="utf-8"), "veri")

    def test_kapatinca_kilit_duser(self):
        uyum.kilitle(self.fd)
        os.close(self.fd)
        self.fd = None
        self.assertEqual(self.baska_surec("ex"), "ALINDI")

    def test_baglam_yoneticisi(self):
        with uyum.kilit(self.fd):
            self.assertEqual(self.baska_surec("ex"), "DOLU")
        self.assertEqual(self.baska_surec("ex"), "ALINDI")


class DosyaTesti(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.TemporaryDirectory()
        self.addCleanup(self.kok.cleanup)
        self.yol = Path(self.kok.name)

    def test_degistir(self):
        a, b = self.yol / "a", self.yol / "b"
        a.write_text("yeni", encoding="utf-8")
        b.write_text("eski", encoding="utf-8")
        uyum.degistir(a, b)
        self.assertEqual(b.read_text(encoding="utf-8"), "yeni")
        self.assertFalse(a.exists())

    def test_baglanti_ve_guvenli_ac(self):
        hedef = self.yol / "hedef"
        hedef.mkdir()
        (hedef / "dosya.txt").write_text("x", encoding="utf-8")
        baglar = []
        if os.name == "nt":
            kavsak = self.yol / "kavsak"
            cikti = subprocess.run(["cmd", "/c", "mklink", "/J", str(kavsak), str(hedef)],
                                   capture_output=True, text=True)
            if cikti.returncode == 0:
                baglar.append(kavsak)
        else:
            sembolik = self.yol / "sembolik"
            os.symlink(hedef, sembolik)
            baglar.append(sembolik)
        self.assertFalse(uyum.baglanti_mi(hedef))
        self.assertFalse(uyum.baglanti_mi(self.yol / "yok"))
        for bag in baglar:
            self.assertTrue(uyum.baglanti_mi(bag), bag)
            with self.assertRaises(OSError):
                uyum.guvenli_ac(bag)
        fd = uyum.guvenli_ac(hedef / "dosya.txt")
        try:
            self.assertEqual(os.read(fd, 1), b"x")
        finally:
            os.close(fd)

    def test_sahip_ve_kullanici(self):
        bilgi = (self.yol).stat()
        self.assertTrue(uyum.sahibi_ben_mi(bilgi))
        self.assertTrue(uyum.kullanici_kimligi())


if __name__ == "__main__":
    unittest.main()
