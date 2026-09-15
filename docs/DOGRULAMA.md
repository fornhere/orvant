# Kamu paketi yayın doğrulaması

Bu paketteki altı skill dosyası, test edilen v0.2 pilot kaynaklarıyla birebir
karşılaştırıldı; içerik değişikliği yoktur. Yayına hazırlanırken README, teknik
anlatım ve sık sorulan sorular bu kamu paketi için ayrı yazıldı.

## Bu pakette yeniden çalıştırılan kontroller

- Ortam: Linux, Python 3.14.7.
- `python3 -m unittest discover -s tests -q`: **40/40 geçti**.
- `scripts/demo.py`, yeni geçici hedefte: **14 adım geçti**.
- Demo sonunda değişmiş dosya için `check=1`, beklenen sonuç olarak doğrulandı.
- Altı skill dosyası kaynak sürümle birebir aynı.
- Göreli Markdown bağlantıları ve `.agents/skills/proje-baslat` hedefi kontrol edildi.

Python 3.10+ sözdizimi hedefleniyor; bu yayında diğer Python sürümleri, Windows,
macOS veya farklı ajan istemcilerinde uçtan uca kullanım doğrulanmış değildir.

## Sonuçların anlamı

Testler belirli kayıt ve geçiş kurallarını kontrol eder. Genel kullanıcı
verimliliği, gerçek insan memnuniyeti veya Markdown'a üstünlük ölçümü değildir.
Başarılı dosya hash'i içerik kalitesini veya reviewer kimliğini doğrulamaz.
Açık sorular gibi bazı tarihsel metinlerin eskimesini bu sürüm yakalamaz.

Yeniden çalıştırmak için depo kökünden:

```sh
python3 -m unittest discover -s tests -v
```
