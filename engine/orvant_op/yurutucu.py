"""Temiz profilli codex exec sarmalayıcısı; yürütücü testte enjekte edilir."""
import json
import subprocess
import tempfile
import time
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


class YurutucuHatasi(RuntimeError):
    pass


class YurutucuZamanAsimi(YurutucuHatasi):
    """Alt süreç süre sınırını aştı ve sonlandırıldı."""


def calistir(istem, *, model, effort, calisma, sema_yolu, sandbox="read-only",
             arama=False, zaman_asimi=1500, iz_yolu=None, yurutucu=None, proje=None, gorev=None):
    """Son mesajı JSON nesnesi olarak döndürür; her denemeyi gelişim izine yazar."""
    run = yurutucu or subprocess.run
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
                raise YurutucuHatasi(f"codex exec rc={proc.returncode}: {(proc.stderr or '')[-300:]}")
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
    run = yurutucu or subprocess.run
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
            hata = f"codex exec rc={rc}: {(proc.stderr or '')[-300:]}"
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
