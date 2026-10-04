# Orvant hızlı başlangıç

[Orvant](../README.tr.md) · [İngilizce hızlı başlangıç](QUICKSTART.md)

## 1. Skill'i 60 saniyede dene

Bu yol Python 3.10+ ister; kodlama ajanı CLI’ı, API anahtarı veya kurulu Orvant paketi
istemez.

```sh
git clone https://github.com/fornhere/orvant.git
cd orvant
demo="$(mktemp -d)/demo"
python3 skills/orvant/scripts/project.py init "$demo" --spec examples/demo-spec.json
python3 "$demo/.project/scripts/project.py" check "$demo"
python3 "$demo/.project/scripts/project.py" context "$demo"
python3 "$demo/.project/scripts/project.py" ontology "$demo"
```

`init`, proje runtime'ını `.project/` içine kopyalar ve **hedef dizine
`AGENTS.md` yazar**. Değerlendirirken yukarıdaki gibi geçici bir dizin kullan.
`check` kaydı doğrular; `context` hazır ve engelli işleri gösterir; `ontology`
canlı nesne haritasını üretir. Bu spec için kaydedilmiş gerçek çıktılar
[`sample-output/`](sample-output/KOMUTLAR.md) dizinindedir.

## 2. Motoru kur

Motor Linux, Git, Python 3.11+ ve kurulu, oturum açılmış **Codex CLI veya Claude Code (biri yeterli)** gerektirir. İkisi eşit desteklenir; hiçbiri varsayılan veya deneysel değildir. Motor `codex-cli 0.155.1` ile geliştirilmiştir; diğer sürümler doğrulanmadı ve Claude Code için doğrulanmış sürüm iddiası yoktur. macOS veya Windows motor davranışı doğrulanmadı.

İşçi seçimi: `ORVANT_YURUTUCU=codex|claude`, `orvant.toml` içindeki `[yurutucu] tur = "codex"` veya `tur = "claude"` ayarından önceliklidir. İkisi de belirtilmezse PATH'te veya ayarlı yolunda kurulu tek ikili otomatik algılanır. İkisi de kuruluysa açık seçim zorunludur; aksi halde Orvant `iki yürütücü bulundu` hatası verir.

Linux'ta kehanetin OS yalıtımı, seçilen işçiden bağımsız olarak bubblewrap (`bwrap`) veya Codex sandbox (`codex sandbox`) gerektirir. `orvant.toml` içindeki `[kehanet] yalitim = "auto"` önce doğrulanmış `bwrap`, sonra Codex sandbox dener; `yalitim = "bwrap"` veya `yalitim = "codex"` ilgili arka ucu zorlar. Kullanılabilir yalıtım arka ucu yoksa Orvant kehaneti çalıştırmaz: kapı kapanır, yalıtımsız koşmaz.

```sh
git clone https://github.com/fornhere/orvant.git
cd orvant
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
orvant --help
```

Wheel motoru kurar, skill dosyalarını kurmaz. Seçilen CLI’da oturum açılmadan ve ürün
deposu hazır olmadan aşağıdaki model çağıran komutları çalıştırma.

## 3. Motor oturumu başlat

Köşeli parantezli bütün yer tutucuları değiştir. `<motor-oturumu>` ile
`<urun-deposu>` Orvant kaynak ağacının ve birbirlerinin dışında olsun. İlk plan
için `<urun-deposu>` temiz ve `main` dalında bir Git deposu olmalıdır.

| Komut | Model çağırır mı? | Kullanıcı kararı mı? |
| --- | --- | --- |
| `orvant karsila baslat "<motor-oturumu>" --hedef "<hedef>"` | Hayır | Kullanıcının hedefini aktarır |
| `orvant karsila ilerle "<motor-oturumu>"` | Evet, otomatik karşılama adımı yürüttüğünde | Hayır; asla cevap uydurma |
| `orvant karsila durum "<motor-oturumu>"` | Hayır | Hayır; salt okunur durum |
| `orvant karsila sorular "<motor-oturumu>"` | Hayır | Hayır; salt okunur sorular |
| `orvant karsila cevapla "<motor-oturumu>" "<soru-kimligi>" "<cevap>"` | Hayır | **Evet; yalnız gerçek kullanıcının cevabını kaydet** |
| `orvant karsila sozlesme "<motor-oturumu>"` | Hayır | Hayır; önerilen sözleşmeyi incele |
| `orvant karsila onayla "<motor-oturumu>" "<revizyon>"` | Hayır | **Evet; gösterilen revizyonu yalnız kullanıcı onaylar** |
| `orvant mimar plan "<motor-oturumu>" --depo "<urun-deposu>"` | Evet | Hayır; onaylı sözleşmeden plan çıkarır |
| `orvant mimar durum "<motor-oturumu>"` | Hayır | Hayır; salt okunur plan durumu |
| `orvant mimar yetki "<motor-oturumu>"` | Hayır | Hayır; salt okunur izin istekleri |
| `orvant mimar izin "<motor-oturumu>" "<istek-kimligi>" "<cevap>" --karar verildi` | Hayır | **Evet; izni yalnız kullanıcı verir veya reddeder** |

Kullanıcı izni reddederse `verildi` yerine `--karar reddedildi` kullan.
Sözleşmenin onayı bütün izinleri vermez. `ilerle` sonucunu okumadan komutu
tekrarlama; her soruyu kullanıcıya taşı ve oturumun bastığı gerçek kimlik ile
revizyonu kullan.

## 4. Mevcut planı sürdür

Plan oluştuktan sonra aynı oturumu sürdür; `karsila baslat` veya `mimar plan`
komutunu yeniden çalıştırma.

| Komut | Model çağırır mı? | Kullanıcı kararı mı? |
| --- | --- | --- |
| `orvant surdur "<motor-oturumu>" --kuru` | Hayır | Hayır; eylemleri önizler, işi kabul etmez |
| `orvant surdur "<motor-oturumu>" --en-fazla-tur 5 --tur-basina-kosu 3` | Evet, yetkili iş ilerleyebildiğinde | Hayır; ürün deposunu değiştirebilir |
| `orvant operator sorular "<motor-oturumu>"` | Hayır | Hayır; kuyruktaki soruları salt okur |
| `orvant operator cevapla "<motor-oturumu>" "<soru-kimligi>" "<cevap>"` | Hayır | **Evet; yalnız gerçek kullanıcının cevabını kaydet** |
| `orvant yurut durum "<motor-oturumu>"` | Hayır | Hayır; salt okunur yürütme durumu |

Devam raporunu, görevlerin kapı sonuçlarını, makbuzları ve açık soruları
birlikte oku. Yalnızca sıfır çıkış kodu projenin tamamlandığı anlamına gelmez.

## 5. Neyin başarı olmadığını bil

Şu bitiş nedenlerinden hiçbirini başarı sayma: `kullanici_bekleniyor`, `kota`,
`zaman_asimi`, `ilerleme_yok` veya `orvant_duzeltmesi_bekleniyor`.

İşçinin `complete` bildirimi kabul değildir. Tek görevin kabulü projenin
tamamlandığı anlamına gelmez; süreç çıkış kodunun sıfır olması da tamamlanma
değildir. Bağımsız kapıyı, güncel görev durumlarını, bekleyen inceleme veya
karantinayı, makbuzları ve çözülmemiş soruları denetle. Aynı oturumu ancak gerçek
bir cevap, izin veya başka bir yetkili ilerleme mümkün olduğunda sürdür.

## 6. Sonraki adımlar

Eksiksiz komut başvurusu için [Kullanım](KULLANIM.md), kabul davranışı, oturum
kayıtları, sınırlar ve bitiş nedenleri için [Motor](MOTOR.md) belgesine bak.
Deneysel `orvant proje` komutu da vardır; bu hızlı başlangıç akışının dışındadır.
