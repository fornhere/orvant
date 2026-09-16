# Uygulama tasarımı — proje-baslat v0.3

16 Eylül 2026. Yerel JSON ontolojisi, kontrollü değişiklikler ve dayanağı izlenen
kabul modeli. Python 3.10+ sözdizimi, standart kütüphane, Windows/macOS/Linux CLI.

## Sorumluluklar

- **Skill:** Kullanıcı amacı ve alanı yorumlar, somut kayıtları kurar, işi yapar
  ve içerik kalitesini inceler. İlişki adından doğruluk veya kullanıcı onayı çıkarmaz.
- **ontology.py:** Tür/alan/uç/çokluk kuralları, etki yönleri, girdi kapanışı ve
  okunabilir veri görüntüleri. Genel RDF/OWL/SHACL motoru değildir.
- **acceptance.py:** Açıkça incelenmiş alternatif destekler ve sınırlı alan
  kuralları. Keyfi kod çalıştırmaz; ilişki yolları, sayım, küme eşitliği/ayrıklığı,
  all/any destekler. Sonucun doğru olması ile dayanağın değişmemesi ayrıdır.
- **core.py:** Görev/karar geçişleri, üretici bağımlılıkları, inceleme geçersizliği,
  geçmiş kayıtları, salt okunur context/ontology ve değişiklik provası.
- **project.py:** CLI, dosya sınırları, platforma uygun yazıcı kilidi, revizyon/önizleme
  özeti, atomik state yazımı, yükseltme/geçiş yedekleri ve türetilmiş görünümler.

## Tek kayıt ve veri sözleşmesi

Yetkili dosya `.project/state.json`; CONTEXT.md ve ONTOLOJİ.md ondan türetilir.
Eski şema 1/2 desteklenir. Yeni projeler şema 3 ile kurulur. Tam alan tanımları
[model sözleşmesinde](skills/proje-baslat/references/model.md), alan modelleme
ilkeleri [ontoloji rehberinde](skills/proje-baslat/references/ontology.md) tutulur.

Tür tanımı ile nesne örneği ayrıdır. İlişki türünde iki uç türü, uç başına
min/max sayısı ve forward/reverse/both/none değişiklik etkisi bulunur. Bağlantı
gezinti yönü etki yönüyle aynı olmak zorunda değildir. Yapısal ihlal eylemi
reddeder; alan kabul kuralının bozulması güncel veriyi kaydetmeye engel değildir,
ancak işi tamamlamayı engeller ve eski kabulü güncellikten düşürür.

Görev `object_ids` ile konusunu, `input_ids` ile zorunlu dayanaklarını,
`output_ids` ile ürettiği nesneleri bildirir. Bir çıktının tek üreticisi vardır.
İlan edilmiş etki oklarının tersinde gezilerek girdi kapanışı bulunur;
buradaki üreticiler ile açık depends_on birleşimi etkin iş önkoşuludur.
Bu iş grafında döngü reddedilir; alan grafının her döngüsü yasak değildir.

## Çalıştırma, kabul ve güncellik

1. `start_task`, çalışma başlangıcındaki girdileri `run_snapshot` ile kaydeder.
2. Görev kendi çıktısını üretir. Girdiler değiştiyse eski koşullardaki çalışmayı
   yeni koşullara mal eden `submit_evidence` reddedilir; yeniden başlatılır.
3. Teslim; incelenmiş dosyaların SHA-256 değerlerini, girdi manifestini, seçilmiş
   destekleri, kendi çıktı bütünlüğünü ve alan kurallarının dayanaklarını kaydeder.
4. `complete_task`, bunları yeniden kontrol eder ve tamamlanma sayısını artırır.
5. Context ve değişiklik eylemleri bu dayanakları güncel durumla karşılaştırır.

Girdi manifesti nesne değerleri, etkili bağlar, ilgili tür kuralları, gerçek
file içerik hash'leri ve üreticilerin tamamlanma sayılarını içerir. `input_fields`
seçilmiş özelliklerle sınırlar; etiketler hesap girdisi değildir. Çıktı görüntüsü
kendi nesnelerini/dosyalarını ve onlara gelen etkili bağlantıları alır; tüm
üst veriyi yeniden zorunlu çıktı girdisi yapmaz. Kural gözlemleri, boş sonuçlar
dahil, seçilen nesne/bağ/özellikleri ayrıca saklar. Kural hâlâ geçse bile dayanak
kümesinin değişmesi yeniden inceleme gerektirebilir.

`support_groups` yalnız açıkça incelenmiş dalları sayar. `any` için bir güncel
incelenmiş dal yeterlidir; `all` hepsini ister. API ile gözlenen dal kaybı kalıcı
kaydedilir; kaynağın eski değerine dönmesi veya yeni bir alternatif gelmesi
kendiliğinden eski kabulü diriltmez. Alternatif kanıt bağlantıları impact=none
olarak modellenir; zorunlu hesap girdileri ayrı tanımlanır.

Kaydedilmiş görev durumu, kabul güncelliği ve yapılabilirlik ayrı gösterilir.
Mutasyon/reopen/revise eski kabulü temizlediğinde before/after kayıtları history
içinde kalır. `immutable:true` türler geçmiş içerik ve bağlantıyı korur; yeni
koşul için yeni kimlik gerekir. Silinen nesne/bağ kimliği yeniden kullanılamaz.
Bu yerel geçmiş imzalı veya dışarıdan değiştirilemez bir denetim defteri değildir.

## İşlem sınırı ve prova

Sabit eylemler: görev/karar geçişleri, extend_model, revise_task,
migrate_ontology ve mutate_graph. Mutasyon nesne ekleme/değiştirme/kaldırma,
ilişki ekleme/kaldırma ve tür sözlüğünü değiştirme işlemlerini birlikte alır.
Son graf doğrulanır; geçici bir eksik ilişki aynı işlemde tamamlanabilir.
Önceki ve sonraki graf birlikte etki adaylarını bulur; gerçek görev güncelliği
manifest karşılaştırmasıyla yeniden hesaplanır. İlgisiz etiket değişikliği
bütün işleri eskitmez.

`preview` ve `apply` aynı geçiş motorunu kullanır. Preview diske yazmaz;
etki yolları, değişen görevler ve sonraki bağlamı döndürür. `--expected-revision`
eski durumu, `--preview-digest` durum/eylem/başvurulan dosya gözlemlerinin
önizlemeden beri değişmesini reddeder. Özet, öncesi ve sonrası bütün ilan
edilmiş dosya referanslarını ihtiyatlı biçimde kapsar. Aynı revizyonla tekrar
apply reddedilir; genel idempotency anahtarı/otomatik olay tekrarı yoktur.

CLI init/apply/upgrade işlemleri proje köküne bağlı macOS/Linux flock veya Windows msvcrt kilidi ile sıralanır.
State geçici dosya + os.replace ile atomik yazılır; ardından görünümler üretilir.
Görünüm yazımı başarısızsa state_committed sonucu verilir; olay tekrar uygulanmaz.
Haricî editörler bu kilide uymaz. Dosya gözlemleri atomik dosya sistemi snapshot'ı
değildir; okuma sonrasındaki dış yazımlara karşı tam izolasyon iddia edilmez.

## Yükseltme ve geçiş

`upgrade` kaynak skill'den çalışır; dört runtime dosyasını yedekler ve yeniler.
State ve şemayı değiştirmez. Önceki dosya varlıkları manifestte korunur;
başarısızlıkta eski durum geri yüklenir. Çok dosyalı paket değişimi ani süreç
kesilmesine karşı bütünüyle atomik değildir; yedek kurtarma yolu bildirilir.

`migrate_ontology` bütün eski görevlerin input/output bağlarını açıkça ister.
State ayrıca yedeklenir; eski nesne/görev/kanıt kayıtları history içinde korunur.
Eski tamamlanmış iş yeni alan sözleşmesine otomatik onaylanmış sayılmaz.

## Kanıt ve sınırlar

Davranış kontrolleri `tests/` altında, platform matrisi
[CI tanımında](.github/workflows/tests.yml) bulunur.

Araç içerik hakikatini, inceleyici kimliğini veya gerçek kullanıcı kabulünü
ispatlamaz; gizli bağımlılıkları keşfetmez. Genel sorgu dili, dağıtık işlem,
kurumsal izin sistemi ve otomatik deney yürütme bu sürümün kapsamında değildir.
