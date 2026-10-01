# Kurulum, kullanım ve devam

[Orvant](../README.tr.md) · [Motor](MOTOR.md) · [Sık sorulanlar](SSS.md)

Orvant iki ayrı yol sunar: yerel proje kaydı için skill ve yazılım yürütmek için motor. Ajana [SKILL.md](../skills/orvant/SKILL.md) dosyasını açıkça okut; hedefini normal dille anlat. Ajanın dosya okuma, düzenleme ve komut çalıştırma erişimi gerekir.

## 1. Motoru kur

Linux'ta Python **3.11+** ve Git ile deponun kökünden:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
orvant --help
```

Gerçek koşu için Codex CLI kimlik doğrulaması, goal desteği ve `codex sandbox` gerekir. Motor `codex-cli 0.155.1` temel alınarak geliştirilmiştir; başka sürümler ve macOS/Windows motor davranışı doğrulanmış sayılmaz. Skill dosyaları wheel'den ayrı dağıtılır.

## 2. Yeni motor oturumu

`<motor-oturumu>` ayrı çalışma dizini, `<urun-deposu>` gerçek Git depo köküdür. Birbirlerini içermesinler; Orvant kaynak ağacının dışında olsunlar. Yer tutucuları gerçek yollarla değiştir.

```sh
orvant karsila baslat "<motor-oturumu>" --hedef "<hedef>"
orvant karsila ilerle "<motor-oturumu>"
orvant karsila durum "<motor-oturumu>"
orvant karsila sorular "<motor-oturumu>"
```

Gerçek kaynaklar için `baslat` komutuna `--kaynak "<kaynak-yolu>"` eklenebilir. `ilerle` tek otomatik adım yürütür ve model çağırabilir; çıktıyı okuyup gerekli otomatik adımlarda tekrar çağır. Soruları gerçek kullanıcıya taşı ve cevapları gerçek soru kimliğiyle kaydet. Sözleşmeyi `orvant karsila sozlesme "<motor-oturumu>"` ile incele.

Yalnız gösterilen sözleşme revizyonu için gerçek kullanıcı onayı olduğunda:

```sh
orvant karsila onayla "<motor-oturumu>" "<revizyon>"
orvant mimar plan "<motor-oturumu>" --depo "<urun-deposu>"
orvant mimar durum "<motor-oturumu>"
orvant mimar yetki "<motor-oturumu>"
```

İlk planın deposu temiz `main` dalında olmalıdır. Planı ve yetki isteklerini incele; sözleşme onayı bütün izinlerin verilmesi değildir. Ajan kullanıcı adına cevap veya onay üretmez. Ayrıntılı cevap, onay ve devam koşulları [motor akışında](../skills/orvant/references/engine.md) bulunur.

## 3. Mevcut oturumdan devam

Plan zaten varsa aynı oturumu kullan; yeniden `baslat` veya `mimar plan` çağırma. Önce depo/sözleşme bağını, mevcut onayları ve izinleri oku.

```sh
orvant surdur "<motor-oturumu>" --kuru
orvant surdur "<motor-oturumu>" --en-fazla-tur 5 --tur-basina-kosu 3
orvant operator sorular "<motor-oturumu>"
orvant yurut durum "<motor-oturumu>"
```

`surdur` ilk S1 veya ilk planı oluşturmaz. Raporun bitiş nedenini, görevlerin kapı kabulünü ve açık soruları değerlendir; çıkış kodu 0 tek başına tamamlanma değildir. Gerçek cevap/izin kayda işlendiğinde veya tur sınırı içinde yeni yetkili ilerleme mümkün olduğunda devam et.

## 4. Ontoloji ve yerel proje kaydı

Kayıt runtime'ı Python **3.10+** ile Linux, macOS ve Windows için hazırlanmıştır. Windows'ta aşağıdaki `python3` önekini uygun Python sürümünü gösteren `py -3` veya `python` ile değiştir. JSON dosyaları UTF-8 olmalıdır; dosya yolları proje köküne göre `/` kullanır.

Ajan, projenin gerçek türlerini, nesnelerini, ilişkilerini ve görevlerini [model sözleşmesine](../skills/orvant/references/model.md) göre hazırlar. Kullanıcıya JSON doldurtmak gerekmez. Hazırlanmış modelle kaynak skill'den:

```sh
python3 skills/orvant/scripts/project.py init "<proje-koku>" --spec "<model-dosyasi>"
```

Mevcut proje kökünden kopyalı runtime'ı kullan:

```sh
python3 .project/scripts/project.py check .
python3 .project/scripts/project.py context .
python3 .project/scripts/project.py ontology .
```

`check` başarılıyken açık işler bulunabilir. `context` görevlerin hazır/engelli durumunu ve kabul güncelliğini gösterir. `ontology` türleri somut kayıtlarla birlikte gösterir. Markdown görünümleri son üretimdir; canlı komutlar güncelliği yeniden denetler.

## 5. Değişikliği önizle

Ajan, eylem dosyasını gerçek karar ve yetkiye göre hazırlar. Güncel revizyonu `context . --json` çıktısından oku; önizlemenin digest değerini aynen aktar:

```sh
python3 .project/scripts/project.py context . --json
python3 .project/scripts/project.py preview . --event "<eylem-dosyasi>" --expected-revision "<revizyon>"
python3 .project/scripts/project.py apply . --event "<eylem-dosyasi>" --expected-revision "<revizyon>" --preview-digest "<preview_digest>"
```

Revizyon veya digest çakışırsa yeni önizleme al. State yazılmış ama görünüm üretilememişse aynı eylemi tekrar uygulama; canlı context'i oku. `.project` ve gerçek dayanak dosyalarını birlikte yedekle.

Kaynak skill'in güncellenmesi proje kopyasını kendiliğinden yenilemez. Kaynak skill'den `upgrade "<proje-koku>"` runtime'ı yedekleyip yeniler; ontoloji geçişi ayrı işlem olarak incelenir. [Değişiklik ve geçiş rehberi](../skills/orvant/references/plan-changes.md).
