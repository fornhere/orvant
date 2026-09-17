# Sık sorulan sorular

[← Orvant](../README.md) · [Kullanım rehberi](KULLANIM.md)

## 1. Tam olarak hangi sorunu çözmeye çalışıyor?

Proje fikrini somut alan kayıtlarına, işlere ve kabul ölçütlerine çevirir; sonraki
oturumda aynı kayıttan devam etmeyi sağlar. v0.3 ayrıca hangi çıktının neye
dayandığını, değişen bir girdinin hangi incelemeyi eskittiğini ve sıradaki işin
neden hazır veya engelli olduğunu gösterir. Genel zaman kazancı ölçülmüş değildir.

## 2. Palantir ile bağlantısı ne?

İlham, nesneleri, ilişkileri ve yapılabilecek eylemleri ortak modelde tanımlamak.
Palantir bağlantısı, ortaklık, lisanslı teknoloji veya kurumsal eşdeğer iddiası yok.
Şirketin piyasa değeri,
bu skill'in değeri veya teknik kapasitesi için ölçüt değildir.

## 3. İyi bir prompt ya da AGENTS.md yetmez mi?

Küçük işlerde yeterli olabilir. AGENTS.md çalışma talimatlarını taşıyabilir;
bu skill ayrıca tür/ilişki doğrulaması, üretici bağımlılığı, karar geçişi ve
kanıt güncelliği hesabı getirir. Her projede daha iyi sonuç veya daha az bakım
iddiası yoktur.

## 4. Bu bir hafıza sistemi mi?

Proje bağlamını dosyalarda saklar; kişisel hafıza kasası, anlamsal arama veya
sohbet arşivi kurmaz. Yetkili kayıt `.project/state.json` dosyasıdır. Başka
oturumun bu kayıtları okuyabilmesi için proje dosyalarına erişmesi gerekir.

## 5. Ontoloji dediği gerçekten ne?

`Deney` bir tür, `Açılış metni denemesi A` o türün somut örneğidir. Türler izinli
özellikleri; ilişki türleri izinli uçları, çokluğu ve değişim yönünü tanımlar.
Görev girdileri bu kayıtlara bağlanır. Örneğin ölçüt değişince ilgili değerlendirme
ve onu kullanan karşılaştırma yeniden incelenir. `ontology` komutu modeli somut
kayıtlarıyla gösterir. Bir kutuya “Deney” yazmak yeterli modelleme sayılmaz.
[Ontoloji rehberi](../skills/orvant/references/ontology.md).

## 6. Her projede çalışır mı?

Türler ve ilişkiler projeye göre tanımlanabilir; bütün alanlar, uzun projeler veya
ekip yapıları kabul testinden geçmiş değildir. Modelin hangi soruları cevaplaması
gerektiğini belirle, ardından küçük bir gerçek değişimle işe yarayıp yaramadığını
sına. Örnekler belirli davranışları gösterir; evrensel alan modeli sağlamaz.

## 7. AI'ın hata yapmasını engeller mi?

Belirli yanlış geçişleri reddeder: hatalı tür/bağ, eksik zorunlu ilişki, hazır
olmayan üreticiyi kullanma, çalışma sırasında değişen girdiye eski sonucu bağlama
ve güncel kanıt olmadan tamamlama gibi. Yanlış ölçüt veya yanlış değerlendirme
yine mümkün. Araç bütün ajanın davranışını denetleyen bir güvenlik sınırı değildir.

## 8. Kanıt ve snapshot varsa iş kesin doğru mu?

Hayır. Dosya hash'i ve girdi/çıktı manifest'i hangi kayıtların kontrol edildiğini
ve sonradan değişip değişmediğini gösterir. İnceleme beyanının doğruluğu, ölçütün
yeterliliği ve insan kimliği doğrulanmaz. Raporun kontrol ettiği zorunlu dosyalar
evidence'a bağlanır; alternatif kaynak dosyaları destek dallarının manifest'inde
izlenir. Bütün alternatifleri zorunlu evidence yapmak `any` grubunu etkisizleştirir.
Motor modellenmemiş kaynakları kendiliğinden keşfetmez.

## 9. Bedava mı, API anahtarı gerekli mi?

Python runtime standart kütüphane kullanır; kendi API çağrısı veya API anahtarı
zorunluluğu yoktur. Konuşmayı modele çeviren AI aracının erişim/ücret koşulları
ayrıdır. Bu teknik özellikler depoya dağıtım veya lisans izni vermez.

## 10. Hangi ajan ve işletim sisteminde çalışıyor?

Runtime Python 3.10+ ile Windows, macOS ve Linux için düzenlendi. Windows/macOS/Linux ve Python 3.10/3.14 için test ve demo koşuları
[CI üzerinde geçti](https://github.com/fornhere/orvant/actions/runs/35109602302).
[Platforma göre komutlar](KULLANIM.md#işletim-sistemine-göre-komutlar). JSON/Markdown taşınabilir; otomatik keşif ve komut çalıştırma
istemciye bağlıdır. [Resmî Codex skill belgesi](https://learn.chatgpt.com/docs/build-skills).
Güncel koşular [GitHub Actions](https://github.com/fornhere/orvant/actions) sayfasındadır.

## 11. Veriler nereye gidiyor?

Kontrol betikleri kayıtları ve kanıt referanslarını yerel proje klasöründe tutar;
kendi içinde ağ aktarımı yapmaz. Ajan dosyaları okuduğunda kullanılan AI hizmetinin
veri işleme koşulları geçerlidir. Yerel kayıt kullanmak AI'ın çevrimdışı olduğu
anlamına gelmez. Kayıtlara sır ekleme; seçtiğin yedekleme/senkronizasyon kapsamını bil.

## 12. Mevcut dosyalarım ezilir mi?

`init`, mevcut AGENTS.md'yi korur. Tam kurulumda tekrar init no-op'tur; yeni spec'i
eski kayda uygulamaz. Yarım/bozuk kurulum sıfırlanmaz. Entegrasyon
`.project/integration.md` üzerinden incelenir. `upgrade` runtime dosyalarını
yedekler; ontoloji migrasyonu da eski state'in tam yedeğini alır. Bu davranışlar
ajana sonradan verilen ayrı dosya düzenleme yetkilerini kısıtlamaz.

## 13. Yeni oturumda nasıl devam ederim?

Proje kökünde `python3 .project/scripts/project.py context .` çalıştır.
Türleri ve somut yolları görmek için `python3 .project/scripts/project.py ontology .`
kullan. Markdown dosyaları son üretilen görünümdür; canlı komutlar dosya ve
snapshot güncelliğini yeniden kontrol eder. [Kullanım rehberi](KULLANIM.md).

## 14. Birden çok ajan aynı anda çalışabilir mi?

Bağımsız işleri inceleyip bulgularını ana yazıcıya getirebilirler. v0.3 CLI
`init`, `apply`, `upgrade` işlemlerini aynı kullanıcı/proje için işletim sistemi advisory
kilidiyle sıraya koyar. `--expected-revision` eski kaydı, preview digest ise
state/eylem/referans dosya kaymasını reddeder. Doğrudan dosya editörleri veya
başka betikler kilide uymak zorunda değildir; ortak dosyaları paralel elle yazma.

## 15. Sonradan modeli veya hedefi değiştirebilir miyim?

`extend_model` nesne/ilişki/görev ekler; `revise_task` görev tanımını değiştirir.
V3 `mutate_graph` nesne ve ilişki ekleme/düzeltme/silme ile ontoloji şeması
revizyonunu atomik bir işlemde destekler. Etkileri preview'da görülür ve bağlı
incelemeler güncelliğini kaybeder. Tarihsel immutable kayıtlar için yeni kimlik
gerekir. Genel proje hedefini güncelleme ve görev iptal etme eylemi hâlâ yoktur.
[İşlemler ve geçiş](../skills/orvant/references/plan-changes.md).

## 16. Testler neyi kanıtlıyor?

`tests/` kümesi kayıt/kanıt/bağımlılık, alan değişimi, kabul, yükseltme ve
platform davranışlarını denetler; demo 14 sentetik adımı yürütür.
Bunlar genel AI güvenilirliği, bütün saldırılara dayanıklılık, kullanıcı
memnuniyeti veya zaman kazancı kanıtı değildir. [Test komutları](KULLANIM.md).

## 17. Bir kaynak kaybolunca bütün iddia yanlış mı sayılıyor?

Hayır. Normal girdiler birlikte gerekli kabul edilir. Alternatif destek için
`support_groups` içindeki `any`/`all` grupları kullanılır. Yalnız gerçekten
incelenip `reviewed_supports` ile kaydedilen dallar kabul edilir. `any` grubunda
başka incelenmiş güncel dal kalırsa yeterlilik korunabilir. Yeni bir kaynak
kendiliğinden alternatif kabul olmaz; kaybı kaydedilmiş dalın geri gelmesi açık
yeniden inceleme gerektirir. Destek yeterliliği, iddianın doğruluğunun ispatı değildir.

## 18. Aynı ölçütle karşılaştırıldığını otomatik denetleyebilir mi?

İlan edilmiş dar veri kuralları için evet: `equal_sets`, `disjoint`, `count` ve
iç içe `all`/`any` kabul kapıları bulunur. Örneğin iki değerlendirmeden ulaşılan
ölçüt kimliklerinin aynı olması kontrol edilebilir. Boş küme eşitliği tek başına
yeterli değildir; gerekiyorsa count şartı eklenir. Bu, deney tasarımının kalitesini
veya kazananı otomatik belirlemez. Serbest kod/eval veya genel mantıksal çıkarım yoktur.

## 19. Skill'i kurunca bütün projeyi yapmış oluyor muyum?

Hayır. Skill çalışma modelini kurar ve sürdürür. İçerik, kod, araştırma ve gerçek
incelemeyi ajan veya insan yapar. Kurulum isteği bütün işleri bitirme veya dışarıya
yayınlama yetkisi değildir. Kullanıcının verdiği devam yetkisi kapsamında ilerlenir.

## 20. Bugün ne için uygun?

Alan kayıtları, kararlar ve değişim etkisinin açıkça izlenmesi gereken yerel bir
projede denenebilir. Ontoloji şeması, kanıt, alternatif destek ve yeniden inceleme
akışını birlikte kullanır. Her organizasyon için ontoloji tasarlamaz, kişisel
hafıza kurmaz, çok kullanıcılı yetki sistemi veya kurumsal veri platformu sunmaz.
