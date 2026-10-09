"""Temiz profilli işçi sarmalayıcısı: codex exec (varsayılan) ya da claude -p (B-Y); yürütücü testte enjekte edilir."""
import errno
import json
import os
import signal
import shlex
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import ayarlar
from .iz import kaydet

WORKER_CLEAN_PREAMBLE = (
    "Bu otomatik bir işçi koşusudur. Kullanıcı düzeyindeki kişisel hafıza ve oturum "
    "açılış, geri çağırma ve kayıt yönergelerini bu koşuda uygulama; "
    "kişisel hafıza komutu çalıştırma veya dosyasını okuma. "
    "Çalışma alanı/depo içindeki görev bağlamını ve "
    "'Bağımlı görev çıktıları' altında listelenen depo içi dosyaları okuyabilirsin. "
    "Çalışma alanı dışındaki kullanıcı girdilerini yalnız istemde 'Okunabilir kullanıcı girdileri' "
    "altında açıkça listelenen yollardan oku; diğer dış dosyaları okuma. "
    "Okuma izni yazma veya başka bir işlem yetkisi vermez. "
    "Yalnız bu çalışma alanı, açıkça listelenen dış girdiler ve aşağıdaki iş tanımıyla çalış.\n\n"
)


_kanca = threading.local()

# Codex Cloud CLI'nin kendi çalışma dizinine bırakabildiği, kullanıcı verisi
# içerebilen araç kayıtları. Bunlar görev ürünü olarak uygulanmaz veya gönderilmez.
BULUT_YURUTUCU_YAN_URUNLERI = frozenset({"error.log"})


@contextmanager
def baslangic_kancasi(kanca):
    """Bu iş parçacığında başlatılan alt süreç grubunu `kanca(pgid)` ile bildirir (G-105)."""
    onceki = getattr(_kanca, "fn", None)
    _kanca.fn = kanca
    try:
        yield
    finally:
        _kanca.fn = onceki


def komut_argv(komut):
    """Kabuk sözdizimi gerekmiyorsa tırnaklı argümanları doğrudan geçir."""
    islec = None
    lexer = shlex.shlex(komut, posix=False, punctuation_chars="|&;<>$`()*?[]{}~")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        parcalar = list(lexer)
        for parca in parcalar:
            if parca.startswith("'"):
                continue
            ozel = "$`" if parca.startswith('"') else "|&;<>$`()*?[]{}~#"
            islec = next((c for c in parca if c in ozel), None)
            if islec:
                break
        if not islec and "\n" in komut:
            islec = "satır sonu"
        if not islec and parcalar and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parcalar[0]):
            islec = "ortam ataması"
        argv = shlex.split(komut)
        if not islec and argv and argv[0] in {
                "cd", "exit", "export", "unset", "read", "eval", "exec", ".", ":",
                "source", "set", "trap", "umask", "wait", "if", "for", "while", "case", "!",
                "command", "type", "hash", "readonly", "return", "break", "continue",
                "getopts", "times", "shift", "ulimit", "jobs", "fg", "bg", "alias", "unalias", "local",
                "time", "let", "declare", "typeset", "function", "until", "select", "coproc",
                "builtin", "pushd", "popd", "shopt", "dirs", "disown", "enable", "mapfile",
                "readarray", "caller", "compgen", "complete", "logout", "suspend", "[[", "(("}:
            islec = "kabuk yerleşiği " + argv[0]
        if not argv:
            islec = islec or "boş komut"
    except ValueError:
        # Sözdizimi hatası da önceki gibi kabuğun çıkış koduyla reddedilir.
        islec, argv = "kabuk sözdizimi", []
    if islec:
        return ["/bin/sh", "-c", komut], "kabuk gerekli: " + islec
    return argv, None


def kabuk_geri_dususu(exc, argv, komut):
    """Yalnız hedef ikilinin başlatma hatasında eski sh -c davranışını koru."""
    if exc.filename == argv[0] and exc.errno in (errno.ENOEXEC, errno.ENOENT):
        neden = "çalıştırılabilir biçim yok" if exc.errno == errno.ENOEXEC else "komut bulunamadı"
        return ["/bin/sh", "-c", komut], "kabuk geri düşüşü: " + neden
    raise exc


def grup_run(komut, *, input=None, capture_output=True, text=True, timeout=None, cwd=None, shell=False, env=None):
    """subprocess.run eşdeğeri; zaman aşımında yalnız codex'i değil süreç grubunu sonlandırır (G-102).

    Yeni oturumdaki gruba terminalin Ctrl-C'si ulaşmaz; kesintide de grup sonlandırılır (G-145)."""
    proc = subprocess.Popen(komut, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE if capture_output else None,
                            stderr=subprocess.PIPE if capture_output else None,
                            text=text, start_new_session=True, cwd=cwd, shell=shell, env=env)
    try:
        kanca = getattr(_kanca, "fn", None)
        if kanca:
            kanca(proc.pid)
        cikti, hata = proc.communicate(input, timeout=timeout)
    except BaseException:  # zaman aşımı, Ctrl-C ya da kanca hatası: grup yetim kalmasın
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            os.killpg(proc.pid, signal.SIGKILL)  # SIGTERM'i yok sayan torunlar da kalmasın
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=5)  # gruptan kaçmış torun boruyu açık tutarsa bekleme sınırlı
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(komut, proc.returncode, cikti, hata)


def surec_baslangici(pid):
    """/proc/<pid>/stat başlangıç zamanı; süreç yoksa None (PID yeniden kullanımı denetimi)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]


def grup_canli(pgid):
    """Grupta çalışabilir süreç var mı; toplanmayan zombileri canlı sayma."""
    proc = Path("/proc")
    if proc.is_dir():
        for yol in proc.iterdir():
            if not yol.name.isdigit():
                continue
            try:
                alanlar = (yol / "stat").read_text().rsplit(")", 1)[1].split()
                if int(alanlar[2]) == pgid and alanlar[0] != "Z":
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _grup_bitti(pgid, sure):
    son = time.monotonic() + sure
    while True:
        if not grup_canli(pgid):
            return True
        if time.monotonic() >= son:
            return False
        time.sleep(0.05)


def grup_durdur(pgid, baslangic, *, bekleme=5.0):
    """Kayıtlı işçi grubunu SIGTERM, gerekirse SIGKILL ile durdurur (G-105).

    Lider yaşıyor ama başlangıcı uyuşmuyorsa PID başka sürecindir; dokunmaz. Lider ölüyse
    pgid, grupta süreç kaldıkça yeniden kullanılamaz; killpg güvenlidir."""
    guncel = surec_baslangici(pgid)
    if guncel is not None and guncel != baslangic:
        return {"pgid": pgid, "durduruldu": False, "neden": "PID başka sürece ait (başlangıç uyuşmuyor)"}
    for sinyal, sure in ((signal.SIGTERM, bekleme), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sinyal)
        except ProcessLookupError:
            if sinyal == signal.SIGKILL:  # SIGTERM beklemesinin hemen ardından bitti
                return {"pgid": pgid, "durduruldu": True, "sinyal": "SIGTERM"}
            return {"pgid": pgid, "durduruldu": False, "neden": "süreç grubu zaten bitmiş"}
        except PermissionError:
            return {"pgid": pgid, "durduruldu": False, "neden": "sinyal izni yok"}
        if _grup_bitti(pgid, sure):
            return {"pgid": pgid, "durduruldu": True, "sinyal": sinyal.name}
    return {"pgid": pgid, "durduruldu": False, "neden": "SIGKILL sonrası grup hâlâ var"}


class YurutucuHatasi(RuntimeError):
    pass


class YurutucuOnKontrolHatasi(YurutucuHatasi):
    """İşçi başlamadan saptanan, kullanıcı düzeltmesi gerektiren kalıcı engel."""


class YurutucuZamanAsimi(YurutucuHatasi):
    """Alt süreç süre sınırını aştı ve sonlandırıldı."""


class YurutucuKimlikHatasi(YurutucuHatasi):
    """Model CLI oturumu yok veya süresi dolmuş; işçi kusuru değildir."""


KOTA_HATA_KALIPLARI = (
    r"\busage limit\b", r"\brate[ _-]?limit(?:ed)?\b",
    r"\brate_limit_exceeded\b", r"\binsufficient_quota\b",
    r"\bquota (?:exceeded|reached)\b",
    r"\bHTTP 429\b|\bstatus(?: code)?:? 429\b|\b429 too many requests\b",
    r"\byou'?ve hit your usage limit\b",
)


class YurutucuKotaHatasi(YurutucuHatasi):
    """Sağlayıcı kotası dolu; görev denemesi olarak değerlendirilmez."""


def kota_hatasi(metin):
    """Bütün yürütücüler için tek, sabit kota metni sınıflandırıcısı."""
    metin = str(metin or "")
    return any(re.search(kalip, metin, re.IGNORECASE) for kalip in KOTA_HATA_KALIPLARI)


def _yapisal_hata_metinleri(stdout):
    """Ajan mesajlarını değil, yalnız CLI'ın yapısal hata olaylarını döndürür."""
    mesajlar = []
    for veri in _hata_jsonlari(stdout):
        if veri.get("type") not in ("error", "turn.failed") and not veri.get("is_error"):
            continue
        hata = veri.get("error")
        mesajlar.append(veri.get("message"))
        mesajlar.append(veri.get("result"))
        if isinstance(hata, dict):
            mesajlar.extend((hata.get("message"), hata.get("code")))
        else:
            mesajlar.append(hata)
    return [str(m) for m in mesajlar if m not in (None, "")]


def _hata_jsonlari(*metinler):
    """CLI stdout/stderr içindeki tek JSON veya JSONL hata kayıtlarını döndürür."""
    kayitlar = []
    for metin in metinler:
        for satir in (metin or "").splitlines():
            try:
                veri = json.loads(satir)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(veri, dict):
                kayitlar.append(veri)
    return kayitlar


def _kimlik_hatasi(metin):
    metin = str(metin).casefold()
    return bool(re.search(r"\b(?:auth|login)\b", metin)) or any(isaret in metin for isaret in (
        "authenticate", "authentication", "oauth", "not logged in", "not logged-in",
        "login required", "please login", "unauthorized", "invalid credential", "expired session",
    ))


def _cli_hata_ayrintisi(stdout, stderr):
    """İnsan mesajını ve bilinen makine teşhis alanlarını stderr kaybolmadan birleştirir."""
    parcalar = []
    for veri in _hata_jsonlari(stdout, stderr):
        for alan in ("result", "message", "error"):
            deger = veri.get(alan)
            if deger not in (None, ""):
                parcalar.append(str(deger))
        for alan in ("terminal_reason", "api_error_status"):
            if veri.get(alan) not in (None, ""):
                parcalar.append(f"{alan}={veri[alan]}")
    if not parcalar and stderr:
        parcalar.append(stderr[-300:])
    return "; ".join(dict.fromkeys(parcalar))[-600:]


def _codex_kimlik_hatasi(stdout, stderr):
    """Ajan içeriğini değil, yalnız Codex'in kendi hata kanallarını incele."""
    if _kimlik_hatasi(stderr or ""):
        return True
    for veri in _hata_jsonlari(stdout):
        if veri.get("type") not in ("error", "turn.failed"):
            continue
        hata = veri.get("error")
        mesajlar = [veri.get("message")]
        if isinstance(hata, dict):
            mesajlar.extend((hata.get("message"), hata.get("code")))
        elif hata is not None:
            mesajlar.append(hata)
        if _kimlik_hatasi(" ".join(str(m) for m in mesajlar if m is not None)):
            return True
    return False


def _codex_rc_hatasi(proc):
    ayrinti = _cli_hata_ayrintisi(proc.stdout, proc.stderr)
    if kota_hatasi(" ".join([proc.stderr or "", *_yapisal_hata_metinleri(proc.stdout)])):
        return YurutucuKotaHatasi(f"Codex kotası doldu: {ayrinti}")
    if _codex_kimlik_hatasi(proc.stdout, proc.stderr):
        return YurutucuKimlikHatasi(
            f"Codex oturumu geçersiz: codex login ile yeniden giriş yapın; {ayrinti}")
    return YurutucuHatasi(f"codex exec rc={proc.returncode}: {ayrinti}")


def semali_komut(*, model, effort, calisma, sema_yolu, son, sandbox="read-only", arama=False):
    """Şemalı çağrının gerçek/kuru koşuda aynı yürütücü komutu."""
    if ayarlar.yurutucu_turu() == "claude":
        if sandbox != "read-only":
            raise YurutucuHatasi(f"Claude şemalı çağrısı yalnız read-only koşar (istenen: {sandbox})")
        sema = json.dumps(json.loads(Path(sema_yolu).read_text(encoding="utf-8")), ensure_ascii=False)
        araclar = "Read,Grep,Glob" + (",WebSearch,WebFetch" if arama else "")
        return _claude_komutu(effort) + ["--json-schema", sema, "--tools", araclar,
                                       "--permission-mode", "dontAsk"] + ([] if arama else CLAUDE_WEB_YASAK)
    komut = [ayarlar.codex_ikili(), "exec", "--ignore-user-config", "--json",
             "--skip-git-repo-check", "--sandbox", sandbox, "-m", model,
             "-c", f"model_reasoning_effort={effort}", "-C", str(calisma),
             "--output-schema", str(sema_yolu), "-o", str(son)]
    if arama:
        komut.insert(1, "--search")
    return komut + ["-"]


def claude_isci_komutu(effort, ek_dizinler=()):
    """S3/S5 için aynı ayar, araç ve sandbox bağımsızlığı."""
    komut = _claude_komutu(effort) + ["--tools", CLAUDE_ISCI_ARACLARI, *CLAUDE_WEB_YASAK,
                                     "--permission-mode", "acceptEdits",
                                     "--settings", json.dumps(CLAUDE_ISCI_AYARI)]
    for dizin in ek_dizinler:
        komut += ["--add-dir", str(dizin)]
    return komut


def gelisim_isci_komutu(agac, rapor):
    """S5'in tarihsel Codex komutu korunur; Claude ortak işçi yolunu kullanır."""
    if ayarlar.yurutucu_turu() == "claude":
        return claude_isci_komutu("high", (agac,))
    return [ayarlar.codex_ikili(), "exec", "--skip-git-repo-check", "-m",
            ayarlar.model("gelistir_isci"), "-c", "model_reasoning_effort=high",
            "-s", "workspace-write", "-C", str(agac), "--add-dir", str(agac),
            "--color", "never", "--json", "-o", str(rapor), "-"]


def claude_isci_son_mesaj(proc):
    """S5 raporunu ortak Claude sonuç/hata ayrıştırıcısından al."""
    veri, _ = _claude_sonucu(proc)
    return str(veri.get("result") or "")


def calistir(istem, *, model, effort, calisma, sema_yolu, sandbox="read-only",
             arama=False, zaman_asimi=1500, iz_yolu=None, yurutucu=None, proje=None, gorev=None):
    """Son mesajı JSON nesnesi olarak döndürür; her denemeyi gelişim izine yazar."""
    from orvant_op.proje import load as proje_bagi
    if proje_bagi(calisma):
        raise ValueError("project handoff uses a prepared plan and pinned command oracle; automatic model planning is disabled")
    if ayarlar.yurutucu_turu() == "claude":
        return _claude_calistir(istem, effort=effort, calisma=calisma, sema_yolu=sema_yolu, sandbox=sandbox,
                                arama=arama, zaman_asimi=zaman_asimi, iz_yolu=iz_yolu, run=yurutucu or grup_run,
                                proje=proje, gorev=gorev)
    run = yurutucu or grup_run
    baslangic = time.monotonic()
    sonuc = "hata"
    kullanim = {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}
    hata = None
    data = None
    with tempfile.TemporaryDirectory(prefix="orvant-op-") as gecici:
        son = Path(gecici) / "son.json"
        komut = [ayarlar.codex_ikili(), "exec", "--ignore-user-config", "--json",
                 "--skip-git-repo-check", "--sandbox", sandbox, "-m", model,
                 "-c", f"model_reasoning_effort={effort}", "-C", str(calisma),
                 "--output-schema", str(sema_yolu), "-o", str(son)]
        if arama:
            # --search codex'in üst düzey bayrağıdır; exec alt komutundan önce gelmeli.
            komut.insert(1, "--search")
        komut.append("-")
        try:
            proc = run(komut, input=WORKER_CLEAN_PREAMBLE + istem,
                       capture_output=True, text=True, timeout=zaman_asimi)
            for satir in (proc.stdout or "").splitlines():
                try:
                    olay = json.loads(satir)
                except json.JSONDecodeError:
                    continue
                if olay.get("type") == "turn.completed":
                    usage = olay.get("usage") or {}
                    for hedef, kaynak in (("girdi_token", "input_tokens"),
                                          ("onbellek_token", "cached_input_tokens"),
                                          ("cikti_token", "output_tokens")):
                        kullanim[hedef] += int(usage.get(kaynak, 0) or 0)
            if proc.returncode != 0:
                raise _codex_rc_hatasi(proc)
            data = json.loads(son.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise YurutucuHatasi("son mesaj JSON nesnesi değil")
            sonuc = "ok"
        except subprocess.TimeoutExpired as exc:
            hata = YurutucuZamanAsimi(f"İşçi zaman aşımı ({zaman_asimi} sn): {exc}")
        except (OSError, ValueError, subprocess.TimeoutExpired, YurutucuHatasi) as exc:
            hata = exc if isinstance(exc, YurutucuHatasi) else YurutucuHatasi(str(exc))
        finally:
            kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu", yasak_kokler=(calisma,),
                   aktor_tur="codex", kimlik=f"{model}/{effort}", sonuc=sonuc,
                   ozet="Şemalı işçi çağrısı" if not hata else str(hata),
                   maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
                   ham={"sandbox": sandbox, "arama": arama}, gorev=gorev)
    if hata:
        raise hata
    return data


def hedef_calistir(istem, *, calisma, effort="high", iz_yolu=None,
                  yurutucu=None, zaman_asimi=3600, proje=None, ag=False, ek_dizinler=(), gorev=None,
                  deneme=1):
    """Şemasız goal koşusu. Hedef kararı değil, thread ve süreç kanıtı döner."""
    tur = ayarlar.yurutucu_turu()
    if tur == "codex-cloud":
        return _codex_cloud_hedef(istem, calisma=calisma, effort=effort, iz_yolu=iz_yolu,
                                  run=yurutucu or grup_run, zaman_asimi=zaman_asimi,
                                  proje=proje, ag=ag, ek_dizinler=ek_dizinler, gorev=gorev,
                                  deneme=deneme)
    if tur == "claude":
        return _claude_hedef(istem, calisma=calisma, effort=effort, iz_yolu=iz_yolu, run=yurutucu or grup_run,
                             zaman_asimi=zaman_asimi, proje=proje, ag=ag, ek_dizinler=ek_dizinler, gorev=gorev)
    return _codex_hedef(istem, calisma=calisma, effort=effort, iz_yolu=iz_yolu,
                        yurutucu=yurutucu, zaman_asimi=zaman_asimi, proje=proje, ag=ag,
                        ek_dizinler=ek_dizinler, gorev=gorev)


def _codex_hedef(istem, *, calisma, effort="high", iz_yolu=None,
                 yurutucu=None, zaman_asimi=3600, proje=None, ag=False, ek_dizinler=(), gorev=None):
    """Yerel Codex goal yürütücüsü; bulut geri düşüşü de doğrudan bunu çağırır."""
    run = yurutucu or grup_run
    baslangic = time.monotonic()
    model = ayarlar.model("isci")
    komut = [ayarlar.codex_ikili(), "exec", "--ignore-user-config", "-c", "features.goals=true",
             "--json", "-s", "workspace-write", "-C", str(calisma),
             "-m", model, "-c", f"model_reasoning_effort={effort}", "-"]
    for dizin in ek_dizinler:
        komut[-1:-1] = ["--add-dir", str(dizin)]
    if ag:
        # workspace-write sandbox ağı varsayılan kapatır; yalnız planda ağ izni verilmiş görevde açılır.
        komut[-1:-1] = ["-c", "sandbox_workspace_write.network_access=true"]
    thread_id = None
    son_mesaj = None
    kullanim = {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}
    rc, hata, kota_doldu = None, None, False
    zaman_asimina_ugradi = False
    try:
        proc = run(komut, input=WORKER_CLEAN_PREAMBLE + istem,
                   capture_output=True, text=True, timeout=zaman_asimi)
        rc = proc.returncode
        for satir in (proc.stdout or "").splitlines():
            try:
                olay = json.loads(satir)
            except json.JSONDecodeError:
                continue
            if olay.get("type") == "thread.started":
                thread_id = olay.get("thread_id")
            elif olay.get("type") == "item.completed" and (olay.get("item") or {}).get("type") == "agent_message":
                son_mesaj = (olay["item"].get("text") or "")[-2000:]
            elif olay.get("type") == "turn.completed":
                usage = olay.get("usage") or {}
                for hedef, kaynak in (("girdi_token", "input_tokens"),
                                      ("onbellek_token", "cached_input_tokens"),
                                      ("cikti_token", "output_tokens")):
                    kullanim[hedef] += int(usage.get(kaynak, 0) or 0)
        if rc:
            hata_nesnesi = _codex_rc_hatasi(proc)
            kota_doldu = isinstance(hata_nesnesi, YurutucuKotaHatasi)
            hata = str(hata_nesnesi)
    except subprocess.TimeoutExpired as exc:
        zaman_asimina_ugradi = True
        hata = f"İşçi zaman aşımı ({zaman_asimi} sn): {exc}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        hata = str(exc)[-300:]
    kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu", yasak_kokler=(calisma,),
           aktor_tur="codex", kimlik=f"{model}/{effort}",
           sonuc="hata" if hata else "ok", ozet="Goal işçi koşusu" if not hata else hata,
           maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
           ham={"thread_id": thread_id, "sandbox": "workspace-write"}, gorev=gorev)
    return {"thread_id": thread_id, "rc": rc, "hata": hata,
            "son_mesaj": son_mesaj, "kullanim": kullanim, "zaman_asimi": zaman_asimina_ugradi,
            "kota_doldu": kota_doldu}


def _cloud_run(run, argv, *, cwd, timeout):
    proc = run(argv, capture_output=True, text=True, timeout=timeout, cwd=str(cwd))
    if proc.returncode:
        if kota_hatasi(proc.stderr or ""):
            raise YurutucuKotaHatasi(
                f"Codex Cloud kotası doldu: {(proc.stderr or proc.stdout or '')[-500:]}")
        raise YurutucuHatasi(f"{' '.join(argv[:3])} rc={proc.returncode}: {(proc.stderr or proc.stdout or '')[-500:]}")
    return proc.stdout or ""


def _bulut_dali(run, calisma, uzak, gorev, deneme, oid="HEAD"):
    """Verilen commit'i yalnız Orvant'a ayrılmış, çakışmayan bir uzak dala gönder."""
    proc = subprocess.run(["git", "ls-tree", "-r", "--name-only", oid], capture_output=True,
                          text=True, timeout=30, cwd=str(calisma))
    if proc.returncode:
        raise YurutucuHatasi(f"gönderilecek ağaç denetlenemedi: {(proc.stderr or '')[-500:]}")
    yan_urunler = sorted(yol for yol in proc.stdout.splitlines()
                         if Path(yol).name in BULUT_YURUTUCU_YAN_URUNLERI)
    if yan_urunler:
        raise YurutucuOnKontrolHatasi(
            "workspace setup failed: bulut ön kontrolü; gönderilecek ağaç yürütücü "
            "yan ürünü içeriyor: " + ", ".join(yan_urunler) +
            "; bunlar meşru proje dosyalarıysa bulut yürütücüsünü kullanmadan önce "
            "yeniden adlandırın")
    taban = re.sub(r"[^A-Za-z0-9._-]+", "-", gorev or "gorev").strip("-.") or "gorev"
    kok = f"orvant/{taban}-{deneme}"
    aday, sonek = kok, 1
    while True:
        proc = run(["git", "ls-remote", "--exit-code", "--heads", uzak, aday],
                   capture_output=True, text=True, timeout=30, cwd=str(calisma))
        if proc.returncode == 2:
            break
        if proc.returncode not in (0, 2):
            raise YurutucuHatasi(f"uzak dal denetlenemedi: {(proc.stderr or '')[-500:]}")
        sonek += 1
        aday = f"{kok}-{sonek}"
    _cloud_run(run, ["git", "push", uzak, f"{oid}:refs/heads/{aday}"], cwd=calisma, timeout=60)
    return aday


def _bulut_deneme_refi(gorev, deneme):
    taban = re.sub(r"[^A-Za-z0-9._-]+", "-", gorev or "gorev").strip("-.") or "gorev"
    return f"refs/orvant/bulut/{taban}/{deneme}"


def _bulut_agac_commit(run, calisma, gorev, deneme, *, kaydet=False):
    """Çalışma ağacını HEAD'i oynatmadan nesneleştir; bulut diff'ine köken kanıtı verir."""
    with tempfile.TemporaryDirectory(prefix="orvant-bulut-") as gecici:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(gecici) / "index")}

        def git(*args):
            proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60,
                                  cwd=str(calisma), env=env)
            if proc.returncode:
                raise YurutucuHatasi(f"git {' '.join(args)}: {(proc.stderr or proc.stdout or '')[-500:]}")
            return (proc.stdout or "").strip()

        git("read-tree", "HEAD")
        git("add", "-A")
        agac = git("write-tree")
        if not kaydet:
            return agac
        bas = git("rev-parse", "HEAD")
        oid = git("-c", "user.name=Orvant", "commit-tree", agac, "-p", bas,
                  "-m", f"orvant {gorev or 'gorev'} bulut deneme {deneme}")
        git("update-ref", _bulut_deneme_refi(gorev, deneme), oid)
        return oid


def _bulut_onceki_denemeyi_devral(run, calisma, gorev, deneme):
    """Aynen duran önceki bulut diff'inin commit'ini HEAD/index'e dokunmadan döndür."""
    if deneme <= 1:
        return False
    ref = _bulut_deneme_refi(gorev, deneme - 1)

    def git(*args):
        proc = subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=60, cwd=str(calisma))
        if proc.returncode:
            raise YurutucuHatasi(f"git {' '.join(args)}: {(proc.stderr or proc.stdout or '')[-500:]}")
        return (proc.stdout or "").strip()

    proc = subprocess.run(["git", "show-ref", "--verify", "--hash", ref], capture_output=True,
                          text=True, timeout=30, cwd=str(calisma))
    if proc.returncode:
        return False
    oid = (proc.stdout or "").strip()
    ebeveyn = git("rev-parse", f"{oid}^")
    head = git("rev-parse", "HEAD")
    kayitli_agac = git("rev-parse", f"{oid}^{{tree}}")
    if ebeveyn != head or _bulut_agac_commit(run, calisma, gorev, deneme) != kayitli_agac:
        return False
    return oid


def _bulut_diff_ayikla(diff, alt_dizin):
    """Bilinen yürütücü yan ürünü yamalarını ayır; gerçek görev dosyalarına dokunma."""
    bolumler = re.split(r"(?=^diff --git )", diff, flags=re.M)
    temiz, atlanan = [], []
    izinli_kokler = set(BULUT_YURUTUCU_YAN_URUNLERI)
    if alt_dizin:
        izinli_kokler.update(f"{alt_dizin}/{ad}" for ad in BULUT_YURUTUCU_YAN_URUNLERI)
    for bolum in bolumler:
        eslesme = re.match(r"diff --git a/(\S+) b/(\S+)$", bolum.splitlines()[0], re.M) if bolum else None
        if eslesme and eslesme.group(1) == eslesme.group(2) and eslesme.group(1) in izinli_kokler:
            atlanan.append(eslesme.group(1))
        else:
            temiz.append(bolum)
    return "".join(temiz), atlanan


def _bulut_diff_uygula(run, calisma, diff, alt_dizin):
    """Bulut depo kökü diff'ini görev ağacındaki güvenli alt dizine indir."""
    yollar = re.findall(r"^diff --git a/(\S+) b/(\S+)$", diff, re.M)
    if alt_dizin and any(not (a.startswith(alt_dizin + "/") and b.startswith(alt_dizin + "/"))
                         for a, b in yollar):
        raise YurutucuHatasi("bulut diff alt_dizin dışı yol içeriyor")
    # İçerik satırlarını yeniden yazma: git'in yol bileşeni soyma davranışı yalnız
    # diff başlıklarına uygulanır.
    derinlik = len(Path(alt_dizin).parts) if alt_dizin else 0
    return run(["git", "apply", f"-p{1 + derinlik}", "-"], input=diff, capture_output=True, text=True,
               timeout=60, cwd=str(calisma))


def _bulut_istemi(istem, calisma, ek_dizinler):
    """Yerel-only bağlamı ve işçiden gizli kehaneti bulut isteminden çıkar."""
    # Son bölüm kehanet olduğunda da sınırsız `\Z` eşleşmesi kullanma; önce yalnız
    # bu işlevin bildiği bir bölüm sınırı koyup temizliği onunla sınırla.
    sinir = "\nBulut istemi sınırı (iç):\n"
    istem += sinir
    istem = re.sub(r"Bağımsız kabul ölçütleri \(değiştiremezsin\):\n.*?(?="
                   r"Çıktı sözleşmesi|Önceki bağımsız kapı hataları|Proje bağlamı|Bulut istemi sınırı \(iç\))",
                   "", istem, flags=re.S)
    istem = istem.removesuffix(sinir)
    istem = istem.replace(str(Path(calisma).resolve()), ".")
    for yol in ek_dizinler:
        istem = istem.replace(str(Path(yol).resolve()),
                               "[yalnız yerelde kullanılabilir; bulutta okunamaz]")
    return istem


def _codex_cloud_hedef(istem, *, calisma, effort, iz_yolu, run, zaman_asimi,
                       proje, ag, ek_dizinler, gorev, deneme):
    """Push edilmiş temiz dalı Codex Cloud'a yollar, diff'i yerel görev ağacına uygular."""
    del effort, ag  # Bulut ortamı bu yerel goal seçeneklerini taşımaz.
    baslangic = time.monotonic()
    cfg = ayarlar.codex_cloud_ayarlari(calisma)
    istem = _bulut_istemi(istem, calisma, ek_dizinler)
    ikili = ayarlar.codex_ikili(calisma)
    durumlar, url, gorev_id = [], None, None
    hata = None
    diff_uygulandi = False
    diff_ozeti = {"bayt": 0, "dosya": 0}
    bulut_sayisi = 0
    bulut_sure_sn = 0.0
    bulut_baslangici = None
    atlanan_yan_urunler = []
    sonuc_biliniyor = False

    def sonucu_bilinmiyor(exc):
        komut = f"{ikili} cloud status {gorev_id}"
        gecen_bulut_suresi = bulut_sure_sn
        if bulut_baslangici is not None:
            gecen_bulut_suresi += max(0, time.monotonic() - bulut_baslangici)
        return {"thread_id": gorev_id, "rc": 1,
                "hata": (f"outcome_unknown: bulut görevi {gorev_id} sonucu alınamadı: {exc}; "
                         f"uzlaştırma: {komut}; URL: {url or '-'}"),
                "son_mesaj": None, "outcome_unknown": True,
                "bulut_gorev_url": url, "uzlastirma_komutu": komut,
                "bulut_gorev_sayisi": bulut_sayisi * cfg["attempts"],
                "bulut_sure_sn": gecen_bulut_suresi,
                "kullanim": {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0},
                "zaman_asimi": False,
                "alt_surec_zaman_asimi": isinstance(
                    exc, (subprocess.TimeoutExpired, YurutucuZamanAsimi))}
    try:
        kirli = _cloud_run(run, ["git", "status", "--porcelain", "--untracked-files=all"],
                           cwd=calisma, timeout=30).strip()
        devralinan = _bulut_onceki_denemeyi_devral(run, calisma, gorev, deneme) if kirli else None
        if kirli and not devralinan:
            raise YurutucuHatasi("commit edilmemiş değişiklikler nedeniyle kirli ağaç buluta gönderilmedi")
        bulut_dali = _bulut_dali(run, calisma, cfg["uzak"], gorev, deneme, devralinan or "HEAD")
        with tempfile.TemporaryDirectory(prefix="orvant-cloud-oturum-") as oturum:
            for bos_deneme in range(2):
                sonuc_biliniyor = False
                bulut_sayisi += 1
                bulut_baslangici = time.monotonic()
                kalan = max(.01, zaman_asimi - (time.monotonic() - baslangic))
                cikti = _cloud_run(run, [ikili, "cloud", "exec", "--env", cfg["ortam"],
                                     "--branch", bulut_dali, "--attempts", str(cfg["attempts"]),
                                   WORKER_CLEAN_PREAMBLE + istem], cwd=oturum, timeout=kalan)
                url_eslesme = re.search(r"https?://\S+", cikti)
                url = url_eslesme.group(0).rstrip(".,)") if url_eslesme else None
                kimlik = re.search(r"(?:task|id)\s*[:=]\s*([\w.-]+)", cikti, re.I)
                gorev_id = kimlik.group(1) if kimlik else (url.rstrip("/").rsplit("/", 1)[-1] if url else cikti.strip().split()[-1])
                while True:
                    kalan = zaman_asimi - (time.monotonic() - baslangic)
                    if kalan <= 0:
                        raise YurutucuZamanAsimi(f"Bulut işçisi zaman aşımı ({zaman_asimi} sn)")
                    durum = _cloud_run(run, [ikili, "cloud", "status", gorev_id],
                                    cwd=oturum, timeout=min(30, kalan)).strip()
                    etiket = re.search(r"\[([A-Z_]+)\]", durum)
                    etiket = etiket.group(1) if etiket else "BILINMIYOR"
                    durumlar.append(etiket)
                    if etiket == "READY":
                        bulut_sure_sn += max(0, time.monotonic() - bulut_baslangici)
                        bulut_baslangici = None
                        break
                    if etiket in ("ERROR", "FAILED", "CANCELLED"):
                        sonuc_biliniyor = True
                        raise YurutucuHatasi(f"bulut görevi başarısız: {durum[-300:]}")
                    if etiket not in ("PENDING", "RUNNING", "IN_PROGRESS", "QUEUED"):
                        durumlar[-1] = f"BILINMEYEN:{etiket}"
                    kalan = zaman_asimi - (time.monotonic() - baslangic)
                    time.sleep(min(cfg["yoklama_saniye"], max(0, kalan)))
                diff = _cloud_run(run, [ikili, "cloud", "diff", gorev_id], cwd=oturum, timeout=min(60, kalan))
                sonuc_biliniyor = True
                diff_ozeti = {"bayt": len(diff.encode("utf-8")),
                          "dosya": sum(1 for satir in diff.splitlines() if satir.startswith("diff --git "))}
                diff, yeni_atlananlar = _bulut_diff_ayikla(diff, cfg["alt_dizin"])
                atlanan_yan_urunler.extend(yeni_atlananlar)
                if yeni_atlananlar and not diff.strip():
                    # READY görevi değişiklik üretmiştir; değişikliğin tamamı CLI yan ürünü
                    # olduğunda aynı iş için ikinci kez ücretli bulut görevi açma.
                    hata = None
                    break
                if diff.strip():
                    uygula = _bulut_diff_uygula(run, calisma, diff, cfg["alt_dizin"])
                    if uygula.returncode:
                        raise YurutucuHatasi(f"bulut diff uygulanamadı: {(uygula.stderr or '')[-500:]}")
                    _bulut_agac_commit(run, calisma, gorev, deneme, kaydet=True)
                    diff_uygulandi = True
                    hata = None
                    break
                hata = "bulut_bos_dondu: READY görevi files_changed=0"
        if hata:
            if cfg["geri_dusus"] == "codex":
                sonuc = _codex_hedef(istem, calisma=calisma, iz_yolu=iz_yolu, yurutucu=run,
                                      zaman_asimi=max(.01, zaman_asimi-(time.monotonic()-baslangic)),
                                      proje=proje, gorev=gorev)
                sonuc["bulut_geri_dusus"] = True
                sonuc["bulut_gorev_sayisi"] = bulut_sayisi * cfg["attempts"]
                sonuc["bulut_sure_sn"] = bulut_sure_sn
                hata = None
                return sonuc
        if hata and not diff.strip():
            return {"thread_id": gorev_id, "rc": 1, "hata": hata, "son_mesaj": None,
                    "bulut_gorev_sayisi": bulut_sayisi * cfg["attempts"],
                    "bulut_sure_sn": bulut_sure_sn,
                    "kullanim": {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}, "zaman_asimi": False}
        return {"thread_id": gorev_id, "rc": 0, "hata": None, "son_mesaj": "Codex Cloud diff uygulandı",
                "goal_status": "bulut_tamamlandi",
                "bulut_gorev_sayisi": bulut_sayisi * cfg["attempts"],
                "bulut_sure_sn": bulut_sure_sn,
                "kullanim": {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}, "zaman_asimi": False}
    except subprocess.TimeoutExpired as exc:
        hata = f"Bulut işçisi zaman aşımı ({zaman_asimi} sn): {exc}"
        genel_butce_doldu = time.monotonic() - baslangic >= zaman_asimi
        if gorev_id and not sonuc_biliniyor and not genel_butce_doldu:
            return sonucu_bilinmiyor(exc)
        raise YurutucuZamanAsimi(f"Bulut işçisi zaman aşımı ({zaman_asimi} sn): {exc}") from exc
    except YurutucuZamanAsimi as exc:
        hata = str(exc)
        raise
    except Exception as exc:
        hata = str(exc)
        if gorev_id and not sonuc_biliniyor and isinstance(exc, (OSError, YurutucuHatasi)):
            hata = "outcome_unknown: " + hata
            return sonucu_bilinmiyor(exc)
        raise
    finally:
        simdi = time.monotonic()
        if bulut_baslangici is not None:
            bulut_sure_sn += max(0, simdi - bulut_baslangici)
        ham = {"gorev_url": url, "bulut_gorev_id": gorev_id, "durum_gecisleri": durumlar,
               "bulut_gorev_sayisi": bulut_sayisi * cfg["attempts"], "diff_ozeti": diff_ozeti,
               "diff_uygulandi": diff_uygulandi}
        if atlanan_yan_urunler:
            ham["yurutucu_yan_urunleri_atlandi"] = sorted(set(atlanan_yan_urunler))
        if bulut_sayisi:
            ham["bulut_sure_sn"] = bulut_sure_sn
        kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu", yasak_kokler=(calisma,),
               aktor_tur="codex", kimlik=f"cloud/{cfg['ortam']}", sonuc="hata" if hata else "ok",
               ozet=hata or "Codex Cloud işçi koşusu",
               maliyet={"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0,
                        "saniye": max(0, simdi-baslangic)},
               ham=ham, gorev=gorev)


# --- Claude Code yürütücüsü (B-Y / G-172) ---------------------------------------------------------

CLAUDE_EFORLARI = {"low", "medium", "high", "xhigh", "max"}
# Codex workspace-write eşdeğeri: Claude Code OS sandbox'ı açık, sandbox dışı komut yok, ağ izni verilmez.
CLAUDE_ISCI_AYARI = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                                 "allowUnsandboxedCommands": False}}
# G-174: goal işçisinin araçları açıkça adlandırılır (--restricted Bash'i ancak adlandırılınca açar); Web aracı yok.
CLAUDE_ISCI_ARACLARI = "Bash,Read,Edit,Write,Glob,Grep"
CLAUDE_WEB_YASAK = ["--disallowedTools", "WebFetch,WebSearch"]


def _claude_komutu(effort):
    # Ayrı oturum (kalıcı değil), MCP yüklenmez: işçi bağımsızlığı (B-Y 4). --restricted kullanıcı/proje/yerel
    # ayar dosyalarını yok sayar (G-174: ağaçtaki .claude/settings*.json kanca/izin ekleyemez); --settings geçerli kalır.
    komut = [ayarlar.claude_ikili(), "-p", "--output-format", "json", "--no-session-persistence",
             "--restricted", "--strict-mcp-config"]
    if ayarlar.claude_model():
        komut += ["--model", ayarlar.claude_model()]
    if effort in CLAUDE_EFORLARI:
        komut += ["--effort", effort]
    return komut


def _claude_sonucu(proc):
    """(veri, kullanım); rc≠0, bozuk JSON veya is_error → YurutucuHatasi."""
    try:
        veri = json.loads((proc.stdout or "").strip() or "null")
    except json.JSONDecodeError as exc:
        raise YurutucuHatasi(f"claude -p çıktısı JSON değil: {(proc.stdout or '')[-300:]}") from exc
    usage = (veri.get("usage") if isinstance(veri, dict) else None) or {}
    kullanim = {"girdi_token": int(usage.get("input_tokens", 0) or 0),
                "onbellek_token": int(usage.get("cache_read_input_tokens", 0) or 0),
                "cikti_token": int(usage.get("output_tokens", 0) or 0)}
    if proc.returncode != 0:
        ayrinti = _cli_hata_ayrintisi(proc.stdout, proc.stderr)
        if kota_hatasi(" ".join([proc.stderr or "", *_yapisal_hata_metinleri(proc.stdout)])):
            raise YurutucuKotaHatasi(f"Claude Code kotası doldu: {ayrinti}")
        if _kimlik_hatasi(" ".join((proc.stdout or "", proc.stderr or "", ayrinti))):
            raise YurutucuKimlikHatasi(
                f"Claude Code oturumu geçersiz: claude ile yeniden giriş yapın; {ayrinti}")
        raise YurutucuHatasi(f"claude -p rc={proc.returncode}: {ayrinti}")
    if not isinstance(veri, dict) or veri.get("is_error"):
        raise YurutucuHatasi(f"claude -p hata: {str(veri.get('result') if isinstance(veri, dict) else veri)[-300:]}")
    return veri, kullanim


def _claude_calistir(istem, *, effort, calisma, sema_yolu, sandbox, arama, zaman_asimi, iz_yolu, run,
                     proje, gorev):
    baslangic = time.monotonic()
    sonuc, hata, data = "hata", None, None
    kullanim = {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}
    try:
        if sandbox != "read-only":
            raise YurutucuHatasi(f"Claude şemalı çağrısı yalnız read-only koşar (istenen: {sandbox})")
        sema = json.dumps(json.loads(Path(sema_yolu).read_text(encoding="utf-8")), ensure_ascii=False)
        araclar = "Read,Grep,Glob" + (",WebSearch,WebFetch" if arama else "")
        komut = _claude_komutu(effort) + ["--json-schema", sema, "--tools", araclar,
                                          "--permission-mode", "dontAsk"] + ([] if arama else CLAUDE_WEB_YASAK)
        proc = run(komut, input=WORKER_CLEAN_PREAMBLE + istem, capture_output=True, text=True,
                   timeout=zaman_asimi, cwd=str(calisma))
        veri, kullanim = _claude_sonucu(proc)
        data = veri.get("structured_output")
        if data is None:
            data = json.loads(veri.get("result") or "null")
        if not isinstance(data, dict):
            raise YurutucuHatasi("son mesaj JSON nesnesi değil")
        sonuc = "ok"
    except subprocess.TimeoutExpired as exc:
        hata = YurutucuZamanAsimi(f"İşçi zaman aşımı ({zaman_asimi} sn): {exc}")
    except (OSError, ValueError, YurutucuHatasi) as exc:
        hata = exc if isinstance(exc, YurutucuHatasi) else YurutucuHatasi(str(exc))
    finally:
        kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu", yasak_kokler=(calisma,),
               aktor_tur="claude", kimlik=f"{ayarlar.claude_model() or 'varsayilan'}/{effort}", sonuc=sonuc,
               ozet="Şemalı işçi çağrısı" if not hata else str(hata),
               maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
               ham={"sandbox": sandbox, "arama": arama, "yurutucu": "claude"}, gorev=gorev)
    if hata:
        raise hata
    return data


def _claude_proje_goal(veri):
    """Claude JSON ölçümü; cache okuma/oluşturma girdileri de bütçeye dahildir."""
    usage = veri.get("usage")
    tokens = None
    if isinstance(usage, dict):
        values = [usage.get("input_tokens"), usage.get("output_tokens"),
                  usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0)]
        if all(type(value) is int and value >= 0 for value in values):
            tokens = sum(values)
    session = veri.get("session_id")
    complete = (isinstance(session, str) and bool(session.strip())
                and veri.get("is_error") is False and veri.get("subtype") == "success")
    return {"status": "complete" if complete else "incomplete", "tokens_used": tokens,
            "session_id": session, "source": "claude_json_usage"}


def _claude_hedef(istem, *, calisma, effort, iz_yolu, run, zaman_asimi, proje, ag, ek_dizinler, gorev):
    baslangic = time.monotonic()
    thread_id = son_mesaj = rc = hata = None
    kullanim = {"girdi_token": 0, "onbellek_token": 0, "cikti_token": 0}
    zaman_asimina_ugradi = False
    project_goal = None
    if ag:
        # Ağ izni Claude sandbox'ında alan adı listesi ister; bu dilimde desteklenmez, sessiz gevşetme yok.
        hata = "Claude yürütücüsünde ağ izinli görev henüz desteklenmiyor (B-Y); Codex yürütücüsü kullanın"
    else:
        komut = _claude_komutu(effort) + ["--tools", CLAUDE_ISCI_ARACLARI, *CLAUDE_WEB_YASAK,
                                          "--permission-mode", "acceptEdits",
                                          "--settings", json.dumps(CLAUDE_ISCI_AYARI)]
        for dizin in ek_dizinler:
            komut += ["--add-dir", str(dizin)]
        try:
            proc = run(komut, input=WORKER_CLEAN_PREAMBLE + istem, capture_output=True, text=True,
                       timeout=zaman_asimi, cwd=str(calisma))
            rc = proc.returncode
            veri, kullanim = _claude_sonucu(proc)
            project_goal = _claude_proje_goal(veri)
            thread_id = veri.get("session_id")
            son_mesaj = str(veri.get("result") or "")[-2000:]
        except subprocess.TimeoutExpired as exc:
            zaman_asimina_ugradi = True
            hata = f"İşçi zaman aşımı ({zaman_asimi} sn): {exc}"
        except (OSError, YurutucuHatasi) as exc:
            hata = str(exc)[-300:]
    kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu", yasak_kokler=(calisma,),
           aktor_tur="claude", kimlik=f"{ayarlar.claude_model() or 'varsayilan'}/{effort}",
           sonuc="hata" if hata else "ok", ozet="Goal işçi koşusu" if not hata else hata,
           maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
           ham={"thread_id": thread_id, "sandbox": "claude-sandbox", "yurutucu": "claude"}, gorev=gorev)
    from .proje import _EXECUTING
    # Proje kapısı Claude ölçümünü kendi sonrasındaki kancada devralır;
    # Codex goals DB bu yürütücü için okunmaz. Gerçek oturum kimliği korunur.
    return {"thread_id": None if _EXECUTING.get() else thread_id, "rc": rc, "hata": hata,
            "session_id": thread_id, "project_goal": project_goal,
            "son_mesaj": son_mesaj, "kullanim": kullanim, "zaman_asimi": zaman_asimina_ugradi}


def kor_teshis_komutu(*, model, effort, calisma, sema_yolu, son):
    """Kör Claude koşusunda tüm araç olayları ve yalnız kopya içi okuma."""
    komut = semali_komut(model=model, effort=effort, calisma=calisma,
                         sema_yolu=sema_yolu, son=son)
    if ayarlar.yurutucu_turu() != "claude":
        return komut
    kok = Path(calisma).resolve()
    # Read/Edit gitignore desenleri kullanır (Claude permissions belgesi).
    # Her bileşenin kısa/farklı/uzun kardeş adlarını kapsa: dış dosyanın var
    # olmasına bağlı değildir, kalabalık /tmp de argv boyutunu büyütmez.
    def kacir(ad):
        return "".join("\\" + c if c in "\\*?[]!" else c for c in ad)
    dis_yollar = []
    ust = "//"
    for ad in kok.parts[1:]:
        for i, harf in enumerate(ad):
            onek = kacir(ad[:i])
            if i:
                dis_yollar.extend([ust + onek, ust + onek + "/**"])
            harf = "\\" + harf if harf in "\\[]!^-" else harf
            farkli = ust + onek + "[!" + harf + "]*"
            dis_yollar.extend([farkli, farkli + "/**"])
        uzun = ust + kacir(ad) + "?*"
        dis_yollar.extend([uzun, uzun + "/**"])
        ust += kacir(ad) + "/"
    # Read yolu Grep/Glob'a da, Edit yolu yazma araçlarına da uygulanır;
    # Glob/Write için etkisiz path kuralları üretme. Yazma bütünüyle kapalıdır.
    ayar = {"permissions": {
        "blockReadsOutsideWorkingDirectories": True,
        "allow": [f"{arac}(//{'/'.join(kacir(ad) for ad in kok.parts[1:])}/**)" for arac in ("Read",)],
        "deny": ["Edit", "Write", "Bash"] + [
            f"{arac}({yol})" for arac in ("Read", "Edit") for yol in dis_yollar]}}
    komut[komut.index("--output-format") + 1] = "stream-json"
    return komut + ["--verbose", "--settings", json.dumps(ayar)]


def claude_akis_sonucu(proc):
    """Akışın son result olayını ortak JSON ayrıştırıcısına hazırla."""
    try:
        olaylar = [json.loads(s) for s in (proc.stdout or "").splitlines() if s.strip()]
    except ValueError as exc:
        raise YurutucuHatasi("Claude akışı ayrıştırılamadı") from exc
    if any(not isinstance(o, dict) for o in olaylar):
        raise YurutucuHatasi("Claude akış olayı nesne değil")
    sonuclar = [o for o in olaylar if o.get("type") == "result"]
    if not sonuclar:
        raise YurutucuHatasi("Claude akışında result yok")
    return subprocess.CompletedProcess(proc.args, proc.returncode, json.dumps(sonuclar[-1]), proc.stderr)
