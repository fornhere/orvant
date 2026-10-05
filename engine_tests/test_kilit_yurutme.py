import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from orvant_op import proje, uyum
from orvant_op.yurutme.akis import _ekle
from orvant_op.yurutme.zamanlayici import YurutmeKilidi
from orvant_gelisim import kayit

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")


def _alt_surec(kod, *args, timeout=10):
    """Test alt surecini UTF-8 cikti ile calistirir; cp1254 ortaminda bile bozulmaz."""
    proc = subprocess.run(
        [sys.executable, "-c", kod, ENGINE, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)
    return proc


class YurutmeKilidiTesti(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.TemporaryDirectory()
        self.addCleanup(self.kok.cleanup)

    def test_ayni_surecte_yeniden_girilebilir(self):
        with YurutmeKilidi(self.kok.name):
            with YurutmeKilidi(self.kok.name):
                pass

    def test_baska_surecte_bekleyerek_alir(self):
        kod = """\
import sys
sys.path.insert(0, sys.argv[1])
from orvant_op.yurutme.zamanlayici import YurutmeKilidi
with YurutmeKilidi(sys.argv[2]):
    print("ALINDI")
"""
        import time
        with YurutmeKilidi(self.kok.name):
            proc = subprocess.Popen(
                [sys.executable, "-c", kod, ENGINE, self.kok.name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace")
            time.sleep(0.3)
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "ALINDI")

    def test_baska_surecte_uyum_kilidi_dolu(self):
        kod = """\
import sys, os
sys.path.insert(0, sys.argv[1])
from orvant_op import uyum
fd = os.open(sys.argv[2], os.O_RDWR | os.O_CREAT, 0o666)
try:
    uyum.kilitle(fd, bekle=False)
    print("ALINDI")
except BlockingIOError:
    print("DOLU")
finally:
    uyum.kilit_birak(fd)
    os.close(fd)
"""
        kilit_yolu = Path(self.kok.name) / "yurutme" / ".kilit"
        with YurutmeKilidi(self.kok.name):
            proc = _alt_surec(kod, str(kilit_yolu))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "DOLU")


class ProjeKilidiTesti(unittest.TestCase):
    def test_lock_baska_surecte_reddeder(self):
        kod = """\
import sys
sys.path.insert(0, sys.argv[1])
from orvant_op import proje
try:
    with proje._lock(sys.argv[2]):
        print("ALINDI")
except ValueError as e:
    if "another project controller is running" in str(e):
        print("REDDEDILDI")
    else:
        print("HATA " + str(e))
"""
        with tempfile.TemporaryDirectory() as tmp:
            with proje._lock(tmp):
                proc = _alt_surec(kod, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "REDDEDILDI")


class GoreliYolTesti(unittest.TestCase):
    def test_goreli_mi_windows_normcase(self):
        with tempfile.TemporaryDirectory() as tmp:
            alt = Path(tmp) / "a" / "b"
            alt.mkdir(parents=True)
            ust = Path(tmp) / "A"
            self.assertTrue(proje._goreli_mi(alt, ust))
            self.assertFalse(proje._goreli_mi(ust, alt))

    def test_goreli_mi_duzgun_kok(self):
        with tempfile.TemporaryDirectory() as tmp:
            alt = Path(tmp) / "alt"
            alt.mkdir()
            self.assertTrue(proje._goreli_mi(alt, tmp))
            self.assertFalse(proje._goreli_mi(tmp, alt))


class AkisGunuTesti(unittest.TestCase):
    def test_ekle_crlf_yok(self):
        with tempfile.TemporaryDirectory() as tmp:
            yol = Path(tmp) / "olaylar.jsonl"
            _ekle(yol, {"id": "x", "mesaj": "Turkce: Isci"})
            ham = yol.read_bytes()
            self.assertNotIn(b"\r\n", ham)
            self.assertTrue(ham.endswith(b"\n"))


class KayitKilitTesti(unittest.TestCase):
    def test_yaz_ve_oku(self):
        with tempfile.TemporaryDirectory() as tmp:
            yol = Path(tmp) / "iz.jsonl"
            item = kayit.olay(proje="test", is_turu="orvant_kayit", aktor_tur="orvant",
                              aktor_kimlik="orvant", sonuc="ok", ozet="deneme")
            kayit.yaz(yol, item)
            okunan = kayit.oku(yol)
            self.assertEqual(len(okunan), 1)
            self.assertEqual(okunan[0]["id"], item["id"])


class ProjeAkisEntegrasyonTesti(unittest.TestCase):
    def test_lock_altinda_akis_yazar(self):
        with tempfile.TemporaryDirectory() as tmp:
            with proje._lock(tmp):
                yol = Path(tmp) / "yurutme" / "olaylar.jsonl"
                _ekle(yol, {"tur": "test", "veri": 1})
            ham = yol.read_bytes()
            self.assertIn(b'"tur": "test"', ham)
            self.assertNotIn(b"\r\n", ham)


if __name__ == "__main__":
    unittest.main()
