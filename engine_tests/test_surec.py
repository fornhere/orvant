import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from orvant_op import ayarlar, uyum_surec, yurutucu  # noqa: E402

WINDOWS = os.name == "nt"
PY = sys.executable

# Çocuk: torun başlatır, PID'ini dosyaya yazar, ikisi de uzun uyur.
COCUK = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(g.pid))\n"
    "time.sleep(120)\n"
)


def canli(pid):
    """PID çalışıyor mu (zombi sayılmaz)."""
    if WINDOWS:
        return uyum_surec.surec_baslangici(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return uyum_surec.grup_canli(pid) or Path(f"/proc/{pid}/stat").exists() and \
        Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"


def bekle(kosul, sure=15.0):
    son = time.monotonic() + sure
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(0.05)
    return kosul()


class Gecici(unittest.TestCase):
    def setUp(self):
        gecici_kok = Path(__file__).resolve().parents[1] / ".scratch"
        gecici_kok.mkdir(exist_ok=True)
        self.kok = tempfile.TemporaryDirectory(dir=gecici_kok)
        self.addCleanup(self.kok.cleanup)
        self.dizin = Path(self.kok.name)
        self.yetimler = []
        self.addCleanup(self.temizle)

    def temizle(self):
        for pid in self.yetimler:
            try:
                if WINDOWS:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=5)
                else:
                    os.kill(pid, 9)
            except OSError:
                pass

    def cocuk_betigi(self):
        betik = self.dizin / "cocuk.py"
        betik.write_text(COCUK, encoding="utf-8")
        return betik, self.dizin / "torun.pid"

    def torun_pid(self, dosya):
        self.assertTrue(bekle(lambda: dosya.exists() and dosya.read_text(encoding="utf-8").strip()), "torun başlamadı")
        pid = int(dosya.read_text(encoding="utf-8"))
        self.yetimler.append(pid)
        return pid


class AgacOldurmeTesti(Gecici):
    @unittest.skipUnless(WINDOWS, "adlandırılmış Job Object Windows'a özgü")
    def test_lider_erken_ciksa_da_torun_iptal_edilir(self):
        betik = self.dizin / "erken.py"
        pidyolu = self.dizin / "erken_torun.pid"
        betik.write_text(
            "import subprocess, sys\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open(sys.argv[1], 'w', encoding='utf-8').write(str(g.pid))\n",
            encoding="utf-8")
        kayit, sonuc = {}, {}

        def kanca(pid):
            kayit["pid"] = pid
            kayit["baslangic"] = yurutucu.surec_baslangici(pid)
            self.addCleanup(yurutucu.grup_durdur, pid, kayit["baslangic"])

        def kos():
            try:
                with yurutucu.baslangic_kancasi(kanca):
                    sonuc["proc"] = yurutucu.grup_run([PY, str(betik), str(pidyolu)], timeout=20)
            except BaseException as exc:
                sonuc["hata"] = exc

        iparcacigi = threading.Thread(target=kos, daemon=True)
        iparcacigi.start()
        torun = self.torun_pid(pidyolu)
        self.assertTrue(bekle(lambda: yurutucu.surec_baslangici(kayit["pid"]) is None),
                        "lider çıkmadı")
        self.assertTrue(canli(torun), "torun erken bitti")
        durdur = yurutucu.grup_durdur(kayit["pid"], kayit["baslangic"], bekleme=2)
        iparcacigi.join(10)
        self.assertTrue(durdur["durduruldu"], durdur)
        self.assertFalse(iparcacigi.is_alive(), "çıktı borusu kapanmadı")
        self.assertNotIn("hata", sonuc)
        self.assertTrue(bekle(lambda: not canli(torun)), "torun hâlâ yaşıyor")

    @unittest.skipUnless(WINDOWS, "Job Object Windows'a özgü")
    def test_jobu_olmayan_surece_iptalde_dokunulmaz(self):
        proc = subprocess.Popen([PY, "-c", "import time; time.sleep(20)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            baslangic = yurutucu.surec_baslangici(proc.pid)
            self.assertIsNotNone(baslangic)
            sonuc = yurutucu.grup_durdur(proc.pid, baslangic, bekleme=1)
            self.assertFalse(sonuc["durduruldu"], sonuc)
            self.assertIsNone(proc.poll(), "yabancı süreç öldürüldü")
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_zaman_asiminda_cocuk_ve_torun_olur(self):
        betik, pidyolu = self.cocuk_betigi()
        zaman = [None]

        def kanca(pgid):
            zaman[0] = pgid

        with yurutucu.baslangic_kancasi(kanca):
            with self.assertRaises(subprocess.TimeoutExpired):
                yurutucu.grup_run([PY, str(betik), str(pidyolu)], timeout=4)
        torun = self.torun_pid(pidyolu)
        self.assertIsNotNone(zaman[0])
        self.assertTrue(bekle(lambda: not canli(torun)), "torun hâlâ yaşıyor")
        self.assertTrue(bekle(lambda: not canli(zaman[0])), "çocuk hâlâ yaşıyor")

    def test_iptal_kancasi_hatasinda_agac_olur(self):
        betik, pidyolu = self.cocuk_betigi()
        pgid = []

        def kanca(p):
            pgid.append(p)
            # İptal yarışı: torun oluşana kadar bekle, sonra kancadan istisna fırlat (EskiDeneme benzeri).
            self.assertTrue(bekle(lambda: pidyolu.exists() and pidyolu.read_text(encoding="utf-8").strip()))
            raise RuntimeError("iptal")

        with yurutucu.baslangic_kancasi(kanca):
            with self.assertRaises(RuntimeError):
                yurutucu.grup_run([PY, str(betik), str(pidyolu)], timeout=60)
        torun = self.torun_pid(pidyolu)
        self.assertTrue(bekle(lambda: not canli(torun)))
        self.assertTrue(bekle(lambda: not canli(pgid[0])))

    def test_grup_durdur_baska_iparcacigindan_agaci_oldurur(self):
        """Başka süreçten iptal: yalnız (pgid, başlangıç) ile ağaç ölür, grup_run normal döner."""
        betik, pidyolu = self.cocuk_betigi()
        kayit = {}

        def kanca(pgid):
            kayit["pgid"], kayit["baslangic"] = pgid, yurutucu.surec_baslangici(pgid)
            self.addCleanup(yurutucu.grup_durdur, pgid, kayit["baslangic"])

        sonuc = {}

        def kos():
            with yurutucu.baslangic_kancasi(kanca):
                sonuc["proc"] = yurutucu.grup_run([PY, str(betik), str(pidyolu)], timeout=60)

        iparcacigi = threading.Thread(target=kos, daemon=True)
        iparcacigi.start()
        torun = self.torun_pid(pidyolu)
        self.assertIsNotNone(kayit["baslangic"])
        durdur = yurutucu.grup_durdur(kayit["pgid"], kayit["baslangic"], bekleme=3.0)
        iparcacigi.join(20)
        self.assertFalse(iparcacigi.is_alive(), "grup_run dönmedi")
        self.assertTrue(durdur["durduruldu"], durdur)
        self.assertNotEqual(sonuc["proc"].returncode, 0)
        self.assertTrue(bekle(lambda: not canli(torun)))

    def test_pid_baska_surece_aitse_dokunmaz(self):
        sonuc = yurutucu.grup_durdur(os.getpid(), "yanlis-baslangic")
        self.assertFalse(sonuc["durduruldu"])
        self.assertIn("başka sürece", sonuc["neden"])

    def test_bitmis_grup_icin_durdur_zaten_bitmis_der(self):
        proc = subprocess.Popen([PY, "-c", "pass"])
        proc.wait()
        sonuc = yurutucu.grup_durdur(proc.pid, None, bekleme=1.0)
        self.assertFalse(sonuc["durduruldu"])


class CiktiTesti(Gecici):
    def test_turkce_cikti_ve_girdi_yakalanir(self):
        metin = "çğıöşü ÇĞİÖŞÜ — ₺"
        betik = self.dizin / "yaz.py"
        betik.write_text(
            "import sys\nsys.stdout.write(sys.stdin.read())\n"
            "sys.stderr.write('hata: ç ğ ı ö ş ü')\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        proc = yurutucu.grup_run([PY, str(betik)], input=metin, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, metin)
        self.assertEqual(proc.stderr, "hata: ç ğ ı ö ş ü")

    def test_cikis_kodu_ve_bozuk_bayt_cokmez(self):
        proc = yurutucu.grup_run(
            [PY, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb'); sys.exit(7)"], timeout=30)
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(proc.stdout, "a�b")

    def test_bulunamayan_komut_filename_tasir(self):
        argv = ["orvant-yok-boyle-bir-komut-xyz", "x"]
        with self.assertRaises(FileNotFoundError) as bag:
            yurutucu.grup_run(argv, timeout=10)
        self.assertEqual(bag.exception.filename, argv[0])
        self.assertEqual(bag.exception.errno, errno.ENOENT)


class BaslangicZamaniTesti(unittest.TestCase):
    def test_kararli_ve_metin(self):
        a = yurutucu.surec_baslangici(os.getpid())
        self.assertIsInstance(a, str)
        self.assertEqual(a, yurutucu.surec_baslangici(os.getpid()))

    def test_farkli_surecler_farkli_baslangic(self):
        proc = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(bekle(lambda: yurutucu.surec_baslangici(proc.pid) is not None))
            self.assertNotEqual(yurutucu.surec_baslangici(proc.pid), yurutucu.surec_baslangici(os.getpid()))
        finally:
            proc.kill()
            proc.wait()

    def test_olu_pid_none(self):
        proc = subprocess.Popen([PY, "-c", "pass"])
        proc.wait()
        # Popen nesnesi tutamağı açık tutsa bile çıkmış süreç ölü sayılır.
        self.assertIsNone(yurutucu.surec_baslangici(proc.pid))
        self.assertIsNone(yurutucu.surec_baslangici(2 ** 22 - 8))
        self.assertIsNone(yurutucu.surec_baslangici(0))


@unittest.skipUnless(WINDOWS, "ikili çözümleme Windows'a özgü")
class IkiliBulmaTesti(Gecici):
    def yol_ortami(self):
        return mock.patch.dict(os.environ, {"PATH": str(self.dizin) + os.pathsep + os.environ["PATH"],
                                            "PATHEXT": ".COM;.EXE;.BAT;.CMD"})

    def test_cmd_betigi_bulunur_ve_calisir(self):
        (self.dizin / "sahtekod.cmd").write_text("@echo off\r\necho arg1=%1 arg2=%2\r\n", encoding="ascii")
        with self.yol_ortami():
            self.assertEqual(Path(uyum_surec.ikili_yolu("sahtekod")), self.dizin / "sahtekod.cmd")
            proc = yurutucu.grup_run(["sahtekod", "bir", "iki ucu"], timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("arg1=bir", proc.stdout)
        self.assertIn('arg2="iki ucu"', proc.stdout)

    def test_cmd_betigi_metakarakterli_argumani_reddeder(self):
        (self.dizin / "sahtekod.cmd").write_text("@echo off\r\necho %*\r\n", encoding="ascii")
        with self.yol_ortami():
            with self.assertRaises(uyum_surec.BatKomutHatasi):
                uyum_surec.cozumle_argv(["sahtekod", "a&calc"])
            with self.assertRaises(uyum_surec.BatKomutHatasi):
                uyum_surec.cozumle_argv(["sahtekod", '{"a": 1}'])

    def test_npm_golgesi_gercek_ikiliye_cozulur(self):
        (self.dizin / "node_modules" / "paket" / "bin").mkdir(parents=True)
        exe = self.dizin / "node_modules" / "paket" / "bin" / "arac.exe"
        exe.write_bytes(b"MZ")
        (self.dizin / "arac.cmd").write_text(
            '@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\n'
            'CALL :find_dp0\r\n"%dp0%\\node_modules\\paket\\bin\\arac.exe"   %*\r\n', encoding="ascii")
        with self.yol_ortami():
            self.assertEqual(uyum_surec.cozumle_argv(["arac", "--json", '{"x": 1}']),
                             [str(exe), "--json", '{"x": 1}'])

    def test_npm_node_golgesi(self):
        (self.dizin / "node.exe").write_bytes(b"MZ")
        (self.dizin / "bin").mkdir()
        (self.dizin / "bin" / "arac.js").write_text("//", encoding="ascii")
        (self.dizin / "arac2.cmd").write_text(
            '@ECHO off\r\nSETLOCAL\r\nSET dp0=%~dp0\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n'
            ') ELSE (\r\n  SET "_prog=node"\r\n)\r\n'
            'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\bin\\arac.js" %*\r\n',
            encoding="ascii")
        with self.yol_ortami():
            self.assertEqual(uyum_surec.cozumle_argv(["arac2", "x"]),
                             [str(self.dizin / "node.exe"), str(self.dizin / "bin" / "arac.js"), "x"])

    def test_ps1_bulunur_ve_calisir(self):
        (self.dizin / "psarac.ps1").write_text("Write-Output ('ps:' + ($args -join '|'))\r\n", encoding="utf-8")
        with self.yol_ortami():
            yol = uyum_surec.ikili_yolu("psarac")
            self.assertEqual(Path(yol), self.dizin / "psarac.ps1")
            argv = uyum_surec.cozumle_argv(["psarac", "a", "b c"])
            self.assertEqual(argv[-3:], [yol, "a", "b c"][-3:])
            proc = yurutucu.grup_run(["psarac", "a", "b c"], timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ps:a|b c", proc.stdout)

    def test_exe_yolu_ve_cwd_taranmaz(self):
        # cwd'deki sahte komut PATH'te değilse bulunmamalı (CreateProcess/cwd ele geçirme).
        (self.dizin / "yalnizcwd.cmd").write_text("@echo off\r\n", encoding="ascii")
        eski = os.getcwd()
        os.chdir(self.dizin)
        try:
            self.assertIsNone(uyum_surec.ikili_yolu("yalnizcwd"))
        finally:
            os.chdir(eski)
        self.assertTrue(uyum_surec.ikili_yolu("cmd").lower().endswith("cmd.exe"))
        self.assertTrue(Path(uyum_surec.ikili_yolu(PY)).samefile(PY))

    def test_python3_yorumlayiciya_cevrilir(self):
        self.assertEqual(uyum_surec.cozumle_argv(["python3", "-V"]), [PY, "-V"])

    def test_ayarlar_ikiliyi_tam_yola_cozer(self):
        (self.dizin / "bosluklu dizin").mkdir()
        cmd = self.dizin / "bosluklu dizin" / "codex.cmd"
        cmd.write_text("@echo off\r\n", encoding="ascii")
        with mock.patch.dict(os.environ, {"ORVANT_CODEX": str(self.dizin / "bosluklu dizin" / "codex")}):
            self.assertEqual(Path(ayarlar.codex_ikili()), cmd)
        with mock.patch.dict(os.environ, {"ORVANT_CLAUDE": str(cmd)}):
            self.assertEqual(Path(ayarlar.claude_ikili()), cmd)


class AyarlarTesti(Gecici):
    def ayar(self, icerik, bom=False):
        yol = self.dizin / "orvant.toml"
        yol.write_bytes((b"\xef\xbb\xbf" if bom else b"") + icerik.encode("utf-8"))
        return mock.patch.dict(os.environ, {"ORVANT_AYAR": str(yol)})

    def test_utf8_ve_bom_okunur(self):
        for bom in (False, True):
            with self.ayar('[modeller]\nisci = "model-ç"\n[yollar]\ndepo = "klasör"\n', bom=bom):
                self.assertEqual(ayarlar.model("isci"), "model-ç")
                self.assertEqual(ayarlar.depo_koku().name, "klasör")

    def test_utf8_olmayan_dosya_ayar_hatasi(self):
        yol = self.dizin / "orvant.toml"
        yol.write_bytes('[modeller]\nisci = "ç"\n'.encode("cp1254"))
        with mock.patch.dict(os.environ, {"ORVANT_AYAR": str(yol)}):
            with self.assertRaises(ayarlar.AyarHatasi):
                ayarlar.model("isci")

    def test_yalitim_degerleri(self):
        with self.ayar('[kehanet]\nyalitim = "codex"\n'):
            self.assertEqual(ayarlar.kehanet_yalitimi(), "codex")
        with self.ayar('[kehanet]\nyalitim = "yok"\n'):
            with self.assertRaises(ayarlar.AyarHatasi):
                ayarlar.kehanet_yalitimi()
        with self.ayar('[kehanet]\nyalitim = "auto"\n'):
            self.assertEqual(ayarlar.kehanet_yalitimi(), "codex" if WINDOWS else "auto")
        with self.ayar('[kehanet]\nyalitim = "bwrap"\n'):
            if WINDOWS:
                with self.assertRaises(ayarlar.AyarHatasi):
                    ayarlar.kehanet_yalitimi()
            else:
                self.assertEqual(ayarlar.kehanet_yalitimi(), "bwrap")

    @unittest.skipUnless(WINDOWS, "APPDATA yapılandırması Windows'a özgü")
    def test_appdata_yapilandirmasi(self):
        (self.dizin / "orvant").mkdir()
        (self.dizin / "orvant" / "orvant.toml").write_text('[modeller]\nisci = "appdata-modeli"\n', encoding="utf-8")
        ortam = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME" and k != "ORVANT_AYAR"}
        ortam["APPDATA"] = str(self.dizin)
        ortam.pop("ORVANT_MODEL", None)
        ortam.pop("ORVANT_MODEL_ISCI", None)
        with mock.patch.dict(os.environ, ortam, clear=True):
            self.assertEqual(ayarlar.model("isci", self.dizin / "bos"), "appdata-modeli")


class KabukTesti(Gecici):
    def calistir(self, komut, **kw):
        argv, gerekce = yurutucu.komut_argv(komut)
        return argv, gerekce, yurutucu.grup_run(argv, shell=False, cwd=str(self.dizin), timeout=60, **kw)

    def test_basit_komut_kabuksuz_ve_ters_egik_cizgili_yol(self):
        komut = f'"{PY}" -c "import sys; print(\'merhaba\', len(sys.argv))" a b'
        argv, gerekce, proc = self.calistir(komut)
        self.assertIsNone(gerekce)
        self.assertEqual(argv[0], PY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "merhaba 3")

    def test_kabuk_gerektiren_komut_boru_ve_tirnak(self):
        if WINDOWS and not uyum_surec.git_bash():
            self.skipTest("Git bash yok; POSIX sözdizimi için kabuk gerekir")
        komut = "echo 'iki  kelime' | tr a-z A-Z && echo $((6*7))"
        argv, gerekce, proc = self.calistir(komut)
        self.assertTrue(gerekce.startswith("kabuk gerekli: |"), gerekce)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["IKI", "KELIME", "42"])

    @unittest.skipUnless(WINDOWS, "POSIX sözdizimi Windows kabuk seçimine özgü")
    def test_tek_tirnak_bash_ister_cmd_ile_sessizce_gecmez(self):
        with mock.patch.object(uyum_surec, "git_bash", return_value=None):
            with mock.patch.dict(os.environ, {"ORVANT_KABUK": ""}):
                with self.assertRaisesRegex(OSError, "Git Bash gerekli"):
                    yurutucu.komut_argv("echo 'iki kelime'")
                with self.assertRaisesRegex(OSError, "Git Bash gerekli"):
                    yurutucu.komut_argv("echo $HOME")

    @unittest.skipUnless(WINDOWS, "cmd.exe yedeği Windows'a özgü")
    def test_cmd_yedegi(self):
        with mock.patch.dict(os.environ, {"ORVANT_KABUK": "cmd"}):
            argv, gerekce, proc = self.calistir('echo birinci & echo "ikinci ürün" && exit /b 3')
        self.assertIn("(kabuk: cmd)", gerekce)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("birinci", proc.stdout)
        self.assertIn("ikinci", proc.stdout)

    def test_kabuk_cikis_kodu_ve_fail_closed(self):
        if WINDOWS and not uyum_surec.git_bash():
            self.skipTest("Git bash yok")
        _, _, proc = self.calistir("exit 5")
        self.assertEqual(proc.returncode, 5)
        # Var olmayan komut: kabuğa düşer ve sıfırdan farklı kodla biter (kabul ASLA geçmez).
        argv, gerekce = yurutucu.komut_argv("orvant-yok-komut-xyz --x")
        self.assertIsNone(gerekce)
        with self.assertRaises(OSError) as bag:
            yurutucu.grup_run(argv, shell=False, cwd=str(self.dizin), timeout=30)
        argv2, gerekce2 = yurutucu.kabuk_geri_dususu(bag.exception, argv, "orvant-yok-komut-xyz --x")
        self.assertIn("kabuk geri düşüşü", gerekce2)
        proc = yurutucu.grup_run(argv2, shell=False, cwd=str(self.dizin), timeout=30)
        self.assertNotEqual(proc.returncode, 0)

    def test_kabuk_ve_agac_zaman_asimi(self):
        """Kabuk üzerinden başlayan zincirde de torunlar zaman aşımında ölür."""
        if WINDOWS and not uyum_surec.git_bash():
            self.skipTest("Git bash yok")
        self.cocuk_betigi()
        pidyolu = self.dizin / "torun.pid"
        komut = "python3 cocuk.py torun.pid && echo bitti"
        argv, gerekce = yurutucu.komut_argv(komut)
        self.assertIsNotNone(gerekce)
        pgid = []
        with yurutucu.baslangic_kancasi(pgid.append):
            with self.assertRaises(subprocess.TimeoutExpired):
                yurutucu.grup_run(argv, cwd=self.dizin, timeout=4)
        torun = self.torun_pid(pidyolu)
        self.assertTrue(bekle(lambda: not canli(pgid[0])))
        self.assertTrue(bekle(lambda: not canli(torun)))

    def test_python3_kabukta_calisir(self):
        if WINDOWS and not uyum_surec.git_bash():
            self.skipTest("Git bash yok")
        argv, gerekce, proc = self.calistir("python3 -c 'print(1+1)' && echo bitti")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["2", "bitti"])


@unittest.skipUnless(WINDOWS, "Job Object Windows'a özgü")
class JobTesti(Gecici):
    def test_job_kapaninca_agac_olur(self):
        betik, pidyolu = self.cocuk_betigi()
        proc = uyum_surec.grup_baslat([PY, str(betik), str(pidyolu)], stdin=subprocess.DEVNULL)
        self.addCleanup(uyum_surec.grup_birak, proc)
        torun = self.torun_pid(pidyolu)
        self.assertTrue(uyum_surec.grup_canli(proc.pid))
        uyum_surec.grup_birak(proc)  # KILL_ON_JOB_CLOSE
        self.assertTrue(bekle(lambda: not canli(torun)))
        proc.wait(10)

    def test_job_adla_baska_tutamaktan_sonlandirilir(self):
        betik, pidyolu = self.cocuk_betigi()
        proc = uyum_surec.grup_baslat([PY, str(betik), str(pidyolu)], stdin=subprocess.DEVNULL)
        self.addCleanup(uyum_surec.grup_birak, proc)
        torun = self.torun_pid(pidyolu)
        baslangic = uyum_surec.surec_baslangici(proc.pid)
        with uyum_surec._isler_kilidi:
            gizli = uyum_surec._isler.pop(proc.pid)  # kayıtsız: yalnız ad üzerinden bulunabilmeli
        try:
            sonuc = uyum_surec.grup_durdur(proc.pid, 1.0, baslangic)
        finally:
            uyum_surec._k32.CloseHandle(gizli[0])
        self.assertTrue(sonuc["durduruldu"], sonuc)
        self.assertTrue(bekle(lambda: not canli(torun)))
        proc.wait(10)

    def test_baska_surecten_job_iptali(self):
        betik, pidyolu = self.cocuk_betigi()
        proc = uyum_surec.grup_baslat([PY, str(betik), str(pidyolu)], stdin=subprocess.DEVNULL)
        self.addCleanup(uyum_surec.grup_birak, proc)
        torun = self.torun_pid(pidyolu)
        baslangic = uyum_surec.surec_baslangici(proc.pid)
        kod = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
               "from orvant_op import yurutucu;"
               "print(json.dumps(yurutucu.grup_durdur(int(sys.argv[2]),sys.argv[3],bekleme=2)))")
        iptal = subprocess.run([PY, "-B", "-c", kod, str(Path(__file__).resolve().parents[1] / "engine"),
                               str(proc.pid), baslangic], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15)
        self.assertEqual(iptal.returncode, 0, iptal.stderr)
        self.assertTrue(json.loads(iptal.stdout)["durduruldu"], iptal.stdout)
        self.assertTrue(bekle(lambda: not canli(torun)))
        proc.wait(10)

    def test_askidan_cikan_surec_normal_calisir(self):
        proc = uyum_surec.grup_baslat([PY, "-c", "print('canli')"], stdout=subprocess.PIPE, text=True,
                                      stdin=subprocess.DEVNULL)
        cikti, _ = proc.communicate(timeout=30)
        uyum_surec.grup_birak(proc)
        self.assertEqual(cikti.strip(), "canli")


@unittest.skipIf(WINDOWS, "POSIX süreç grubu")
class PosixTesti(Gecici):
    def test_yeni_oturum_ve_killpg(self):
        betik, pidyolu = self.cocuk_betigi()
        proc = uyum_surec.grup_baslat([PY, str(betik), str(pidyolu)], stdin=subprocess.DEVNULL)
        torun = self.torun_pid(pidyolu)
        self.assertEqual(os.getpgid(proc.pid), proc.pid)
        uyum_surec.grup_oldur(proc, nazik=False)
        proc.wait(10)
        self.assertTrue(bekle(lambda: not canli(torun)))

    def test_kabuk_argv(self):
        self.assertEqual(uyum_surec.kabuk_argv("a | b"), ["/bin/sh", "-c", "a | b"])
        self.assertEqual(uyum_surec.grup_kwargs(), {"start_new_session": True})


if __name__ == "__main__":
    unittest.main()
