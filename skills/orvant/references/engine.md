# Hedeften motorla yürütmeye ve devam etmeye

Bu rehber yazılım geliştirme isteğini mevcut CLI ile yürütür. Skill'i kullanan
ajan başlangıç komutlarını çağırır; `surdur` mevcut planı işletir. Yeni arayüz,
servis veya harness ekleme. Kullanıcıya JSON hazırlatma ve görevleri tek tek
seçtirme. Aşağıdaki bloklar koşullara bağlı tariflerdir; hepsini sırayla çalıştıran
bir onay betiği değildir. Yer tutucuları gerçek kayıttan doldur.

## Motoru, yorumlayıcıyı ve yolları doğrula

`<skill-dir>`, okunan SKILL.md'nin gerçek dizinidir; keşif bağlantısı varsa hedefini
çöz. `<project-root>` yazılımın Git depo kökü, `<motor-oturumu>` bu projeye ait
motor çalışma dizinidir. Dizinin adı serbesttir; motor oturumu `.project` değildir.
Motor oturumu ile ürün deposu birbirini içermesin ve Orvant kaynak ağacının dışında
olsun. Komutlarda bu iki yolu mutlak ve alıntılanmış kullan; `cd` ile anlamları değişmesin.

Kayıt runtime'ının Python 3.10+ olması motor için yeterli değildir. Motor Python
3.11+, Git ve gerçek model/yürütmede Codex CLI ister. Motor Linux'ta geliştirilmiştir; kayıt runtime'ının Windows/macOS kanıtını motora taşıma.
Gerçek yürütücü Codex'tir; diğer yürütücüler veya genel proje otonomluğu kanıtlanmış
sayılmaz. Mevcut durum ve sürüm sınırları için [motor belgesini](../../../docs/MOTOR.md)
ve [kurulum rehberini](../../../README.md) oku. Model varsayılanı bütün rollerde
`gpt-6.1-sol`dur; kullanıcı isteği olmadan değiştirme.

Yayın ağacında `<skill-dir>/../..` depo köküdür; motor kaynağı onun `engine/`
dizininde, modül `engine/orvant_op` içindedir. Bu dizinin varlığını kontrol et;
kullanıcının çalışma klasöründe aynı isimli bir modül var diye onu seçme. Kaynaktan
çalıştırıyorsan doğrulanmış motor yorumlayıcısıyla bu dizinden çalıştır:

```sh
cd "<skill-dir>/../../engine"
python3 --version
python3 -c "import orvant_op; print(orvant_op.__file__)"
```

Buradaki `python3`, motorun doğrulanmış 3.11+ yorumlayıcısıdır; gerekiyorsa bütün
motor komutlarında onun tam yolunu kullan. Sanal ortamda yalnız `cd` yapmak
yorumlayıcıyı seçmez. Kaynak yoksa, kurulu motorun yorumlayıcısıyla modülün
konumunu doğrula veya o ortamın `orvant` giriş noktasını kullan. Kurulu `orvant`,
`python3 -m orvant_op` ile aynı CLI'dir; aşağıdaki tariflerde bu öneki değiştirebilirsin.
Skill'in tek başına kopyalanması motor paketini kurmaz. Motor bulunamazsa eksik
kurulumu bildir; `.project` betiğini motor yerine çalıştırma.

Seçtiğin CLI'de `karsila --help`, `mimar plan --help`, `surdur --help` ve
`operator cevapla --help` ile desteklenen argümanları doğrula. `--json` her komutta
yoktur; kullanıcının teknik CLI çıktısını doldurması gerekmez. Gerçek koşu model
çağırabilir ve ürün deposunu değiştirebilir; sentetik test veya demo bunun gerçek
model ve OS sandbox kanıtı değildir.

## Aynı projedeki başlangıç durumunu oku

Önce proje yönergelerini ve kaydedilmiş oturum yolunu incele. `.project` varsa
onun kopyalı betiğiyle canlı `context` ve gerekirse `ontology` oku; kaydı koru.
Bu kayıttaki hedef, görev veya kabul edilmiş karar, S1 onay olayı yerine geçmez.
Motor yolunu proje notlarından veya verilmiş çalışma kökünden bul; başka sohbet
ya da kişisel hafızanın kendiliğinden arandığını varsayma. Oturumun
`karsilama/oturum.json` hedef/kaynaklarını ve varsa `plan/plan.json` içindeki
`depo.yol` değerini bu projeyle eşleştir. Birden çok uyuşan oturum varsa gerçek
seçimi netleştir; kısmi veya bozuk kaydı yeni oturum gibi sıfırlama.

Mevcut S1 kaydı varsa şu canlı komutları oku:

```sh
python3 -m orvant_op karsila durum "<motor-oturumu>"
python3 -m orvant_op karsila sozlesme "<motor-oturumu>"
```

Plan varsa ayrıca `mimar durum` ve operatör sorularını oku. Onay, sözleşmenin
güncel `revizyon` değeriyle eşleşen `onay.revizyon`, `onay.olay_id` ve gerçek
`kullanici_onayi` olayına dayanmalı; onay durumu `onaylandi` veya
`fizibilite_onayli` olabilir. Taslak kapısının olumlu sonucu kullanıcı onayı
değildir. Fizibilite onayı ürünün tamamlanma onayı değildir.

| Başlangıç | Sonraki adım |
| --- | --- |
| Oturum yok | Yeni, ayrı çalışma yolunda `karsila baslat`, sonra S1 otomatik adımları. |
| Onaysız S1 var | Aynı oturumda soruları/otomatik adımları sürdür; gösterilen sözleşme revizyonunun gerçek onayını bekle. İlk planı veya `surdur`u çağırma. |
| Onaylı S1 var, plan yok | Aynı onaylı revizyonla ilk `mimar plan`; S1'i yeniden başlatma veya tekrar onay isteme. |
| Mevcut plan var | Planın depo ve sözleşme revizyonu bağını, karar/yetki kayıtlarını oku; aynı oturumda `surdur`. `mimar plan` ile planı yeniden kurma. |

## S1: hedef, gerçek sorular ve sözleşme onayı

Yalnız oturum yokken kullanıcının hedefini sadık biçimde aktar; hedefe yeni bütçe,
tarih, kapsam veya izin taahhüdü ekleme:

```sh
python3 -m orvant_op karsila baslat "<motor-oturumu>" --hedef "<hedef>"
python3 -m orvant_op karsila ilerle "<motor-oturumu>"
python3 -m orvant_op karsila durum "<motor-oturumu>"
python3 -m orvant_op karsila sorular "<motor-oturumu>"
```

Gerçek kaynaklar varsa `baslat`a her doğrulanmış kaynak için `--kaynak "<kaynak-yolu>"`
ekle. Envanterde bir araç veya kaynak görülmesi kullanım izni değildir. `ilerle`
tek otomatik adım yürütür; her çıktıyı okuyup otomatik S1 adımlarında tekrar çağır.
`soru_bekliyor` durumunda gerçek kimlik, soru, seçenek ve gerekçeyi kullanıcıya
taşı. Sonucu etkileyen kararı sor; küçük uygulama tercihlerini yetkili kapsamda
gerekçelendir. Önceden verilmiş cevap ilgili soruyu aynı kapsamda çözüyor ise
yeniden sorma; gerçek kullanıcı sözünü kayda aktar:

```sh
python3 -m orvant_op karsila cevapla "<motor-oturumu>" "<soru-id>" "<kullanici-cevabi>"
python3 -m orvant_op karsila ilerle "<motor-oturumu>"
python3 -m orvant_op karsila sozlesme "<motor-oturumu>"
```

`cevapla`yı gerçek cevap gelmeden veya ajan önerisini kullanıcı cevabı sayarak
çağırma. Otomatik adımların sayısını sabitleme; aynı durum yeni ilerleme olmadan
tekrarlanıyorsa nedeni incele. `onay_bekliyor` durumunda sözleşmenin hedefini,
kaynaklı gereksinimlerini, gözlenebilir kabullerini, kapsamını ve açık/ertelenmiş
kararlarını kullanıcıya göster. `<revizyon>` değerini gösterilen sözleşmeden oku;
oturum revizyonuyla veya örnek sayıyla değiştirme. Yalnız bu revizyon için gerçek
kullanıcı onayı mevcut olduğunda kayda geçir:

```sh
python3 -m orvant_op karsila onayla "<motor-oturumu>" "<revizyon>"
```

Genel “projeyi kur” isteği henüz gösterilmemiş sözleşmeye revizyon onayı değildir.
Aynı sözleşmeye verilmiş onayı tekrar isteme; kayıttaki olayla ilişkilendir.
Kapı reddinde hatayı bildir; onay alanını elle doldurma veya ölçütleri gevşetme.
`--yer-tutucu-kabul` ile eksik cevapları kullanıcı kararı gibi tamamlama.

## S2: ilk plan, sonra yetkili yürütme

Yalnız onaylı S1 ve planın yokluğu doğrulandığında ilk planı çağır. Ürün depo
yolu kullanıcının gerçek hedef klasörü olmalı; mevcut depo temiz `main` dalında
olsun. Kullanıcı değişikliklerini kapıyı geçmek için silme veya taşıma. Depo kapısı
alternatif yol kararı isterse gerçek soruyu taşı; sessizce başka ürün deposu seçme.

```sh
python3 -m orvant_op mimar plan "<motor-oturumu>" --depo "<project-root>"
python3 -m orvant_op mimar durum "<motor-oturumu>"
python3 -m orvant_op mimar yetki "<motor-oturumu>"
```

Gerçek `plan/plan.json` oluştuğunu, hedef/depo/sözleşme bağını ve yetki isteklerini
incele. S1 sözleşmesinin onaylanması tüm izinlerin verildiği anlamına gelmez. Aynı kapsam için
önceden verilmiş izni yeniden isteme; motorun izin olayı da gerçek taahhüde
dayanmalı. Ajanın uygulama tercihini `kullanici` aktörüyle kaydetme.

Mevcut planın ilk yetkili işlerini ve ardından hazır olan bağımlılıklarını motor
seçsin. `surdur` S1'i veya ilk S2 planını oluşturmaz. Eksik/geçersiz kehaneti
işçiden önce hazırlar; üretim başarısızlığında kehanetsiz işçiyi başlatmak için
elle `yurut` çağırma. Örneğin desteklenen varsayılan sınırlarla:

```sh
python3 -m orvant_op surdur "<motor-oturumu>" --en-fazla-tur 5 --tur-basina-kosu 3 --kota-esigi 80 --yurut-zaman-asimi 3600 --kehanet-zaman-asimi 1500
python3 -m orvant_op operator sorular "<motor-oturumu>" --esitle
```

İstenirse önce aynı `surdur` tarifine `--kuru` ekle: aday eylemleri ve soru
önizlemesini gösterir, işi yürütmez veya kabul etmez. `operator sorular --esitle`
güncel planla soru kuyruğunu uzlaştırır; kullanıcı adına cevap vermez.

## Sonuç veya soru, sonra aynı oturumdan devam

`<motor-oturumu>/operator/rapor.md`, canlı plan ve ilgili
`yurutme/makbuzlar/` kayıtlarını incele. Komutun çıkış kodu 0 olması projenin
bittiği anlamına gelmez; `bitis_nedeni`, görev durumları, açık sorular ve kapı
kanıtını birlikte değerlendir. Kabul işçinin `complete` sözüne değil bağımsız
kapı kararına dayanır. `tamamlandi` raporunu görevlerin güncel `kabul` durumuyla
ve bekleyen inceleme/karantina bulunmamasıyla doğrula; ilk görevin kabulünü bütün
projenin tamamlanması diye sunma.

`kullanici_bekleniyor` durumunda kuyruktaki gerçek karar/izin paketini kısa
göster; aynı kök kararı her görev için yeniden sorma. Gerçek cevap geldikten
sonra yalnız `karar` veya `girdi` türünde şu kayıt komutunu kullan:

```sh
python3 -m orvant_op operator cevapla "<motor-oturumu>" "<soru-id>" "<kullanici-cevabi>"
```

Eski/geçersiz soru cevabında güncel soruyu yeniden oku. `operator cevapla` yetki,
karantina, kabul değişikliği veya yükseltme gibi diğer türleri otomatik uygulamaz;
soru açık kalabilir ve hazır komut dönebilir. Hazır komut yeni yetki değildir.
`mimar izin`, `izin-yol`, `karar`, `girdi`, `kabul-degistir` ve karantina kaldırma
komutlarını gerçek kullanıcı kararı ve doğru kapsam/olay bağı olmadan çalıştırma.
Yerel yönergeler bu karar komutlarını ajana yasaklıyorsa hazır komutu kullanıcıya
bırak. Karantina kaldırmayı `operator cevapla` ile yapılmış sayma.

Cevap/izin gerçekten ilgili kayda işlendiğinde veya yürütme sınırı içinde yeni
yetkili ilerleme mümkün olduğunda aynı oturumdan devam et:

```sh
python3 -m orvant_op surdur "<motor-oturumu>" --en-fazla-tur 5 --tur-basina-kosu 3
```

`tur_siniri` sonrasında yeni yetkili ilerleme varsa tekrar sürdür. `kota`,
`zaman_asimi`, `ilerleme_yok` veya `orvant_duzeltmesi_bekleniyor` durumlarını
başarı sayma; kanıtıyla engeli ve sonraki adımı bildir. Aynı engeli kör döngüyle
veya sınırları sessizce yükselterek aşma. Kota okunamıyorsa bunu bilinmiyor olarak
raporla, sıfır sayma. Yeni sohbet veya kesinti sonrasında bu rehberin başlangıç
durumunu yeniden oku; var olan oturumun onaylarını/planını yeniden üretme.

Raporda skill'i kullanan ajanın yaptığı başlangıç, plan çağrısı ve kayıt aktarımını
motorun kendi yürüttüğü seçim/kehanet/kapı adımlarından ayır. Kullanıcı kararının
gerçek kaynağını belirt; olay dosyalarını elle değiştirip eski taahhüt üretme.
Sahte rol/CLI testi, gerçek ajanın skill'i izlemesini veya gerçek Codex/OS
izolasyonunu kanıtlamaz; gerçek yerel koşu kanıtı yoksa bunu açık bırak.
