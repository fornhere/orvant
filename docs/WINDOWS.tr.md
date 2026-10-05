# Windows'ta Orvant

[Orvant](../README.tr.md) · [English version](WINDOWS.md)

Bu rehber Orvant motorunun Windows'ta çalıştırılmasını anlatır. Proje kayıt runtime'ı zaten Python 3.10+ ile Windows'ta çalışır; bu sayfa motor (`orvant` CLI) ve kabul kehaneti yalıtımı içindir.

## Doğrulananlar

Aşağıdakiler **Windows 11 Home, Python 3.12, Git 2.55, codex-cli 0.158.0** üzerinde, `PYTHONUTF8` ayarlanmadan (varsayılan cp1254 konsol kodlaması) koşturulup geçti:

- `python -m unittest discover -s engine_tests` — 103 test, 2'si POSIX'e özgü atlandı, OK.
- `python -m orvant_op --help` ve `karsila`, `mimar`, `yurut`, `operator`, `surdur` alt komutları çalışıyor ve UTF-8 çıktı üretiyor.
- Modelsiz akış: `orvant karsila baslat ...` ve `sorular` çalışıyor.
- Skill proje kaydı demosu (`skills/orvant/scripts/project.py init ...`, `context`) çalışıyor.
- Kehanet yalıtımı, Windows kısıtlı-token **elevated** modda `codex sandbox` ile çalışıyor: 22 gerçek-sandbox testi geçiyor, dış yazma ve dış TCP engelli, kapı fail-closed.

Başka Windows sürümleri, başka Python sürümleri, başka Codex CLI sürümleri ve unelevated sandbox modu **doğrulanmadı**.

## Gereksinimler

- Git for Windows (POSIX sözdizimli kabul komutları için; aşağıya bak).
- Python 3.11 veya daha yeni.
- Kurulu ve oturum açılmış Codex CLI 0.158.0, **elevated** Windows sandbox etkin. Unelevated mod Orvant tarafından kabul edilmez çünkü ağ engeli kanıtlanamaz.
- En az bir kez yönetici erişimi, Codex'in elevated sandbox'ı kurabilmesi için.

## Kurulum

Depo kökünde PowerShell aç.

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install .
```

Sandbox kullanıcısı (`CodexSandboxOffline`) kullanıcı profilinizdeki Python'u **çalıştıramaz**, bu yüzden herhangi bir kehanet komutundan önce ayrı, herkesçe okunabilir bir Python kopyası oluşturulmalıdır:

```powershell
py -3 -m orvant_op.mimar.yalitim_windows kur
```

Bu komut Python çalışma zamanının yaklaşık 32 MB'ını `%PUBLIC%\orvant-py` konumuna (veya `--hedef` ile verdiğiniz yere) kopyalar, büyük/gereksiz kütüphaneleri çıkarır ve ACL'yi sertleştirir: yalnızca Yöneticiler, SYSTEM, siz ve Users (oku+çalıştır) erişebilir. Sessiz kurulum yapmaz; komutun açıkça çalıştırılması gerekir.

Mevcut bir kopyayı `ORVANT_KEHANET_PYTHON` ortam değişkeniyle de gösterebilirsiniz:

```powershell
$env:ORVANT_KEHANET_PYTHON = "C:\Path\To\orvant-py\python.exe"
```

Sandbox'ın kopyayı görebildiğini ve çalıştırabildiğini kontrol edin:

```powershell
py -3 -m orvant_op.mimar.yalitim_windows durum
```

`yalitim` değeri `"codex-sandbox"` değilse motor kehaneti çalıştırmayı reddeder. Yalıtım yoksa kehanet koşmaz.

## Komutları çalıştırma

Paket `orvant`'ı PATH'e yerleştirdiyse Linux'taki gibi kullanın:

```powershell
orvant karsila baslat "C:\OrvantOturumlari\demo" --hedef "JSON export komutu ekle"
```

`orvant` PATH'te yoksa modül biçimini kullanın:

```powershell
py -3 -m orvant_op karsila baslat "C:\OrvantOturumlari\demo" --hedef "JSON export komutu ekle"
```

İşçiyi ortam değişkeniyle seçin:

```powershell
$env:ORVANT_YURUTUCU = "codex"
```

## Windows'ta yalıtım nasıl çalışır

Linux kehanet yalıtımı `bwrap` kullanır. Windows'ta `bwrap` olmadığı için Orvant, Codex CLI'nin `codex sandbox` kısıtlı-token sandbox'ını elevated modda kullanır. Kehanet koşmadan önce Orvant, sandbox içinde küçük bir yoklama başlatır ve şunların kanıtlanmasını ister:

- Kapı ağacı dışına yazılamıyor (kullanıcı profili, TEMP, `%SystemRoot%`, sandbox Python dizini yoklanır).
- Güvenlik duvarıyla dış TCP engelleniyor (`192.0.2.1:9` hedef olarak kullanılır).

Yalnızca `PermissionError` kanıt sayılır. Başka herhangi bir sonuç "kanıt yok" olarak değerlendirilir ve kapı kapalı kalır. Elevated mod şarttır çünkü yalnızca o modda giden TCP'yi kesen güvenlik duvarı kuralı kurulur; unelevated mod ağ yalıtımını kanıtlayamadığı için Orvant reddeder.

Süreç temizliği Windows Job Object ile yapılır: zaman aşımında veya iptalde torunlar dahil bütün süreç ağacı sonlandırılır.

## Doğrulanan motor davranışları

- Dosya kilidi (`LockFileEx`), atomik yazma (yeniden denemeli `os.replace`), junction/symlink reddi, UTF-8 dosya G/Ç ve LF satır sonu (`.gitattributes eol=lf` aracılığıyla) Windows'ta çalışıyor.
- POSIX sözdizimli kabul komutları (tek tırnak, `$`, backtick) Git for Windows'taki Git Bash üzerinden çalıştırılır. Git Bash yoksa Orvant `cmd.exe` yedeğine düşer ve yalnızca POSIX komutları için açık hata verir.
- Türkçe yollar, Türkçe girdi dosyaları ve `ORVANT_GIRDILER` ortam değişkeni UTF-8 ile çalışır.

## Bilinen sınırlar ve doğrulanmayanlar

- **DNS sızıntısı (yalnızca Windows güvenlik sınırı):** sandbox içinde ad çözümleme, sistemin DNS istemci hizmeti üzerinden hâlâ dış sunuculara ulaşabilir. Kehanet betiğinin kendisi ağı kullanamaz, ama `isci_calistir` ile koşan işçi programı DNS kanalıyla veri sızdırabilir. Linux'ta bu kanal yok. Güvenilmeyen işçiler çalıştırıyorsanız sır içermeyen bir makine/kullanıcı hesabı kullanın veya Linux tercih edin.
- **Sandbox okuma kapsamı:** sandbox tüm diski yaklaşık Linux `bwrap --ro-bind / /` düzeyinde okuyabilir. Kullanıcı profilinizdeki dosyalar sandbox içinden okunabilir.
- **Unelevated Codex sandbox** doğrulanmadı ve Orvant tarafından reddedilmesi beklenir çünkü giden TCP'nin engellendiği kanıtlanamaz.
- **Linux ve macOS** kod yolları port sırasında korundu ama bu platformlarda yeniden koşturulmadı.
- **Uzun yollar:** Windows'ta varsayılan `MAX_PATH` 260 karakterdir. Her görev ayrı worktree kullandığı için yollar çabuk uzar. `git config --global core.longpaths true` ve gerekirse Windows uzun yol desteği kayıt defteri ayarını etkinleştirin.
- **Sembolik bağ/junction:** Windows'ta sembolik bağ oluşturmak Geliştirici Modu veya yönetici hakları ister. Buna ihtiyaç duyan testler atlanır.
- **dir_fd ve O_NOFOLLOW** Windows'ta yoktur. `teshis/gecici_artik` ve `yurutme/kat_kapi` için eşzamanlı yerel saldırgan modelinde POSIX'e göre daha zayıf bir yarış penceresi vardır.
- **Yerel Windows'ta Claude Code işçisi** doğrulanmadı. Orvant kendi kehanet yalıtımını sağlar, ama Claude Code işçi süreç yönetimi kodu Windows'ta koşturulmadı.
- **Python 3.11** CI'da var ama Windows'ta yerel olarak doğrulanmadı.

## Sorun giderme

| Belirti | Olası neden | Çözüm |
|---|---|---|
| `CreateProcessAsUserW failed: 5` | Sandbox kullanıcısı profil Python'unu okuyamaz. | `py -3 -m orvant_op.mimar.yalitim_windows kur` çalıştır. |
| `durum` çıktısında `yalitim: yok` | Codex sandbox elevated değil veya güvenlik duvarı kuralı eksik. | Codex CLI kurulumunu yönetici olarak çalıştır; elevated modu doğrula. |
| POSIX kabul komutu hatası | Git Bash kurulu değil. | Git for Windows kur. |
| Yol-çok-uzun hatası | `MAX_PATH` aşıldı. | `git config --global core.longpaths true`; Windows uzun yol desteğini etkinleştir. |

## Beta kapsamı

Windows'ta da aynı beta sınırları geçerlidir: küçük Python CLI'ları, veri otomasyonu veya dar depo bakım işleriyle başlayın. Geniş kapsamlı otonom proje tamamlama ve kullanıcı emeğinin azaldığı henüz doğrulanmadı.
