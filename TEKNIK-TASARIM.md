# Teknik tasarım

## Yapı

- `skills/proje-baslat/SKILL.md`: ajan iş akışı.
- Skill içindeki `references/model.md`: şema, komutlar ve eylem örnekleri.
- Skill içindeki `references/plan-changes.md`: model büyümesi ve görev revizyonu.
- Skill içindeki `scripts/core.py`: yapı doğrulama, hesaplanan durum, kanıt hash'leri ve eylemler.
- Skill içindeki `scripts/project.py`: kurulum, dosya işlemleri ve CLI.
- `tests/`: davranış kontrolleri; `examples/`: sentetik tanım; `scripts/demo.py`: kontrollü demo.

## Durum modeli

Proje bilgileri, nesneler, ilişkiler, kararlar, görevler, revision ve history
JSON'da tutulur. Görev yaşam döngüsü todo → doing → review → done'dır;
yapılabilirlik bağımlılıklar ve güncel kanıtlardan ayrıca hesaplanır.
`needs_review` gibi hesaplanan durumlar, kayıtlı done durumunun güncel
başarı sayılmadığını gösterebilir.

Görev revizyonu kabul ölçütlerini, önkoşulları, nesne ve karar bağlarını açıkça
kaydeder. Etkilenen görevlerin eski kanıtları temizlenir. İlişki adları anlam
etiketleridir; kod `supports` ilişkisinden iddia doğruluğu çıkarmaz.

## Yazma ve denetim

`apply --expected-revision` son okunan sürümü doğrular. Eşzamanlı kilit değildir;
tek yazıcı gerekir. Yapı ve eylem doğrulanır, yeni state ayrı hazırlanır.
state ve CONTEXT iki ayrı dosyadır; CLI, state yazıldığı halde görünümün
güncellenememesi durumunu ayrıca bildirir.

Kanıt, bir kabul ölçütüne bağlı dosya yolu, SHA-256, açıklama ve inceleyen rol
içerir. Kimlik doğrulama veya içerik doğruluğu garantisi sağlamaz. History bütün
olay girdilerini ve eski dosya sürümlerini otomatik korumaz; tam replay iddiası yoktur.

## Doğrulama

```sh
python3 -m unittest discover -s tests -v
```

Testler yapı doğrulama, bağımlılıklar, kanıt eskimesi, karar değişimi, kurulum
çakışmaları, model büyümesi, görev revizyonu ve runtime yükseltmesini kapsar.
[Son yayın kontrolü](docs/DOGRULAMA.md).
