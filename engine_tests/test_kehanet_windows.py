"""Windows kehanet yalıtımı: codex sandbox yoklaması, fail-closed davranış ve uçtan uca kehanet.

Gerçek `codex sandbox` çağrıları ~1 sn sürer; her çağrıya zaman aşımı verilir. Sandbox (elevated codex
Windows sandbox + sandbox'ın çalıştırabildiği Python kopyası) yoksa ilgili testler nedenle atlanır.
Fail-closed testlerinin çoğu sandbox gerektirmez. Unelevated kip bu makinede SINANMADI (bkz. rapor).
Dizinler `tempfile.mkdtemp` ile AÇILMAZ: Python 3.12.4+ Windows'ta mkdtemp dizinine yalnız sahibine
erişim veren ACL koyar, sandbox kullanıcısı kapı ağacını göremez.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from orvant_op import ayarlar, uyum  # noqa: E402
from orvant_op.mimar import kehanet  # noqa: E402
from orvant_op.mimar import yalitim_windows as yw  # noqa: E402
from orvant_op.yurutucu import grup_run  # noqa: E402

ZAMAN = 90
_durum = None


def sil(yol):
    """git nesne dosyaları Windows'ta salt okunurdur; rmtree onları ancak öznitelik kalkınca siler."""
    def salt_okunuru_kaldir(islev, yol, _):
        os.chmod(yol, stat.S_IWRITE)
        islev(yol)
    shutil.rmtree(yol, onexc=salt_okunuru_kaldir)


def _kok():
    # Türkçe karakterli yol: komut satırı, codex ortamı ve git çıktısında kodlama hatası görünsün.
    kok = Path(tempfile.gettempdir()) / f"orvant-kw-çğ-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    kok.mkdir()
    return kok


def sandbox_durumu():
    """(python, codex, None) ya da (None, None, atlama nedeni); bir kez yoklanır."""
    global _durum
    if _durum is None:
        python, neden = yw.python_yolu()
        codex = ayarlar.codex_ikili()
        if python is None:
            _durum = (None, None, neden)
        elif not shutil.which(codex):
            _durum = (None, None, f"codex yok: {codex}")
        else:
            kok = _kok()
            try:
                env = yw.codex_ortami({"PATH": os.environ.get("PATH", "")})
                tamam, neden = yw.yokla(codex, python, kok, env, yw.dis_yollar(kok, python=python),
                                        zaman_asimi=ZAMAN)
            finally:
                sil(kok)
            _durum = ((python, codex, None) if tamam else
                      (None, None, "sandbox yalıtımı kanıtlanamadı (elevated değil ya da kurulu değil): " + neden))
    return _durum


def kapi_kur(test, betik, *, dosyalar=None, ciktilar=()):
    """Kapı ağacı (git deposu) + bağımsız plan dizini; (kehanet yolu, ağaç)."""
    kok = _kok()
    test.addCleanup(sil, kok)
    agac, calisma = kok / "agac", kok / "calisma"
    agac.mkdir()
    for ad, icerik in (dosyalar or {"veri.txt": "çğış ÖÜ\n"}).items():
        (agac / ad).parent.mkdir(parents=True, exist_ok=True)
        (agac / ad).write_text(icerik, encoding="utf-8", newline="\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(agac)]
    for komut in (["git", "init", "-q", str(agac)], git + ["add", "-A"], git + ["commit", "-qm", "ilk"]):
        subprocess.run(komut, check=True, capture_output=True, timeout=60)
    (calisma / "plan" / "kehanetler").mkdir(parents=True)
    (calisma / "plan" / "plan.json").write_text(
        json.dumps({"gorevler": [{"id": "T1", "ciktilar": list(ciktilar)}]}), encoding="utf-8")
    yol = calisma / "plan" / "kehanetler" / "T1.py"
    yol.write_text(betik, encoding="utf-8", newline="\n")
    return yol, agac


def koruma_eki_ad_alani(agac):
    """KORUMA_EKI yardımcılarını önsözdeki gibi bir ad alanında tanımlar (yalnız Windows)."""
    ad_alani = {"os": os, "sys": sys, "subprocess": subprocess, "time": time,
                "SALT_OKUNUR": "salt", "ISCI_AGAC": str(agac)}
    exec("def altinda(yol, kok):\n"
         "    yol, kok = os.path.normcase(yol), os.path.normcase(kok)\n"
         "    return yol == kok or yol.startswith(kok + os.sep)\n"
         "def arac_adi(yol):\n"
         "    return os.path.basename(yol).lower().removesuffix('.exe')\n", ad_alani)
    exec(yw.KORUMA_EKI, ad_alani)
    return ad_alani


BASIT = '''"""1. veri.txt Türkçe içeriği taşır. 2. git ls-files dosyayı izler. 3. işçi programı UTF-8 çalışır.
4. Tam yollu git çalışır, GIT_ ortamı reddedilir, ağaca yazma reddedilir."""
import json, os, pathlib, shutil, subprocess
k = []
metin = open("veri.txt").read()
k.append({"ad": "icerik", "gecti": metin.strip() == "çğış ÖÜ", "ayrinti": metin[:40]})
girdiler = json.loads(os.environ["ORVANT_GIRDILER"])
k.append({"ad": "girdiler", "gecti": girdiler == ["ğüşi"], "ayrinti": str(girdiler)})
g = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
k.append({"ad": "git", "gecti": g.returncode == 0 and "veri.txt" in g.stdout, "ayrinti": g.stderr[-200:]})
t = subprocess.run([shutil.which("git"), "--version"], capture_output=True, text=True)
k.append({"ad": "tam_yol_git", "gecti": t.returncode == 0, "ayrinti": t.stdout[-100:]})
s = isci_calistir("arac/isci.py", ["ğ"], girdi="ç\\n", zaman_asimi=30)
k.append({"ad": "isci", "gecti": s["rc"] == 0 and s["stdout"].strip() == "ç|ğ" and not s["zaman_asimi"],
          "ayrinti": repr(s)[:300]})
def reddedilir(ad, islev, parca):
    try:
        islev()
    except PermissionError as exc:
        k.append({"ad": ad, "gecti": parca in str(exc), "ayrinti": str(exc)})
    except Exception as exc:
        k.append({"ad": ad, "gecti": False, "ayrinti": type(exc).__name__ + ": " + str(exc)})
    else:
        k.append({"ad": ad, "gecti": False, "ayrinti": "reddedilmedi"})
reddedilir("git_ortami", lambda: subprocess.run(["git", "status"], env={"GIT_DIR": "x"}, capture_output=True),
           "ortam")
reddedilir("dosya_yazma", lambda: pathlib.Path("yeni.txt").write_text("x"), "yazamaz")
try:
    subprocess.run(["ffprobe", "-version"], capture_output=True)
    k.append({"ad": "olmayan_arac", "gecti": bool(shutil.which("ffprobe")), "ayrinti": "çalıştı"})
except FileNotFoundError:
    k.append({"ad": "olmayan_arac", "gecti": not shutil.which("ffprobe"), "ayrinti": "araç yok"})
except Exception as exc:
    k.append({"ad": "olmayan_arac", "gecti": False, "ayrinti": type(exc).__name__ + ": " + str(exc)})
b = isci_calistir("arac/buyuk.py", zaman_asimi=30)
k.append({"ad": "kesildi", "gecti": b["kesildi"] and len(b["stdout"]) == 1048576 and b["rc"] == 0
          and not b["zaman_asimi"], "ayrinti": repr({**b, "stdout": len(b["stdout"])})[:300]})
print(json.dumps({"gecti": all(x["gecti"] for x in k), "kontroller": k}))
'''

ARAC_GUVENLIGI = '''"""1. Kapı ağacına konmuş sahte git çalıştırılamaz. 2. Popen dışı süreç, ctypes ve kanonik olmayan
komut satırı reddedilir."""
import json, subprocess, importlib, _winapi
k = []
def reddedilir(ad, islev):
    try:
        islev()
    except PermissionError as exc:
        k.append({"ad": ad, "gecti": True, "ayrinti": str(exc)})
    except Exception as exc:
        k.append({"ad": ad, "gecti": False, "ayrinti": type(exc).__name__ + ": " + str(exc)})
    else:
        k.append({"ad": ad, "gecti": False, "ayrinti": "reddedilmedi"})
reddedilir("sahte_git", lambda: subprocess.run(["git", "--version"], capture_output=True))
reddedilir("winapi", lambda: _winapi.CreateProcess(None, "cmd.exe /c echo x", None, None, 0, 0, None, None, None))
reddedilir("ctypes", lambda: importlib.import_module("ctypes"))
reddedilir("kanonik_olmayan", lambda: subprocess.run("git  status", capture_output=True))
print(json.dumps({"gecti": all(x["gecti"] for x in k), "kontroller": k}))
'''

AGAC_YAZAN = '''"""1. İşçi programı kapı ağacına yazamaz (OS sandbox)."""
import json
s = isci_calistir("yaz.py", zaman_asimi=30)
k = [{"ad": "isci_yazamadi", "gecti": s["rc"] != 0 and "PermissionError" in s["stderr"], "ayrinti": s["stderr"][-300:]}]
print(json.dumps({"gecti": all(x["gecti"] for x in k), "kontroller": k}))
'''

ZAMAN_ASIMI = '''"""1. İşçi süresi dolunca bütün süreç ağacı (torun dahil) ölür."""
import json, time, _winapi
bas = time.monotonic()
s = isci_calistir("uyu.py", zaman_asimi=3)
sure = time.monotonic() - bas
parcalar = s["stdout"].split()
pid = int(parcalar[0]) if parcalar else 0
def olu(pid):
    try:
        h = _winapi.OpenProcess(0x00100000, False, pid)
    except OSError:
        return True
    try:
        return _winapi.WaitForSingleObject(h, 2000) == 0
    finally:
        _winapi.CloseHandle(h)
k = [{"ad": "zaman_asimi", "gecti": s["zaman_asimi"] is True, "ayrinti": repr(s)[:200]},
     {"ad": "sure", "gecti": sure < 20, "ayrinti": str(sure)},
     {"ad": "torun_oldu", "gecti": pid > 0 and olu(pid), "ayrinti": str(pid)}]
print(json.dumps({"gecti": all(x["gecti"] for x in k), "kontroller": k}))
'''

GECER = '''"""1. Her zaman geçer (yalıtım denetimi için)."""
import json
print(json.dumps({"gecti": True, "kontroller": [{"ad": "x", "gecti": True, "ayrinti": ""}]}))
'''


@unittest.skipUnless(uyum.WINDOWS, "yalnız Windows")
class KomutSatiriTesti(unittest.TestCase):
    """Popen audit olayı Windows'ta komut satırı METNİ verir; önsöz onu kanonik biçimde çözmeli."""

    def setUp(self):
        self.ns = koruma_eki_ad_alani(Path.cwd())

    def test_win_bol_list2cmdline_tersi(self):
        for argv in (["git", "status"], [r"C:\Program Files\Git\cmd\git.exe", "-C", r"C:\a b\c"],
                     ["x", 'a"b', "", "son\\", 'c\\"d', "ç ğ", "\t", "a\\\\b"]):
            self.assertEqual(self.ns["win_bol"](subprocess.list2cmdline(argv)), argv)

    def test_kanonik_olmayan_komut_satiri_reddedilir(self):
        for satir in ("git  status", '"git" status', "git status ", 'git "a"b'):
            with self.assertRaises(PermissionError, msg=satir):
                self.ns["win_popen"]((None, satir, None, None))

    def test_cwd_icindeki_sahte_arac_guvenilir_degil(self):
        gercek = shutil.which("git")
        if not gercek:
            self.skipTest("git yok")
        kok = _kok()
        self.addCleanup(sil, kok)
        shutil.copyfile(sys.executable, kok / "git.exe")
        ns = koruma_eki_ad_alani(kok)
        eski = os.getcwd()
        os.chdir(kok)
        self.addCleanup(os.chdir, eski)
        calisan = ns["win_calisan"](None, "git")  # CreateProcess önce cwd'ye bakar
        self.assertEqual(os.path.normcase(calisan), os.path.normcase(str(kok / "git.exe")))
        self.assertFalse(ns["win_gercek_arac"](calisan))
        self.assertTrue(ns["win_gercek_arac"](gercek))


@unittest.skipUnless(uyum.WINDOWS, "yalnız Windows")
class YoklamaBetigiTesti(unittest.TestCase):
    """Yoklama betiği yalıtımsız ortamda (sandbox dışı) kanıt üretmemeli; asılı kalmamalı (mkstemp yok)."""

    def kos(self, *dizinler):
        bas = time.monotonic()
        proc = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-", str(Path.cwd()), *dizinler],
                              input=yw.YOKLAMA, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=60)
        self.assertLess(time.monotonic() - bas, 30)
        return proc

    def test_yazilabilir_dizin_yalitim_yok(self):
        proc = self.kos(tempfile.gettempdir())
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("kapı dışına yazılabilir", json.loads(proc.stdout)["neden"])

    def test_korumali_dizin_ama_tcp_engeli_yok(self):
        # C:\Windows sandbox dışında da yazılamaz; ama dış TCP izin hatasıyla reddedilmez → kanıt yok.
        proc = self.kos(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TCP", json.loads(proc.stdout)["neden"])

    def test_zorunlu_dizin_yoksa_kanit_yok(self):
        proc = self.kos(str(Path(tempfile.gettempdir()) / f"yok-{uuid.uuid4().hex}"))
        self.assertEqual(proc.returncode, 3)
        self.assertIn("yoklama dizini yok", json.loads(proc.stdout)["neden"])

    def test_yalniz_istege_bagli_dizin_kanit_degil(self):
        proc = self.kos("?" + os.environ.get("SYSTEMROOT", r"C:\Windows"))
        self.assertEqual(proc.returncode, 3)
        self.assertIn("yoklanacak dış dizin yok", json.loads(proc.stdout)["neden"])


@unittest.skipUnless(uyum.WINDOWS, "yalnız Windows")
class AgacOzetiTesti(unittest.TestCase):
    def setUp(self):
        self.yol, self.agac = kapi_kur(self, GECER, dosyalar={"a.txt": "bir\n", "alt/b.txt": "iki\n"})

    def test_icerik_ekleme_silme_degisikligi_gorulur(self):
        once = kehanet.agac_ozeti(self.agac)
        self.assertEqual(once, kehanet.agac_ozeti(self.agac))
        (self.agac / "alt" / "b.txt").write_text("üç\n", encoding="utf-8")
        ikinci = kehanet.agac_ozeti(self.agac)
        self.assertNotEqual(once, ikinci)
        (self.agac / "yeni.txt").write_bytes(b"")
        ucuncu = kehanet.agac_ozeti(self.agac)
        self.assertNotEqual(ikinci, ucuncu)
        (self.agac / "a.txt").unlink()
        self.assertNotEqual(ucuncu, kehanet.agac_ozeti(self.agac))

    def test_kavsak_bag_olarak_kaydedilir_icine_inilmez(self):
        dis = _kok()
        self.addCleanup(sil, dis)
        (dis / "dis.txt").write_text("x", encoding="utf-8")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.agac / "bag"), str(dis)],
                              capture_output=True, timeout=30)
        if proc.returncode != 0:
            self.skipTest("kavşak oluşturulamadı")
        ozet = kehanet.agac_ozeti(self.agac)
        self.assertEqual(ozet["bag"][0], "bag")
        self.assertNotIn("bag/dis.txt", ozet)
        (dis / "dis.txt").write_text("değişti", encoding="utf-8")
        ozet2 = kehanet.agac_ozeti(self.agac)
        self.assertEqual({k: v for k, v in ozet.items() if not k.startswith("git:")},
                         {k: v for k, v in ozet2.items() if not k.startswith("git:")})


@unittest.skipUnless(uyum.WINDOWS, "yalnız Windows")
class FailClosedTesti(unittest.TestCase):
    """Yalıtım kanıtlanamazsa kehanet KOŞMAZ ve 'yalıtım yok' döner; yalıtımsız geri düşüş yok."""

    def yalitim_yok_mu(self, sonuc):
        self.assertIs(sonuc["gecti"], False)
        self.assertEqual(sonuc.get("yalitim"), "yok", sonuc)
        self.assertNotIn("exit_code", sonuc)  # kehanet hiç koşmadı
        self.assertIn("yalıtımı yok", sonuc["hata"])

    def test_codex_yoksa_kosmaz_ve_bwrap_denenmez(self):
        yol, agac = kapi_kur(self, GECER)
        yok = str(Path(agac) / "yok" / "codex.exe")
        with mock.patch.dict(os.environ, {"ORVANT_CODEX": yok}), \
                mock.patch.object(kehanet, "bwrap_yalitimi", side_effect=AssertionError("bwrap denendi")):
            sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.yalitim_yok_mu(sonuc)
        self.assertIn("codex", sonuc["hata"])

    def test_sandbox_python_yoksa_kosmaz(self):
        yol, agac = kapi_kur(self, GECER)
        with mock.patch.dict(os.environ, {yw.PYTHON_ORTAMI: str(Path(agac) / "yok" / "python.exe")}), \
                mock.patch.object(yw, "yokla", side_effect=AssertionError("python yokken yoklandı")):
            sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.yalitim_yok_mu(sonuc)
        if shutil.which(ayarlar.codex_ikili(agac)):
            self.assertIn("kur", sonuc["hata"])

    def test_sandbox_okuyamayan_python_ile_kosmaz(self):
        profil = os.path.normcase(str(Path.home()))
        python = sys._base_executable if hasattr(sys, "_base_executable") else sys.executable
        if not os.path.normcase(python).startswith(profil):
            self.skipTest("bu Python kullanıcı profilinde değil; sandbox okuyabiliyor olabilir")
        if not shutil.which(ayarlar.codex_ikili()):
            self.skipTest("codex yok")
        yol, agac = kapi_kur(self, GECER)
        with mock.patch.dict(os.environ, {yw.PYTHON_ORTAMI: python}):
            sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.yalitim_yok_mu(sonuc)

    def test_on_yoklama_atlansa_da_onsoz_yoklamasi_kapatir(self):
        """Savunma derinliği: dış yoklama 'geçti' dese bile sandbox'sız koşan önsöz kendi yoklamasıyla durur."""
        yol, agac = kapi_kur(self, GECER)

        def sandboxsuz(komut, **kw):
            self.assertEqual(komut[1:3], ["sandbox", "--"])
            return grup_run(komut[3:], **kw)  # codex sandbox önekini at: yalıtımsız koşu

        with mock.patch.dict(os.environ, {"ORVANT_CODEX": sys.executable, yw.PYTHON_ORTAMI: sys.executable}), \
                mock.patch.object(yw, "yokla", return_value=(True, None)), \
                mock.patch.object(kehanet, "grup_run", side_effect=sandboxsuz):
            sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.assertIs(sonuc["gecti"], False)
        self.assertNotEqual(sonuc["exit_code"], 0)
        self.assertIn("yalıtım yok", sonuc["stderr_kuyrugu"])


@unittest.skipUnless(uyum.WINDOWS, "yalnız Windows")
class SandboxTesti(unittest.TestCase):
    """Gerçek `codex sandbox` (elevated) gerektirir; yoksa nedenle atlanır."""

    def setUp(self):
        self.python, self.codex, neden = sandbox_durumu()
        if neden:
            self.skipTest(neden)

    def test_yoklama_gecer(self):
        kok = _kok()
        self.addCleanup(sil, kok)
        env = yw.codex_ortami({"PATH": os.environ.get("PATH", "")})
        tamam, neden = yw.yokla(self.codex, self.python, kok, env, yw.dis_yollar(kok, python=self.python),
                                zaman_asimi=ZAMAN)
        self.assertTrue(tamam, neden)

    def test_dis_yazma_ve_tcp_engelli(self):
        kok = _kok()
        self.addCleanup(sil, kok)
        betik = ("import json, os, socket, sys\n"
                 "def yaz(d):\n"
                 "    try:\n"
                 "        fd = os.open(os.path.join(d, '.x-%d' % os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_EXCL)\n"
                 "    except OSError as e:\n"
                 "        return type(e).__name__\n"
                 "    os.close(fd)\n"
                 "    return 'YAZDI'\n"
                 "def tcp(h):\n"
                 "    try:\n"
                 "        socket.create_connection(h, timeout=3).close()\n"
                 "    except OSError as e:\n"
                 "        return type(e).__name__\n"
                 "    return 'BAGLANDI'\n"
                 "print(json.dumps({'kapi': yaz(sys.argv[1]), 'temp': yaz(sys.argv[2]), 'ev': yaz(sys.argv[3]),\n"
                 "                  'test_net': tcp(('192.0.2.1', 9)), 'dis': tcp(('1.1.1.1', 443))}))\n")
        env = yw.codex_ortami({"PATH": os.environ.get("PATH", "")})
        proc = grup_run([self.codex, "sandbox", "--", self.python, "-I", "-B", "-X", "utf8", "-", str(kok),
                         tempfile.gettempdir(), str(Path.home())], cwd=kok, env=env, input=betik, timeout=ZAMAN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        veri = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(veri, {"kapi": "PermissionError", "temp": "PermissionError", "ev": "PermissionError",
                                "test_net": "PermissionError", "dis": "PermissionError"})

    def test_uctan_uca_basit_kehanet_gecer(self):
        yol, agac = kapi_kur(self, BASIT, ciktilar=["arac/isci.py", "arac/buyuk.py"], dosyalar={
            "veri.txt": "çğış ÖÜ\n",
            "arac/isci.py": "import sys\nprint(sys.stdin.read().strip() + '|' + sys.argv[1])\n",
            "arac/buyuk.py": "import sys\nsys.stdout.write('x' * 3000000)\n"})
        sonuc = kehanet.calistir_kehanet(yol, agac, ["ğüşi"], zaman_asimi=ZAMAN)
        self.assertTrue(sonuc["gecti"], json.dumps(sonuc, ensure_ascii=True)[-3000:])
        self.assertEqual(sonuc["yalitim"], "codex-sandbox")
        self.assertFalse((Path(agac) / "yeni.txt").exists())

    def test_arac_guvenligi(self):
        yol, agac = kapi_kur(self, ARAC_GUVENLIGI)
        shutil.copyfile(self.python, Path(agac) / "git.exe")  # cwd'ye konmuş sahte araç
        sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.assertTrue(sonuc["gecti"], json.dumps(sonuc, ensure_ascii=True)[-3000:])
        self.assertEqual({k["ad"] for k in sonuc["kontroller"]}, {"sahte_git", "winapi", "ctypes", "kanonik_olmayan"})

    def test_isci_kapi_agacina_yazamaz(self):
        yol, agac = kapi_kur(self, AGAC_YAZAN, ciktilar=["yaz.py"], dosyalar={
            "yaz.py": "open('sizinti.txt', 'w').write('x')\n"})
        sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.assertTrue(sonuc["gecti"], json.dumps(sonuc, ensure_ascii=True)[-3000:])
        self.assertFalse((Path(agac) / "sizinti.txt").exists())

    def test_kapi_agacini_degistiren_kosu_reddedilir(self):
        yol, agac = kapi_kur(self, GECER)

        def degistiren(komut, **kw):
            sonuc = grup_run(komut, **kw)
            (Path(agac) / "sizinti.txt").write_text("x", encoding="utf-8")  # koşu sırasında ağaç değişti
            return sonuc

        with mock.patch.object(kehanet, "grup_run", side_effect=degistiren):
            sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.assertIs(sonuc["gecti"], False)
        self.assertEqual(sonuc["hata"], "kapı ağacı değişti")
        self.assertEqual(sonuc["exit_code"], 0)

    def test_isci_zaman_asiminda_agac_olur(self):
        yol, agac = kapi_kur(self, ZAMAN_ASIMI, ciktilar=["uyu.py"], dosyalar={
            "uyu.py": ("import subprocess, sys, time\n"
                       "q = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                       "print(q.pid, flush=True)\n"
                       "time.sleep(60)\n")})
        bas = time.monotonic()
        sonuc = kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=ZAMAN)
        self.assertTrue(sonuc["gecti"], json.dumps(sonuc, ensure_ascii=True)[-3000:])
        self.assertLess(time.monotonic() - bas, 45)

    def test_kehanet_zaman_asimi_sandbox_surecini_birakmaz(self):
        import threading
        yol, agac = kapi_kur(self, '"""1. Uyur."""\nimport time\ntime.sleep(60)\n')

        def say():
            # Sandbox süreci ayrı kullanıcıdadır (CodexSandboxOffline); aynı kopyayı başka işler de kullanabilir,
            # bu yüzden yalnız bu kehanetin komut satırı (benzersiz kapı yolu) aranır.
            sorgu = subprocess.run(["powershell", "-NoProfile", "-Command",
                                    "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object "
                                    f"{{ $_.CommandLine -like '*{Path(agac).parent.name}*' }}).Count"],
                                   capture_output=True, encoding="utf-8", errors="replace", timeout=60)
            return int(sorgu.stdout.strip() or -1)

        sonuclar = []
        kos = threading.Thread(target=lambda: sonuclar.append(
            kehanet.calistir_kehanet(yol, agac, [], zaman_asimi=8)))
        bas = time.monotonic()
        kos.start()
        en_cok = 0
        while kos.is_alive() and time.monotonic() - bas < 40:
            en_cok = max(en_cok, say())  # olumlu denetim: koşu sırasında süreç görünmeli
        kos.join(60)
        self.assertTrue(sonuclar and sonuclar[0].get("zaman_asimi"), sonuclar)
        self.assertLess(time.monotonic() - bas, 45)
        self.assertGreaterEqual(en_cok, 1)
        time.sleep(1)
        self.assertEqual(say(), 0)

    def test_kur_sertlestirilmis_kopya_yoklamayi_gecer(self):
        hedef = Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / f"orvant-py-test-{uuid.uuid4().hex[:8]}"
        self.addCleanup(sil, hedef)
        python = yw.kur(hedef)
        with self.assertRaises(yw.YalitimHatasi):
            yw.kur(hedef)  # var olan kopya --zorla olmadan ezilmez
        acl = subprocess.run(["icacls", str(hedef)], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout
        self.assertNotIn("(I)", acl)  # kalıtım kesildi (INTERACTIVE/SERVICE/BATCH değiştirme izni yok)
        self.assertFalse((hedef / "Lib" / "site-packages").exists())
        kok = _kok()
        self.addCleanup(sil, kok)
        env = yw.codex_ortami({"PATH": os.environ.get("PATH", "")})
        tamam, neden = yw.yokla(self.codex, str(python), kok, env, yw.dis_yollar(kok, python=python),
                                zaman_asimi=ZAMAN)
        self.assertTrue(tamam, neden)


if __name__ == "__main__":
    unittest.main()
