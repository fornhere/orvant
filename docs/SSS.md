# Sık sorulan sorular

[Orvant](../README.tr.md) · [Kullanım](KULLANIM.md) · [Motor](MOTOR.md)

## Skill şu an ne işe yarıyor?

Hedefi, kaynakları, kararları ve işleri yerel proje kaydında birlikte tutar. Yeni oturumda neyin hazır, neyin engelli ve neyin yeniden incelenmesi gerektiğini görmeye yardımcı olur. Yazılım isteğinde ajanı motorun karşılama, ilk plan ve devam akışına yönlendirir.

## Ontoloji mantığı aynı mı?

Evet. Tür ile somut nesne ayrıdır; özellikler ve ilişkiler açık kurallarla tanımlanır. Görevlerin konusu, girdileri ve çıktıları bu nesnelere bağlanır. İlan edilmiş değişim yönleri, bir girdinin değişmesinden hangi iş ve incelemelerin etkilendiğini belirler. İlişkinin adı tek başına doğruluk ispatı değildir.

## `.project` motorun oturumu mu?

Hayır. `.project/state.json` ontoloji/kayıt tarafının yetkili dosyasıdır. Motor; karşılama, sözleşme, plan, yürütme ve operatör kayıtlarını ayrı oturum dizininde tutar. `.project` içindeki kabul edilmiş karar, motorun gösterilen sözleşme revizyonuna verilen onay yerine geçmez.

## İşçi “bitti” dediğinde iş kabul edilmiş mi oluyor?

Motor kabulü bağımsız kapıya dayanır. Kehanet, sözleşmeye bağlı koşulları işçiden ayrı denetler; kapı ilgili kabul komutlarını, kapsamı ve makbuzları da kontrol eder. Bir görevin kabulü bütün projenin tamamlandığı anlamına gelmez.

## `surdur` tek komutla her şeyi başlatıyor mu?

Mevcut planı sürdürür. İlk karşılama, gerçek sorular, sözleşme revizyon onayı ve ilk plan önce hazırlanır. Kesintiden sonra aynı oturumun mevcut durumunu okuyarak devam edilir; yeniden oturum veya plan üretmek gerekmez.

## Hangi ortam gerekiyor?

Kayıt runtime'ı Python 3.10+ ile Linux, macOS ve Windows için hazırlanmıştır. Motor Python 3.11+, Git ve gerçek koşuda Codex CLI goal/sandbox desteği ister. Motor Linux'ta geliştirilmiştir; diğer işletim sistemleri veya farklı Codex sürümleri için doğrulanmış uyumluluk iddiası yoktur.

## Veriler nereye gidiyor?

Kayıt betikleri dosyaları yerel proje klasöründe tutar ve kendi içinde ağ aktarımı yapmaz. Motorun model adımları Codex'i çağırır. Ajanın okuduğu dosyalar için kullanılan AI hizmetinin veri işleme koşulları geçerlidir; kayıtlarına sır eklememelisin.

## Mevcut projemi yeniden kurmam gerekiyor mu?

Tam `.project` kurulumu varsa `init` yeni modeli üzerine uygulamaz. `context` ve `ontology` ile mevcut kaydı oku. Runtime yükseltme ve ontoloji geçişi ayrı işlemlerdir. Mevcut motor oturumu varsa onun sözleşme ve plan bağlarını inceleyerek devam et.

## Kanıt dosyası varsa sonuç kesin doğru mu?

Dosya hash'i ve manifest, hangi dayanağın incelendiğini ve sonradan değişip değişmediğini gösterir. Ölçütün yeterliliği, inceleme beyanının doğruluğu ve gerçek kullanıcı faydası ayrıca değerlendirilir. İlan edilmemiş bağımlılıkları araç kendiliğinden keşfetmez.

## Beta bugün ne için uygun?

Kayıt tarafı kararları ve değişimin etkisini açıkça izlemek gereken yerel projelere yöneliktir. Motorun başlangıç kapsamı küçük Python CLI'ları, veri otomasyonu ve dar depo bakımıdır. Genel otonom proje tamamlama, her alanda güvenilirlik veya ölçülmüş zaman kazancı vaat edilmez.
