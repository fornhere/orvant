# Proje Başlat

AI ile yürüttüğün projeyi hedefleri, somut nesneleri, ilişkileri, görevleri ve
kanıtlarıyla kur; değişikliklerin hangi işleri etkilediğini gör.

## Kullanım

Dosya ve komut araçlarına erişebilen ajanına şunu söyle:

> skills/proje-baslat/SKILL.md dosyasını oku. Projemin alan modelini ve ilk iş
> paketini kur; mevcut proje kaydı varsa güncel durumu okuyarak devam et.

[Skill yönergeleri](skills/proje-baslat/SKILL.md),
[ontoloji rehberi](skills/proje-baslat/references/ontology.md) ve
[veri sözleşmesi](skills/proje-baslat/references/model.md).

Gerekenler: Linux ve Python 3.10+. Kontrol kodu Python standart kütüphanesini
kullanır; kendi içinde API veya model çağrısı yapmaz.

## Ne sağlar?

- Tür, özellik ve ilişki kurallarını doğrular.
- Görevlerin girdilerini, çıktılarını ve üretici bağımlılıklarını izler.
- Değişiklik etkisini uygulamadan önce gösterir.
- Eski kanıtı ve değişmiş dayanakları yeniden inceleme gerektiren işler olarak gösterir.
- Projede okunabilir bağlam ve ontoloji görünümü oluşturur.

Bu yerel araç içerik doğruluğunu veya insan kabulünü ispatlamaz. Alan kuralları
ve gerçek inceleme, projeyi yürüten kişi/ajan tarafından belirlenir.

Bu paket yalnız skill ve çalışma betiklerini içerir.
