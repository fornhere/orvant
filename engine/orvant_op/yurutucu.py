"""Temiz profilli işçi sarmalayıcısı: codex exec (varsayılan) ya da claude -p (B-Y); yürütücü testte enjekte edilir."""
import errno
import json
import shlex
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import ayarlar, uyum_komut, uyum_surec
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
        tek_tirnak = False
        for parca in parcalar:
            if uyum_surec.WINDOWS and parca.startswith("'"):
                tek_tirnak = True  # cmd.exe tek tırnağı yorumlamaz; Git Bash gerekir
            if parca.startswith("'"):
                continue
            ozel = "$`" if parca.startswith('"') else "|&;<>$`()*?[]{}~#"
            islec = next((c for c in parca if c in ozel), None)
            if islec:
                break
        if not islec and tek_tirnak:
            islec = "tek tırnak"
        if not islec and "\n" in komut:
            islec = "satır sonu"
        if not islec and parcalar and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parcalar[0]):
            islec = "ortam ataması"
        # Windows'ta ters eğik çizgi yol ayırıcıdır; POSIX shlex'i C:\yol'u bozar.
        argv = uyum_komut.bol(komut)
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
        return uyum_surec.kabuk_argv(komut), "kabuk gerekli: " + islec + _kabuk_notu()
    return argv, None


def _kabuk_notu():
    """Windows'ta hangi kabuğun seçildiği iz gerekçesine yazılır; POSIX'te boş (metin değişmez)."""
    return f" (kabuk: {uyum_surec.kabuk_adi()})" if uyum_surec.WINDOWS else ""


def kabuk_geri_dususu(exc, argv, komut):
    """Yalnız hedef ikilinin başlatma hatasında eski sh -c davranışını koru."""
    if exc.filename == argv[0] and exc.errno in (errno.ENOEXEC, errno.ENOENT):
        neden = "çalıştırılabilir biçim yok" if exc.errno == errno.ENOEXEC else "komut bulunamadı"
        return uyum_surec.kabuk_argv(komut), "kabuk geri düşüşü: " + neden + _kabuk_notu()
    raise exc


def grup_run(komut, *, input=None, capture_output=True, text=True, timeout=None, cwd=None, shell=False, env=None):
    """subprocess.run eşdeğeri; zaman aşımında yalnız codex'i değil süreç grubunu sonlandırır (G-102).

    Yeni oturumdaki gruba terminalin Ctrl-C'si ulaşmaz; kesintide de grup sonlandırılır (G-145).
    Windows'ta grup bir Job Object'tir (torunlar dahil tüm ağaç ölür, bkz. uyum_surec)."""
    # POSIX'te subprocess'un eski varsayılan kodlaması aynen kalsın.
    kw = {"encoding": "utf-8", "errors": "replace"} if uyum_surec.WINDOWS and text else {}
    try:
        proc = uyum_surec.grup_baslat(komut if shell else uyum_surec.cozumle_argv(komut),
                                      stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE if capture_output else None,
                                      stderr=subprocess.PIPE if capture_output else None,
                                      text=text, cwd=cwd, shell=shell, env=uyum_surec.ortam(env), **kw)
    except OSError as exc:
        # Windows başlatma hataları filename taşımaz; çağıranlar `exc.filename == argv[0]` ile ayırt eder.
        if uyum_surec.WINDOWS and exc.filename is None and not shell and isinstance(komut, (list, tuple)) and komut:
            exc.filename = str(komut[0])
        raise
    try:
        try:
            kanca = getattr(_kanca, "fn", None)
            if kanca:
                kanca(proc.pid)
            cikti, hata = proc.communicate(input, timeout=timeout)
        except BaseException:  # zaman aşımı, Ctrl-C ya da kanca hatası: grup yetim kalmasın
            uyum_surec.grup_oldur(proc)
            try:
                proc.communicate(timeout=5)  # gruptan kaçmış torun boruyu açık tutarsa bekleme sınırlı
            except subprocess.TimeoutExpired:
                pass
            raise
    finally:
        uyum_surec.grup_birak(proc)  # Windows: job tutamağı kapanır, artakalan torun kalmaz
    return subprocess.CompletedProcess(komut, proc.returncode, cikti, hata)


def surec_baslangici(pid):
    """/proc/<pid>/stat başlangıç zamanı (Windows: GetProcessTimes); süreç yoksa None (PID yeniden kullanımı denetimi)."""
    return uyum_surec.surec_baslangici(pid)


def grup_canli(pgid, baslangic=None):
    """Grupta çalışabilir süreç var mı; toplanmayan zombileri canlı sayma."""
    return uyum_surec.grup_canli(pgid, baslangic)


def _grup_bitti(pgid, sure, baslangic=None):
    return uyum_surec._grup_bitti(pgid, sure, baslangic)


def grup_durdur(pgid, baslangic, *, bekleme=5.0):
    """Kayıtlı işçi grubunu SIGTERM, gerekirse SIGKILL ile durdurur (G-105); Windows'ta job sonlandırılır.

    Lider yaşıyor ama başlangıcı uyuşmuyorsa PID başka sürecindir; dokunmaz. Lider ölüyse
    pgid, grupta süreç kaldıkça yeniden kullanılamaz; killpg güvenlidir."""
    guncel = surec_baslangici(pgid)
    if guncel is not None and guncel != baslangic:
        return {"pgid": pgid, "durduruldu": False, "neden": "PID başka sürece ait (başlangıç uyuşmuyor)"}
    return uyum_surec.grup_durdur(pgid, bekleme, baslangic)


class YurutucuHatasi(RuntimeError):
    pass


class YurutucuZamanAsimi(YurutucuHatasi):
    """Alt süreç süre sınırını aştı ve sonlandırıldı."""


class YurutucuKimlikHatasi(YurutucuHatasi):
    """Model CLI oturumu yok veya süresi dolmuş; işçi kusuru değildir."""


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
            kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu",
                   aktor_tur="codex", kimlik=f"{model}/{effort}", sonuc=sonuc,
                   ozet="Şemalı işçi çağrısı" if not hata else str(hata),
                   maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
                   ham={"sandbox": sandbox, "arama": arama}, gorev=gorev)
    if hata:
        raise hata
    return data


def hedef_calistir(istem, *, calisma, effort="high", iz_yolu=None,
                  yurutucu=None, zaman_asimi=3600, proje=None, ag=False, ek_dizinler=(), gorev=None):
    """Şemasız goal koşusu. Hedef kararı değil, thread ve süreç kanıtı döner."""
    if ayarlar.yurutucu_turu() == "claude":
        return _claude_hedef(istem, calisma=calisma, effort=effort, iz_yolu=iz_yolu, run=yurutucu or grup_run,
                             zaman_asimi=zaman_asimi, proje=proje, ag=ag, ek_dizinler=ek_dizinler, gorev=gorev)
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
    rc, hata = None, None
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
            hata = str(_codex_rc_hatasi(proc))
    except subprocess.TimeoutExpired as exc:
        zaman_asimina_ugradi = True
        hata = f"İşçi zaman aşımı ({zaman_asimi} sn): {exc}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        hata = str(exc)[-300:]
    kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu",
           aktor_tur="codex", kimlik=f"{model}/{effort}",
           sonuc="hata" if hata else "ok", ozet="Goal işçi koşusu" if not hata else hata,
           maliyet={**kullanim, "saniye": max(0, time.monotonic()-baslangic)},
           ham={"thread_id": thread_id, "sandbox": "workspace-write"}, gorev=gorev)
    return {"thread_id": thread_id, "rc": rc, "hata": hata,
            "son_mesaj": son_mesaj, "kullanim": kullanim, "zaman_asimi": zaman_asimina_ugradi}


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
        kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu",
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
    kaydet(iz_yolu, proje or Path(calisma).name, "isci_kosusu",
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
