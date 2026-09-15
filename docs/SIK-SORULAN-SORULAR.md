# Sık sorulan sorular

## Bu skill tam olarak ne?

Fikrinden hedef, sınır, görev, karar ve kanıt düzeni çıkarması için AI ajanına
verilen talimatlar ve bunları denetleyen küçük Python kodu. Skill bir AI modeli değildir.

## Kurulumdan sonra işi biter mi?

Hayır. Güncel bağlamı okuma, değişiklikleri uzlaştırma ve sonuçları kanıtlarıyla
kaydetme akışı da var. Bunu ajan yürütür; skill beklerken çalışmaz.

## Kodlama bilmem gerekiyor mu?

Kayıtları konuşmadan ajan hazırlar; elle JSON yazman beklenmez. Ortamda Python
ve dosya/komut araçlarına erişen bir ajan gerekir. Sonuçları gözden geçirmek önemlidir.

## Hangi projelerde kullanılır?

Birkaç oturuma yayılan küçük yazılım, araştırma veya içerik projelerinde denenebilir.
Nesneler projeye göre seçilir. Her tür projede etkili olduğu doğrulanmış değildir.

## Neden yalnız Markdown kullanmayayım?

Kullanabilirsin. Buradaki ek, belirli ilişkiler ve kanıt güncelliği üzerindeki
programatik kontrollerdir. İyi Markdown planından genel olarak daha faydalı
olduğuna dair karşılaştırmalı kanıtımız henüz yeterli değil.

## Bütün modellerle çalışır mı?

Talimat dosyası okunabilir metindir; yardımcı kod Python'dur. Her ajan istemcisinde
keşif, yönergeye uyma ve komut çalıştırma davranışı test edilmedi. En açık giriş,
ajana `skills/proje-baslat/SKILL.md` yolunu doğrudan vermektir.

## Otomatik ajan filosu kurar mı?

Hayır. Birden fazla ajan kullanılırsa tek durum yazıcısı gerekir. Bu paket süreç
yöneticisi veya zamanlayıcı içermez.

## Veriler nereye gider? Ek API anahtarı gerekir mi?

Python kodu yerel dosyalarla çalışır; kendi içinde LLM veya haricî servis çağrısı
ve API anahtarı ihtiyacı yoktur. Kullandığın AI ajanının kendi veri iletişimi ve
ücretlendirmesi bundan ayrıdır.

## “Tamamlandı” kalite garantisi mi?

Hayır. Her ölçüt için dosya kanıtı gerekir; dosyanın değişip değişmediği kontrol
edilir. Ölçütün yeterli olması, incelemenin doğru yapılması ve insan kabulünün
gerçek olması ayrıca değerlendirilmelidir.

## Mevcut dosyalarım korunur mu?

Kurulum mevcut dosyaları korumaya çalışır, tam kurulumu yeniden başlatmaz ve
yarım kurulumda çakışma bildirir. Mevcut AGENTS yönergeleriyle bağlantının
uzlaştırılması gerekebilir. Bu davranışların testleri repodadır; her ortam
için genel garanti verildiği anlamına gelmez.

## Kapsam değişirse?

Yeni nesne/ilişki/görev ekleme ve görev tanımı revizyonu desteklenir. Kararlar
yeni kararlarla değiştirilebilir. Genel hedef veya açık soru düzenlemesi gibi
desteklenmeyen alanlarda ajan kaydı gizlice yamamamalı; sınırı açıkça belirtmeli.

## Neden pilot diyorsunuz?

Tek yazıcı, kısıtlı model değişiklikleri ve kayıt bakım yükü gibi sınırlar var.
Teknik kontrolleri geçmek ile gerçek kullanıcıya fayda sağlamak ayrı sorular.
