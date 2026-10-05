import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE))
from orvant_op import uyum_komut as uk  # noqa: E402

ORNEKLER = [
    ["python", "-m", "pytest"],
    ["C:\\Program Files\\Py thon\\python.exe", "-m", "x"],
    ["a b", 'c"d', "", "O'Brien", "C:\\x y\\", "Türkçe ğüşıöçİ ışık", "a\\\\\"b", "<g>", "x;y", "50%", "a&b|c"],
    ["C:\\Users\\kullanici\\Documents\\proje\\tests\\test_x.py", "--opt=değer içinde boşluk"],
]


class GidisDonusTesti(unittest.TestCase):
    def test_windows_gidis_donus(self):
        with mock.patch.object(uk, "WINDOWS", True):
            for parcalar in ORNEKLER:
                with self.subTest(parcalar=parcalar):
                    self.assertEqual(uk.bol(uk.birlestir(parcalar)), parcalar)
                    for p in parcalar:
                        self.assertEqual(uk.bol(uk.tirnakla(p)), [p])

    def test_posix_gidis_donus(self):
        with mock.patch.object(uk, "WINDOWS", False):
            for parcalar in ORNEKLER:
                with self.subTest(parcalar=parcalar):
                    self.assertEqual(uk.bol(uk.birlestir(parcalar)), parcalar)

    def test_windows_ters_egik_cizgi_ve_tirnak(self):
        with mock.patch.object(uk, "WINDOWS", True):
            self.assertEqual(uk.bol(r"python3 tests\test_x.py"), ["python3", r"tests\test_x.py"])
            self.assertEqual(uk.bol(r'"C:\Program Files\x\a.exe" -k "a b"'), [r"C:\Program Files\x\a.exe", "-k", "a b"])
            self.assertEqual(uk.bol("-k 'a b' --opt='x y'"), ["-k", "a b", "--opt=x y"])
            self.assertEqual(uk.bol(r"C:\Users\O'Brien\x.py"), [r"C:\Users\O'Brien\x.py"])
            self.assertEqual(uk.bol(r'a\"b'), ['a"b'])
            self.assertEqual(uk.bol('"a""b"'), ['a"b'])  # tırnak içinde "" -> değişmez tırnak
            self.assertEqual(uk.bol('""'), [""])
            with self.assertRaises(ValueError):
                uk.bol('a "b')

    def test_posix_dali_degismedi(self):
        with mock.patch.object(uk, "WINDOWS", False):
            self.assertEqual(uk.bol("python3 -m x 'a b'"), ["python3", "-m", "x", "a b"])
            self.assertEqual(uk.birlestir(["python3", "-m", "x", "a b"]), "python3 -m x 'a b'")
            self.assertEqual(uk.orvant_komutu(["yurut", "geri-al", "/tmp/a b"]),
                             "python3 -m orvant_op yurut geri-al '/tmp/a b'")
            self.assertEqual(uk.prog("karsila"), "python3 -m orvant_op karsila")
            self.assertEqual(uk.argv_uyarla(["python3", "-m", "x"]), ["python3", "-m", "x"])

    def test_islecli_bolme(self):
        with mock.patch.object(uk, "WINDOWS", True):
            self.assertEqual(uk.bol_islecli(r"cd src && python3 tests\a.py;echo 'a;b'|cat"),
                             ["cd", "src", "&&", "python3", r"tests\a.py", ";", "echo", "a;b", "|", "cat"])
        with mock.patch.object(uk, "WINDOWS", False):
            self.assertEqual(uk.bol_islecli("cd src && python3 tests/a.py;echo 'a;b'|cat"),
                             ["cd", "src", "&&", "python3", "tests/a.py", ";", "echo", "a;b", "|", "cat"])

    def test_python_adi(self):
        with mock.patch.object(uk, "WINDOWS", True):
            for ad in ("python", "python3", "PYTHON.EXE", "py", "python3.12", r"C:\x y\python.exe"):
                self.assertTrue(uk.python_adi_mi(ad), ad)
            for ad in ("pythonista", "pip", "node"):
                self.assertFalse(uk.python_adi_mi(ad), ad)
            self.assertEqual(uk.python_modul_indeksi(["env", "PY", "-3", "-m", "pkg.mod"]), 4)
            self.assertEqual(uk.python_modul_indeksi(["PYTHON.EXE", "-m", "pkg"]), 2)
            self.assertIsNone(uk.python_modul_indeksi(["python", "x.py"]))
            self.assertEqual(uk.argv_uyarla(["python3", "-m", "x"]), [sys.executable, "-m", "x"])
            self.assertEqual(uk.argv_uyarla(["PY", "x.py"]), [sys.executable, "x.py"])
            # yol içeren ad ve sürüm seçen py olduğu gibi kalır
            self.assertEqual(uk.argv_uyarla([r".\venv\Scripts\python.exe", "x"]), [r".\venv\Scripts\python.exe", "x"])
            self.assertEqual(uk.argv_uyarla(["py", "-3.11", "x"]), ["py", "-3.11", "x"])
            self.assertEqual(uk.argv_uyarla(["git", "status"]), ["git", "status"])
        with mock.patch.object(uk, "WINDOWS", False):
            self.assertTrue(uk.python_adi_mi("python3"))
            self.assertFalse(uk.python_adi_mi("py"))
            self.assertFalse(uk.python_adi_mi("/usr/bin/python3"))  # eski davranış: birebir ad

    def test_orvant_komutu_windows(self):
        with mock.patch.object(uk, "WINDOWS", True), mock.patch.object(uk, "_gosterim_python", return_value="python"):
            self.assertEqual(uk.orvant_komutu(["yurut", "durum", "C:\\a b\\c"]),
                             'python -m orvant_op yurut durum "C:\\a b\\c"')
        with mock.patch.object(uk, "WINDOWS", True), mock.patch.object(uk, "_gosterim_python", return_value="C:\\P F\\python.exe"):
            self.assertEqual(uk.orvant_komutu(["karsila"]), '& "C:\\P F\\python.exe" -m orvant_op karsila')


@unittest.skipUnless(os.name == "nt", "Windows yollarını doğrular")
class DogrulamaTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from orvant_op.mimar import dogrulama
        except ImportError as exc:  # başka ajanların modülleri henüz içe aktarılamıyor olabilir
            raise unittest.SkipTest(f"dogrulama içe aktarılamadı: {exc}")
        cls.d = dogrulama

    def yollar(self, komut):
        return self.d.komut_yollari(komut)

    def test_python_adlari_ayni_sonuc(self):
        beklenen = ["tests/test_a.py"]
        for on in ("python3", "python", "py -3", "PYTHON.EXE", f'"{sys.executable}"'):
            with self.subTest(on=on):
                self.assertEqual(self.yollar(on + r" tests\test_a.py"), beklenen)
        for on in ("python3", "py -3", "Python.exe"):
            self.assertEqual(self.yollar(on + " -m pkg.arac"), ["pkg/arac.py", "pkg/arac/__main__.py"])
            self.assertEqual(self.yollar(on + " -m pytest"), [])

    def test_yollar_ve_ayraclar(self):
        self.assertEqual(self.yollar(r"python3 .\tests\a.py && python3 scripts\b.py;python3 c.py"),
                         ["tests/a.py", "scripts/b.py", "c.py"])
        self.assertEqual(self.yollar(r"python3 'C:\dis\a.py' ..\x.py \\sunucu\a.py"), [])
        self.assertEqual(self.yollar('python3 "tests\\dizin adi\\a.py"'), ["tests/dizin adi/a.py"])
        self.assertEqual(self.yollar(r"python3 TESTS\A.PY"), ["TESTS/A.PY"])
        self.assertEqual(self.yollar(r'bash.exe -c "python3 tests\a.py"'), ["tests/a.py"])
        self.assertEqual(self.yollar(r'cmd /c "python tests\a.py && python b.py"'), ["tests/a.py", "b.py"])
        self.assertEqual(self.yollar(r'powershell -Command "python tests\a.py"'), ["tests/a.py"])

    def test_kalip_eslesir(self):
        k = self.d.kalip_eslesir
        self.assertTrue(k("src/a/b.py", "src/**"))
        self.assertTrue(k(r"src\a\b.py", "src/**"))
        self.assertTrue(k("SRC/A.PY", "src/*.py"))
        self.assertFalse(k("other/a.py", "src/**"))
        self.assertFalse(k("src/a.py", r"src\**"))      # ters eğik çizgili kalıp reddedilir
        self.assertFalse(k("a.py", "C:/a.py"))
        self.assertFalse(k("a.py", "../a.py"))
        self.assertFalse(k("a.py", "/a.py"))


@unittest.skipUnless(os.name == "nt", "Windows yoklama biçimlerini doğrular")
class YoklamaTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from orvant_op.karsilama import durum
        except ImportError as exc:
            raise unittest.SkipTest(f"durum içe aktarılamadı: {exc}")
        cls.dg = staticmethod(durum._yoklama_dogrula)

    def kabul(self, komut):
        self.dg(komut)

    def ret(self, komut):
        with self.assertRaises(ValueError, msg=komut):
            self.dg(komut)

    def test_izinliler(self):
        for k in ("uname -a", "python3 --version", "python --version", "PYTHON.EXE --version", "py --version",
                  f'"{sys.executable}" --version', "git status --short", "GIT.EXE rev-parse HEAD", "rg --files",
                  "ls -la", "pwd", "nvidia-smi", "command -v ffmpeg", "where.exe ffmpeg", "where ffmpeg",
                  "Get-Command ffmpeg"):
            with self.subTest(k=k):
                self.kabul(k)

    def test_yasaklilar(self):
        for k in ("", "rm -rf x", "python --version --help", "python -c pass", "C:\\evil\\python.exe --version",
                  r".\git.exe status --short", r"C:\x\git.exe status --short", "git push", "git status",
                  "where.exe /R C:\\ x", "where a b", "Get-Command -All x", "where ffmpeg & calc",
                  "curl http://x", "py -3 --version", "rg --files x", "ls -R", "pwd x"):
            with self.subTest(k=k):
                self.ret(k)


class KonsolTesti(unittest.TestCase):
    """Alt süreç, PYTHONIOENCODING/PYTHONUTF8 OLMADAN (varsayılan cp1254) Türkçe + cp1254 dışı karakter yazar."""
    METIN = "ığüşöçİ Ğ → ✓ ünlü"

    def calistir(self, kod, giris=None):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        env["PYTHONPATH"] = str(ENGINE)
        return subprocess.run([sys.executable, "-c", kod], input=giris, capture_output=True, env=env, timeout=60)

    @unittest.skipUnless(os.name == "nt", "cp1254 kontrolü Windows'a özgü")
    def test_kontrol_grubu_cp1254_dusuyor(self):
        kod = f"import sys; print(sys.stdout.encoding); print({self.METIN!r})"
        sonuc = self.calistir(kod)
        ilk = sonuc.stdout.split()[:1]
        if ilk and ilk[0].lower().replace(b"-", b"") == b"utf8":
            self.skipTest("bu ortamda varsayılan kodlama zaten UTF-8")
        self.assertNotEqual(sonuc.returncode, 0)
        self.assertIn(b"UnicodeEncodeError", sonuc.stderr)

    @unittest.skipUnless(os.name == "nt", "komut satırı dizisi yalnız Windows'ta doğrudan çalıştırılabilir")
    def test_os_gidis_donus(self):
        """`birlestir` çıktısı gerçek CommandLineToArgvW ile ayrıştırılır; `bol` ile bire bir aynıdır.

        Alt sürece argv LİSTESİ değil komut satırı DİZİSİ geçirilir (Windows'ta `subprocess`
        dizgiyi doğrudan komut satırı yapar); çocuk kendi `sys.argv`'sini yazar. Tek tırnak
        içeren girdi yok: `bol`un POSIX tarzı tek tırnak uzantısı bilinçli bir eklentidir.
        """
        kod = "import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=True))"
        durumlar = [
            ["python", "-m", "pytest", "tests/test_x.py"],
            ["C:\\Program Files\\Py thon\\python.exe", "-m", "x"],
            ["a b", 'c"d', "", "O'Brien", "C:\\x y\\", "Türkçe ğüşıöçİ ışık",
             'a\\\\"b', "<g>", "x;y", "50%", "a&b|c", "--opt=değer içinde"],
            ['ünide """q""" iç', '""', "..\\x\\\\", "a\\", 'a\\"b'],
        ]
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        for durum in durumlar:
            with self.subTest(durum=durum):
                komut = uk.birlestir([sys.executable, "-c", kod, *durum])
                sonuc = subprocess.run(komut, capture_output=True, env=env, timeout=60)
                self.assertEqual(sonuc.returncode, 0, sonuc.stderr)
                gercek = json.loads(sonuc.stdout.decode("ascii"))
                # çocukta sys.argv[0]="-c", sys.argv[1:]=durum; bol'de ilk 3 sözcük python -c KOD
                self.assertEqual(gercek, durum)
                self.assertEqual(uk.bol(komut)[3:], gercek)
        # El yazması komut satırında `""` -> değişmez tırnak kuralı da gerçek ayrıştırıcıyla aynı.
        komut = uk.birlestir([sys.executable, "-c", kod]) + ' "a""b" ""'
        sonuc = subprocess.run(komut, capture_output=True, env=env, timeout=60)
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr)
        self.assertEqual(json.loads(sonuc.stdout.decode("ascii")), ['a"b', ""])
        self.assertEqual(uk.bol(komut)[3:], ['a"b', ""])

    def test_boruya_utf8_baytlari(self):
        kod = f"from orvant_op.uyum_komut import konsolu_utf8_yap; konsolu_utf8_yap(); import json, sys; " \
              f"print(json.dumps({{'m': {self.METIN!r}}}, ensure_ascii=False)); print({self.METIN!r}, file=sys.stderr)"
        sonuc = self.calistir(kod)
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr)
        self.assertIn(self.METIN.encode("utf-8"), sonuc.stdout)
        self.assertIn(self.METIN.encode("utf-8"), sonuc.stderr)
        self.assertNotIn(b"\r\r", sonuc.stdout)

    @unittest.skipUnless(os.name == "nt", "stdin yeniden yapılandırması Windows'ta")
    def test_stdin_utf8_ve_bom(self):
        kod = "from orvant_op.uyum_komut import konsolu_utf8_yap; konsolu_utf8_yap(); import sys; " \
              "veri = sys.stdin.read(); sys.stdout.write(repr(veri))"
        sonuc = self.calistir(kod, giris=("\ufeff" + self.METIN).encode("utf-8"))
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr)
        self.assertEqual(sonuc.stdout.decode("utf-8"), repr(self.METIN))

    def test_cli_help_utf8(self):
        kod = "from orvant_op.__main__ import main; import sys; sys.argv=['orvant','--help']; raise SystemExit(main())"
        sonuc = self.calistir(kod)
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr.decode("utf-8", "replace"))
        self.assertIn(b"karsila", sonuc.stdout)
        # Alt komutların --help'i Türkçe/UTF-8 dışı karakterli yardımları cp1254 borusunda düşürmemeli.
        # (`yurut` tek başına alt eylem yardımları listelemez; `yurut ac --help` Türkçe yardım içerir.)
        for kuyruk, aranan in ((["karsila", "--help"], "Karşılama oturumu aç"),
                               (["mimar", "--help"], "şema-3 proje kaydına aktar"),
                               (["yurut", "ac", "--help"], "Engelli/ret görevi yeniden açar"),
                               (["operator", "--help"], "Yetkili işleri sınırlar içinde sürdür"),
                               (["proje", "--help"], "native plan")):
            alt = kuyruk[0]
            sysargv = ",".join(f"'{x}'" for x in ["orvant", *kuyruk])
            kod = f"from orvant_op.__main__ import main; import sys; sys.argv=[{sysargv}]; raise SystemExit(main())"
            sonuc = self.calistir(kod)
            if sonuc.returncode != 0 and b"Error" in sonuc.stderr and b"import" in sonuc.stderr.lower():
                self.skipTest(f"{alt} zinciri henüz içe aktarılamıyor: " + sonuc.stderr.decode("utf-8", "replace")[-200:])
            self.assertEqual(sonuc.returncode, 0, sonuc.stderr.decode("utf-8", "replace"))
            self.assertIn(aranan.encode("utf-8"), sonuc.stdout)


if __name__ == "__main__":
    unittest.main()
