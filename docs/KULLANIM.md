# Kurulum, kullanım ve devam

[← Proje Başlat](../README.md) · [Sık sorulanlar](SSS.md)

Komutları aksi belirtilmedikçe klonladığın deponun kökünden çalıştır. Python 3.10+
gerekir; Windows, macOS ve Linux için ek pip paketi yoktur. Runtime macOS/Linux
üzerinde `fcntl.flock`, Windows üzerinde `msvcrt.locking` kullanır.
Güncel otomatik koşular [GitHub Actions](https://github.com/fornhere/proje-baslat-skill/actions) sayfasındadır.

## İşletim sistemine göre komutlar

macOS/Linux örnekleri `python3` kullanır. Windows PowerShell'de aynı komutların
başını `py -3` yap; bu başlatıcı yoksa `python --version` ile 3.10+ doğrula ve
`python` kullan. Örneğin deponun kökünden:

```powershell
py -3 --version
py -3 skills/proje-baslat/scripts/project.py init "../benim projem" --spec "examples/deney-defteri.json"
py -3 "../benim projem/.project/scripts/project.py" context "../benim projem"
py -3 scripts/demo.py "../proje demo"
py -3 -m unittest discover -s tests -v
```

Tam yorumlayıcı yolu gerekiyorsa PowerShell'de `& "C:/Python/python.exe" ...`
biçimini kullan. macOS/Linux komutlarında da boşluk içeren yolları tırnakla.
Spec ve eylem JSON dosyalarını UTF-8 kaydet; UTF-8 BOM kabul edilir, UTF-16 kabul
edilmez. CLI çıktısı UTF-8'dir; alt süreçten okurken `encoding="utf-8"` seç.
Kanıt/nesne yolları her sistemde proje köküne göre `/` ile yazılır.

Skill gerçek klasörü `skills/proje-baslat` içindedir. `.agents` keşif bağlantısı
Windows'ta açılmasa da gerçek SKILL.md yolu ile çalıştırılabilir; yönetici izni
veya Developer Mode normal kullanım için gerekmez. macOS'un `/tmp`, `/var`,
`/etc` sistem bağlantıları kabul edilir; proje içi symlink ve Windows junction
bağlantıları yönetilen dosyalarda ve kanıtlarda reddedilir.

Kilitler aynı kullanıcı ve yerel dosya sistemi içindeki CLI yazıcılarını sıralar.
Windows'ta kilit 30 saniyede alınamazsa işlem hata verir; mevcut yazıcı bittikten
sonra tekrar dene. Ağ paylaşımı, OneDrive/iCloud eşzamanlı yazımı ve ayrı işletim
sistemlerinden aynı projeye paralel yazım desteklenmez. Eski proje kopyalarına
bu değişikliği taşımak için aşağıdaki `upgrade` akışını kullan.

Windows/macOS/Linux ve Python 3.10/3.14 üzerinde altı CI koşusunun tamamında
testler ve demo geçti. [16 Eylül 2026 doğrulaması](https://github.com/fornhere/proje-baslat-skill/actions/runs/35109602302).

## 1. Skill'i kullanma

Ajanına `skills/proje-baslat/SKILL.md` dosyasını açıkça okut. Ajan hedef klasörü,
hedef kitleyi, ilk çıktıyı ve modelin cevaplaması gereken soruları belirler.
Türleri, gerçek kayıtları, ilişkileri ve görevleri konuşmadan hazırlar; kullanıcıya
JSON doldurtmaz. Kabul edilmemiş tercih öneri olarak kalır.

Örnek istek:

> Deney Defteri projemi kur. Komut, deney, çıktı ve değerlendirme arasındaki
> ilişkileri göster. Bir ölçüt değişince hangi incelemenin yenileneceğini modelle.

Depo içindeki `.agents/skills/proje-baslat` göreli bağlantısı keşif içindir.
Yeni istemcide otomatik keşfi olmuş sayma; açık skill yolu kullanılabilir.
[Skill sözleşmesi](../skills/proje-baslat/SKILL.md).

## 2. Hazır şema 3 örneğiyle kurulumu deneme

`../benim-yeni-projem` için yeni bir deneme hedefi seç. Örnek Forn'un Deney Defteri
modelini ve açıkça kurmaca deneyleri taşır; kendi projenin kabul edilmiş kararları
olarak kopyalama. Kayıt kurmak uygulamayı veya belge çıktılarını üretmez.

```sh
python3 skills/proje-baslat/scripts/project.py init ../benim-yeni-projem --spec examples/deney-defteri.json
python3 ../benim-yeni-projem/.project/scripts/project.py check ../benim-yeni-projem
python3 ../benim-yeni-projem/.project/scripts/project.py context ../benim-yeni-projem
python3 ../benim-yeni-projem/.project/scripts/project.py ontology ../benim-yeni-projem
```

Türler ile somut deneyleri ve görev girdilerini birlikte incele. `.project/ONTOLOJİ.md`
aynı modelin üretilen görünümüdür. Kayıtta adı geçen ama henüz üretilmemiş belge
ve uygulama çıktıları ilgili görevlerde hazırlanır; var veya kabul edilmiş sayılmaz.
Tam, daha küçük bir spec [model belgesinde](../skills/proje-baslat/references/model.md)
bulunur. `examples/ai-karsilastirma.json` eski şema örneğidir; yeni ontoloji
özelliklerini tek başına etkinleştirmez.

İlk kurulum revizyon 0 üretir. Açık görev varken `check` başarılı olabilir;
“bütün proje bitti” anlamına gelmez. Mevcut AGENTS.md korunur.
`integration: review_required` yanıtında `.project/integration.md` mevcut
yönergelerle uzlaştırılmalıdır. AGENTS.override.md başlangıç davranışını etkileyebilir.

## 3. Eylemi önce inceleme, sonra uygulama

`goreve-basla.json` dosyası bu örneğin mevcut görevi için şöyle olabilir:

```json
{
  "action": "start_task",
  "actor": "agent",
  "reason": "İlk ürün akışını güncel girdilere dayanarak hazırlamaya başlıyorum.",
  "task_id": "T-AKIS"
}
```

Önce canlı context'ten revizyonu oku. `0`, yalnız hiç değiştirilmemiş kurulum için
doğrudur. Preview'dan gelen `preview_digest` değerini apply'a aynen geçir:

```sh
python3 ../benim-yeni-projem/.project/scripts/project.py context ../benim-yeni-projem --json
python3 ../benim-yeni-projem/.project/scripts/project.py preview ../benim-yeni-projem --event goreve-basla.json --expected-revision 0
python3 ../benim-yeni-projem/.project/scripts/project.py apply ../benim-yeni-projem --event goreve-basla.json --expected-revision 0 --preview-digest "<preview_digest>"
```

Preview state'i değiştirmez. Etkilenen nesneler, anlamlı yollar, görevler ve oluşacak
bağlam okunur. Digest state/eylem ile referans evidence/file içeriklerini gözler;
revizyon aynıyken dosya değişirse bile yeniden preview gerekir. Dış editörler
kilitlenmez ve dosya sistemi için atomik snapshot garantisi verilmez.

Start, çalışma başlangıcının girdilerini kaydeder. İşi gerçekten yap; kabul
ölçütlerini kontrol et; sonra `submit_evidence` ve `complete_task` kullan. Girdi
çalışma sırasında değiştiyse eski koşunun sonucunu yeni girdiye bağlama: yeniden
başlat ve kontrolü tekrarla. Raporla birlikte kontrol edilen zorunlu kaynak/çıktı
dosyalarını evidence'a bağla. Alternatif destek kaynaklarının `file` sürümleri dal
manifest'inde hashlenir; hepsini yeniden zorunlu evidence yapmak `any` grubunu
etkisizleştirir. Hash doğruluğu, reviewer kimliği ispatlamaz.

## 4. Alan veya görev değiştiğinde

Önce context, sonra ilgili eylemi aynı preview/digest/apply döngüsüyle uygula:

| Eylem | Kullanımı |
| --- | --- |
| `extend_model` | Yeni nesne, ilişki ve görevleri birlikte ekleme |
| `mutate_graph` | Nesne/ilişki ekleme, düzeltme, silme veya tam ontoloji şemasını revize etme |
| `revise_task` | Görevin ölçüt, girdi/çıktı, konu, önkoşul ve karar bağlarını tam tanımla değiştirme |
| `migrate_ontology` | Eski şema 1/2 kaydını açık görev eşlemeleriyle şema 3'e taşıma |

V3 görevinde `object_ids` konuyu, `input_ids` dayanakları, `output_ids` üretilen
kayıtları gösterir. Girdi üreticileri etkin önkoşul olur; ek iş sırası `depends_on`
ile belirtilir. Geçersiz alan türü, ilişki ucu, çokluk veya görev döngüsü reddedilir.

Değişimden etkilenen incelemelerin eski kabulü temizlenir; kaydedilen geçmişte
önceki görev/snapshot görüntüleri bulunur. Tarihsel koşu ve provenance türlerinde
`immutable: true` kullan; korunan kaydı düzenlemek yerine yeni sürüm kimliği oluştur.
Silinmiş nesne/ilişki kimliği yeniden kullanılamaz.

Alternatif destek gerekiyorsa `support_groups` içindeki `any`/`all` sözleşmesini
kullan. Yalnız gerçekten incelenmiş dallar `reviewed_supports` ile kabul edilir.
Karşılaştırılabilirlik gibi dar veri koşulları `acceptance_rules` ile denetlenir;
ilişki adına bakarak doğruluk veya kaynak yeterliliği çıkarılmaz.

[Tam işlem örnekleri](../skills/proje-baslat/references/plan-changes.md) ·
[Ontoloji modelleme rehberi](../skills/proje-baslat/references/ontology.md).

## 5. Yeni oturumda devam

Ajanı kurulan proje kökünde aç:

> Kayıtlı durumdan devam et. Önce güncel context ve ontology çıktısını oku;
> geçerli kararları, eskiyen incelemeleri ve sıradaki yapılabilir işi açıkla.

```sh
python3 .project/scripts/project.py context .
python3 .project/scripts/project.py ontology .
```

`state.json` yetkili kayıt; Markdown dosyaları son üretilen görünümdür. Canlı
komutlar güncelliği yeniden kontrol eder. Başka sohbetler veya kişisel hafıza
kendiliğinden aranmaz.

`repair_actions` mevcut onarım seçeneklerini gösterir. Bitmiş ama eskiyen işi
`reopen_task`; eski karara bağlı uygun açık işi `reconcile_task` ile ele alabilirsin.
Gerekçeyi değerlendir, actor ekle, preview/digest/apply kullan ve context'i yeniden
oku. `ready` boşluğu tek başına projenin bittiğini göstermez.

## 6. Kontrol sonucunu yorumlama

| Gözlem | Anlamı / sonraki adım |
| --- | --- |
| `check` kodu 0 | Yapı ve denetlenen güncellik uygun; açık işler olabilir |
| `check` kodu 1 | Yapı, kanıt veya alan güncelliği sorununu incele |
| `needs_review` | Girdi/çıktı, karar, destek veya önkoşul değişmiş olabilir |
| `blocked` | Etkin önkoşul hazır değil; önce ilgili işi çöz |
| `freshness` | Önceki kabul current/stale/unverified olarak gösterilir |
| `readiness` | İş sırası açısından ready/blocked; kabul güncelliğinden ayrıdır |
| Nesne `pending` | Üretici görevi henüz güncel kabul almadı |
| `already_initialized` | Tam kurulum var; yeni spec uygulanmadı |
| Init conflict | Yarım/bozuk kurulum veya yol çakışması; otomatik sıfırlanmadı |
| Preview digest conflict | Gözlenen kayıt/eylem/dosya değişti; yeni preview al |
| `state_committed_view_failed` | Eylem state'e yazıldı; aynı eylemi tekrarlama, canlı context oku |

## 7. Runtime yükseltme ve ontoloji migrasyonu

Kaynak skill'in güncellenmesi projeye kopyalanmış runtime'ı kendiliğinden yenilemez.
Kaynak depodan:

```sh
python3 skills/proje-baslat/scripts/project.py upgrade ../benim-yeni-projem
python3 ../benim-yeni-projem/.project/scripts/project.py context ../benim-yeni-projem --json
```

`upgrade`, state'i doğrular ama şema/revizyon/byte'larını değiştirmez. `core.py`,
`project.py`, `ontology.py`, `acceptance.py` modüllerini günceller. Eski iki betikli
kurulum desteklenir. Önceki dosyalar ve hangi modülün önceden bulunmadığı
`.project/runtime-backups/upgrade-*/manifest.json` ile korunur.

| Sonuç | Anlamı |
| --- | --- |
| `updated` | Runtime güncellendi; backup eski dosyaları gösterir |
| `noop` | Kaynakla aynı; yazım veya yeni yedek yok |
| `restored` | Güncelleme başarısız; önceki runtime korundu/geri yüklendi |
| `restore_failed` | Karışık runtime olabilir; yazıcıları durdur ve manifest'e göre geri yükle |

Her dosya atomik değiştirilir; dört dosya topluca atomik değildir. Kesilme veya
restore_failed durumunda yedekte var olan modülleri geri getir, önceden bulunmayan
modülleri kaldır. State ve runtime sürümleri uyumlu olmalı.

Ontolojiye geçiş ayrı `migrate_ontology` eylemidir. Tam tür/örnek/ilişki modeli ve
mevcut her görev için açık konu/girdi/çıktı eşlemesi gerekir. Preview ile incele;
apply eski state'in tam yedeğini `.project/migration-backups/` içine koyar.
Önceki görevler/kanıtlar geçmişte korunur, aktif kabul yeni alan modeli için yeniden
incelemeye açılır. Genel hedef değişikliği veya görev iptal eylemi yoktur.
[Geçiş ve kurtarma ayrıntısı](../skills/proje-baslat/references/plan-changes.md).

## 8. Yedek ve yeniden doğrulama

Özel restore komutu yoktur. `.project` ile gerçek kanıt/çıktı dosyalarını birlikte
yedekle. Yazıcılar dururken tutarlı yedeği ayrı klasöre geri getir; orada check,
context ve ontology çalıştır. Yalnız state'i geri almak eski dosya içeriklerini
geri getirmez. CLI kilidi başka betikleri veya elle düzenlemeyi engellemez.

Kaynak depoda otomatik kontroller:

```sh
python3 -m unittest discover -s tests -v
```

Testler `tests/` altında bulunur; üç platformlu CI aynı kümeyi çalıştırır.
`scripts/demo.py` 14 adımlı sentetik görev/kanıt senaryosudur; tek başına
bütün ontoloji davranışlarının kabul testi değildir.
