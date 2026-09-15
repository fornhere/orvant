# Projeye özgü ontoloji kurma ve kullanma

Ontoloji, projede hangi şeylerin var olduğunu, niteliklerini, birbirine nasıl
bağlandığını ve değişiklikte hangi işin yeniden inceleneceğini açıklar. Görsel bir
şema tek başına yeterli değildir; kayıtlar soruları cevaplamalı, hatalı veri
reddedilmeli ve değişim gerçek çalışma akışını etkilemelidir.

Bu runtime küçük, kapalı bir tür/ilişki sözleşmesidir. OWL/SHACL uygulaması,
evrensel bilgi grafı veya Palantir eşdeğeri değildir. Kural motoru, ilişki adından
gerçeklik çıkarmaz; açık veri türü, uç, çokluk, etki ve görev bağlarını işletir.

## 1. Önce cevaplanacak soruları yaz

Projeye göre birkaç soru seç. Deney Defteri örneği:

- Deney A ve B aynı amaç için mi; hangi komut/ayar değişmiş?
- Çıktı A hangi deney ve komut sürümünden geldi?
- Bu değerlendirme hangi çıktı ve hangi ölçüt sürümüne dayanıyor?
- Bir ölçüt değişirse hangi değerlendirme, karşılaştırma ve görev yeniden incelenmeli?
- Yeni değerlendirme hazır mı; kullandığı çıktı üreticisinin işi güncel olarak bitmiş mi?

Bunlar test edilecek modelleme sorularıdır; şemaya desteklenmeyen
`competency_questions` alanı ekleme. Gerekirse proje içinde `MODEL-SORULARI.md`
yaz, ilgili modelleme görevinin ölçüt/kanıtlarına bağla. Soruya veri üzerinden
cevap verilemiyorsa kaç düğüm çizildiği başarı sayılmaz.

## 2. Tür, özellik ve somut örnek

| Kavram | Tür tanımı | Somut kayıt |
| --- | --- | --- |
| Deney | Hangi özelliklerin gerektiği | Açılış metni denemesi A |
| Komut | Metin ve sürüm özellikleri | A deneyinde kullanılan komutun kayıtlı metni |
| Çıktı | Metin veya güvenli file özelliği | A deneyinde elde edilen belirli çıktı |
| Ölçüt | Ad/açıklama/sürüm | Tek cümle koşulu, sürüm 1 |
| Değerlendirme | Sonuç/gerekçe | Çıktı A'nın ölçüt 1'e göre incelemesi |
| Karşılaştırma | Amaç ve yorum | A ve B'nin aynı ölçütle karşılaştırması |

Tür bir kez tarif edilir; örnekler ayrı kimlikler taşır. `Deney` nesnesinin içine
“amaç, model, komut” alan adlarını koymak gerçek deney kaydı oluşturmaz. Henüz
çalıştırılmamış deneyin sonucunu doldurma. Bir alanın ayrı nesne olmasını kimlik,
bağımsız değişim, yeniden kullanım veya izlenebilirlik gerekçesi belirlesin.
Sıcaklık ayarı özellik olabilir; ortak kullanılan ölçüt ayrı nesneye daha uygundur.

Şema alanları [model sözleşmesinde](model.md) tanımlıdır. Kısa Türkçe etiketler
kullan; ilişki anlamını ve alanın birimini anlaşılır yaz. Kod sayısal aralık veya
özel alan mantığını doğrulamıyorsa bunu doğrulanıyor diye sunma; görev ölçütü ve
incelemeyle ele al veya yetkili runtime geliştirmesi olarak ayrı iş yap.

## 3. İlişkinin okunması ile etki yönünü ayır

Örneğin `degerlendirme-a — applies → olcut-tek-cumle-v1` ilişkisinin metinsel
okunuşu “değerlendirme ölçütü uygular”dır. Ölçüt değişince değerlendirme etkilenir;
bu yüzden `impact: reverse` seçilir.

| Etki | Kaynak değişince | Hedef değişince |
| --- | --- | --- |
| `forward` | Hedef etkilenir | Kaynağa yayılmaz |
| `reverse` | Hedefe yayılmaz | Kaynak etkilenir |
| `both` | Hedef etkilenir | Kaynak etkilenir |
| `none` | Yayılmaz | Yayılmaz |

Deney Defteri'nde örnek yol: `ölçüt → değerlendirme → karşılaştırma`. Oklar bu
satırda **değişim etkisini** gösterir; saklanan ilişkiler ters okunabilir.
Bu zincirin sonunda girdisi karşılaştırma olan görev yeniden incelemeye düşer.
Yalnız açıklama amacıyla ilişkilendirilmiş ürün etiketi için `none` uygun olabilir.

Çokluk her uç için ayrı düşünülür. Karşılaştırmanın tam iki deneye bağlanması
`compares.from_min=2`, `from_max=2` ile anlatılır. Deneyin farklı karşılaştırmalarda
yer alması için hedef tarafı `to_min=0`, `to_max=null` olabilir. İki ilişkiyi aynı
deneye çoğaltmak “iki ayrı deney” sağlamaz; yinelenen üçlü reddedilir.

İlişkiler ve zorunlu uçlar beraber eklenir. Son grafik doğrulandığından iki deneyi
ve tam iki bağı tek transaction'da yaratabilirsin. Tarihsel kayıtta bir ilişkinin
silinmesi gerekiyorsa etki eski ve yeni grafı birlikte kullanır.

## 4. Alan grafiğini görevlerle bağla

Bir görev hangi nesneyi **konu ediyor**, hangisini **girdi olarak kullanıyor**,
hangisini **üretiyor**? Üçü aynı şey değildir:

- `object_ids`: genel konu/bağlam.
- `input_ids`: inceleme veya üretim dayanağı.
- `output_ids`: görevin üreteceği nesne.

Örneğin “A çıktısını ölçüt 1 ile değerlendir” görevinin girdileri çıktı A ve ölçüt
1; çıktısı değerlendirme A'dır. Karşılaştırma görevinin girdileri değerlendirmeler,
çıktısı karşılaştırma kaydıdır. Aynı nesneyi tek görevin hem girdisi hem çıktısı
yapma. Yeni sürüm nesnesi kullanmak bu ayrımı korur.

Girdi kapanımı ilişki etki okları ters gezilerek bulunur. Bu kapanımdaki nesnelerin
üretici görevleri etkin önkoşuldur; ayrıca elle depends_on yazılmasa da kapı çalışır.
Açık iş sırası gerektiğinde `depends_on` kullan. Bir çıktının iki üreticisi, kendi
çıktısını dolaylı tüketen görev veya birleşik görev döngüsü reddedilir.

Çıktı nesnesinin listede bulunması üretilmiş olduğu anlamına gelmez. `object_status`
üretici görevden türetilir; `pending` veya `needs_review` girdiyi güncel sonuç diye
sunma. Üreticisi olmayan başlangıç verisi `input` olarak görünür.

## 5. Geçmiş koşul ile bugünkü ölçütü karıştırma

Bir model koşusu belirli komut ve ayarlarla bir kez yapılmış olaydır. Bugün yeni
ölçüt seçmek, o gün üretilen metni değiştirmez. Eski koşuyu saklamak için komut,
ölçüt ve deney sürümlerini ayrı nesne kimlikleriyle tutmak uygundur:

- `olcut-v1` ve `deney-a-v1` tarihsel kayıtlar olarak kalır.
- Yeni kural `olcut-v2`; yeniden değerlendirme için yeni değerlendirme/görev.
- Karşılaştırma hangi sürümleri kullandığını açıkça bağlar.

Tarihsel koşu/komut sürümü ve provenance ilişki türlerinde `immutable: true`
kullanabilirsin. Motor bu türlerdeki mevcut nesnenin özellik/tür değişimini ve
silinmesini, immutable ilişkinin değişimini/silinmesini reddeder; yeni sürüm
kimliği gerekir. Nesnenin görünüm etiketi düzeltilebilir. Değişebilir çalışma
kayıtlarındaki veri hatasını `replace_object` ile düzelt ve etkilerini yeniden
incele. Genel state/history dosyaları hâlâ değiştirilebilir; bu append-only veya
güvenlik sınırı değildir. İlişki “supports” diye adlandırıldı
diye eski deneyi yanlış veya yenisini başarılı ilan etme.

## 6. Snapshot ve dosya kanıtını birlikte kullan

Kanıt sunarken motor girdilerin o anki manifest'ini ve hash'ini yakalar. İlan
edilmiş alan kayıtları, etkili ilişkiler, ilgili şema kuralları, `file` özellikleri
ve önkoşul tamamlanma kuşakları kapsanır. Sıradan metinde anılan dosya veya
modellenmemiş kaynak kendiliğinden keşfedilmez. Raporu ve gerçekten kontrol ettiği
zorunlu kaynak/çıktı dosyalarını doğru evidence ölçütüne bağla. Alternatif kaynak
sürümleri aşağıda anlatılan destek dalı manifest'inde izlenir.

Snapshot değişince inceleme eskir; bu “içerik yanlıştır” hükmü değildir. Aynı
şekilde hash aynıysa kontrol edilen koşulların değişmediğine dair teknik işaret
vardır; incelemenin kaliteli veya doğru yapıldığını kanıtlamaz. Manifest dosyanın
hash'ini saklar; geçmiş dosya içeriğini geri getirmek için ayrı sürümleme/yedek gerekir.

## 7. Birlikte gerekli destek ile alternatif desteği ayır

`input_ids` normal girdileri birlikte gerekli kabul eder. “Kaynak A **veya** B
incelenmişse yeterli” koşulunu iki normal girdi yazarak modelleme. Göreve
`support_groups` tanımla; `mode: any` kabul edilmiş dallardan en az birini,
`mode: all` bütün tanımlı dalları gerektirir. Her dal açık `input_ids` ve destek
iddiasını gösteren `relation_ids` taşıyabilir. Alternatif witness ilişkilerini
`impact: none` tanımla; aynı kaynakları ayrıca zorunlu task.input_ids içine koyma.
Grup snapshot'ı bu tanıklıkları ayrıca izler. Alternatif kaynak nesnelerinin
`file` özellikleri dal manifest'inde hashlenir; bu kaynak dosyalarını yeniden
zorunlu evidence'a koymak `any` grubunu istemeden “hepsi gerekli” yapar. Mutlak
gerekli rapor/çıktılar evidence'da, alternatif kaynak sürümleri `support_snapshot`
altında tutulur. Gerçek hesap girdileri normal `input_ids` olarak kalır.

Destek ilişkisi ilgili iddiaya gerçekten bağlanmalı. İki uç arasında bir bağın
bulunması, kaynağın metinsel olarak iddiayı desteklediğini ispatlamaz. Ajan/insan
pasajı inceleyip `submit_evidence` içinde açıkça `reviewed_supports` bildirir.
Yeni bir kaynak eklenmesi onu kendiliğinden incelenmiş alternatif yapmaz.

`any` grubunda incelenmiş A kaybolur ama incelenmiş B güncelse grup yeterli
kalabilir. `all` grubunda bir dal kaybı yeterliliği bozar. Eylemle kayda alınmış
kayıp dal, dosya/bağ eski haline geldi diye tekrar kabul edilmez; açık inceleme
gerekir. Kaynak desteğinin kaybı, iddianın yanlışlığına otomatik dönüşmez.

Yalnız gerçekten alternatif olan kaynakları gruplandır. Her durumda gerekli
olgu/ölçüt normal girdi olarak kalır. Bütün alternatifleri aynı zamanda normal
girdilere koymak bu ayrımı bozar. Sözleşme örnekleri [model belgesindedir](model.md).

## 8. Karşılaştırma koşullarını açık kabul kurallarıyla denetle

Tür/çokluk, iki değerlendirmenin aynı ölçüt kümesiyle yapıldığını tek başına
söylemez. `acceptance_rules` ile sınırlı veri kuralları tanımlanabilir:
`equal_sets`, `disjoint`, `count` ve iç içe `all`/`any`. Selector kayıtlı köklerden,
ilan edilmiş ilişki türleri boyunca in/out yönde yürür; nesne kimliklerini veya
tek bir property değerini seçer. Bu yürüyüş etki yönünden bağımsızdır.

Örnek: A ve B değerlendirmelerinden `applies` bağlarıyla ulaşılan ölçüt kimlikleri
eşit olmalı. Boş kümeler de eşit olduğundan yanına her iki taraf için `count ≥ 1`
koy. Aynı ölçüt kümesiyle değerlendirilmiş olmak iyi deney tasarımını veya
kazananı ispatlamaz; sadece ilan ettiğin karşılaştırılabilirlik koşulunu sınar.

Kuralda bilinmeyen ilişki türü veya operatör yapı hatasıdır. Kaybolan kök/özellik
ya da tutmayan karşılaştırma ise açıklamalı başarısız kabul koşuludur; projenin
okunamayan bozuk JSON'a dönüşmesi gerekmez. Kuralın gezdiği kayıt ve ilişki tanıklıkları kabul snapshot'ında gözlenir; aynı
boolean sonuç, dayanak değişimini gizlemez. Gerçek iş girdilerini ayrıca
`input_ids` ile bağla; selector'ü üretici önkoşulu yerine kullanma. Genel kod,
eval, serbest formül veya mantıksal teorem ispatı çalıştırılmaz.

## 9. Kurulan ontolojiyi gerçekten sorgula ve göster

```sh
python3 "<root>/.project/scripts/project.py" ontology "<root>"
python3 "<root>/.project/scripts/project.py" ontology "<root>" --json
python3 "<root>/.project/scripts/project.py" context "<root>" --json
```

Markdown görünümü türleri, özellikleri, çokluğu, etki yönünü, somut kayıtları,
ilişkileri, görev bağlarını ve aynı modelden Mermaid grafını verir. Ayrı elle
çizilmiş ikinci modeli güncel gerçek diye tutma. JSON sorgulanabilir kayıttır;
genel amaçlı sorgu dili/SQL arayüzü yoktur. Ajan ilgili kimlik ve yolları bu
veriden okuyarak soruyu cevaplar.

İlk teslimde ve anlamlı değişimde kullanıcıya bir **somut yol** göster: örnek
kimliklerini, neyin değiştiğini, hangi görevin neden etkilendiğini ve sonraki
kontrolü anlat. Başlangıç sorularını kayda karşı cevapla; yanıt verilemeyen
soruyu model eksikliği olarak görünür tut. Diyagram, bu değerlendirmenin yardımcısıdır.


Salt okunur `context` gözlemi geçmişe yazmaz. Kaynak kaybının kalıcı dal kaydı,
bir eylem işlendiğinde oluşur; dışarıdan değiştirilip geri alınmış dosyanın
bütün geçmişini bu yerel model ispatlayamaz.
