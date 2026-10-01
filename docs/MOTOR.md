# Orvant motoru

[Orvant](../README.tr.md) · [Kullanım](KULLANIM.md) · [Ajanın motor akışı](../skills/orvant/references/engine.md)

Motor bir yazılım hedefini onaylı sözleşme ve görev grafiği üzerinden yürütür. Python **3.11+**, Git ve gerçek koşuda kimliği doğrulanmış Codex CLI gerekir. Beta sürümü **0.1.0b1**; motorun geliştirme ortamı Linux'tur.

## Akış

| Aşama | Sorumluluk |
| --- | --- |
| S1 — `karsila` | Hedefi, kaynakları, kararları ve kabul koşullarını netleştirir; gerçek kullanıcı cevaplarıyla sözleşme revizyonunun onayını kaydeder. |
| S2 — `mimar` | Onaylı sözleşmeden görev grafiği ve yetki istekleri çıkarır; bağımsız kabul kehanetini hazırlar. |
| S3 — `yurut` | Görevleri Codex ile ayrı Git worktree'lerinde yürütür; kabul kapısını uygular ve kabul edilen işi birleştirir. |
| S4 — teşhis | Başarısızlığın nedenini sınıflar; yeniden deneme, plan değişikliği, girdi/izin bekleme veya kullanıcıya taşıma önerir. |
| S6 — `operator` / `surdur` | Mevcut planı tur, süre ve gözlenen kota sınırları içinde sürdürür; gerçek karar sorularını kuyrukta toplar. |

İlk S1 ve S2 adımlarını skill'i kullanan ajan başlatır. `surdur` mevcut plan üzerinde çalışır. Aynı projede var olan oturumun planını ve onaylarını yeniden üretmeden devam et.

## Bağımsız kabul

İşçinin `complete` bildirimi kabul değildir. Kapı; ilan edilmiş kabul komutlarını, kehaneti, yazma kapsamını ve ilgili makbuzları denetler. Kehanetin kontrolleri sözleşmenin gereksinimlerine veya kabul koşullarına bağlanır. Bilinen doğru referansla pozitif kontrol ve uygulanabilir kusurlu çıktı denetimi, kehanetin çalışma koşullarında anlamlı olduğunu sınar.

Kabul bir görev ve belirli dayanaklar içindir. İlk görevin kabulü bütün projenin tamamlandığı anlamına gelmez. Güncel görev durumlarını, bekleyen inceleme/karantinayı ve açık soruları birlikte oku. Eski planda kehanet bulunmaması uyarılı uyumluluk yoludur; yeni işlerde kehaneti yürütmeden önce hazırla.

## Oturum ve ürün deposu

Motor oturumu ile ürün Git deposu ayrı dizinler olmalı; biri diğerini içermemeli ve ikisi de Orvant kaynak ağacının dışında bulunmalı. İlk planın ürün deposu temiz `main` dalında olmalı. Kullanıcı değişikliklerini kapıyı geçmek için silme.

Oturumdaki `karsilama/`, `plan/`, `yurutme/` ve `operator/` kayıtları canlı durumu taşır. `operator/rapor.md` ve `yurutme/makbuzlar/` sonuç incelemesinde kullanılır. `.project` ontoloji kaydı bunlardan ayrı bir kayıt sistemidir.

## Devam etme

Planı ve mevcut yetkileri okuduktan sonra, kurulu motorun yorumlayıcısıyla:

```sh
orvant mimar durum "<motor-oturumu>"
orvant mimar yetki "<motor-oturumu>"
orvant surdur "<motor-oturumu>" --kuru
orvant surdur "<motor-oturumu>" --en-fazla-tur 5 --tur-basina-kosu 3
orvant operator sorular "<motor-oturumu>"
```

`--kuru` eylemleri önizler; işi yürütmez veya kabul etmez. Gerçek `surdur` model çağırabilir ve ürün deposunu değiştirebilir. İzin ve kararlar gerçek kullanıcı taahhüdüne dayanmalı; genel sözleşme onayı bütün izinleri vermez.

`kullanici_bekleniyor`, `kota`, `zaman_asimi`, `ilerleme_yok` veya `orvant_duzeltmesi_bekleniyor` bitişlerini başarı sayma. Yeni yetkili ilerleme mümkün olduğunda aynı oturumdan devam et; aynı engeli sınırları sessizce artırarak döngüye sokma.

## Ayarlar ve sınırlar

Varsayılan model bütün rollerde `gpt-6.1-sol`dur. `python3 -m orvant_op.ayarlar` etkin ayarları gösterir. Model ayarları ortam değişkenleri, proje `orvant.toml` dosyası ve kullanıcı yapılandırmasından okunabilir.

Gerçek yürütme Codex goal desteği ve `codex sandbox` ister. Kabul komutları yerel kabuk komutlarıdır; kehanet sandbox'ı ve yazma kapsamı kontrolleri her komutu kapsayan genel izolasyon iddiası değildir. Plan ve izinler incelenmelidir.

Beta kapsamı küçük yazılım işlerine yöneliktir. Kehanetin sözleşmeye bağlanması, bütün gereksinimlerin yeterli veya her model yorumunun doğru olduğunu ispatlamaz. Gerçek kullanımda fayda ve genel güvenilirlik ayrıca değerlendirilmelidir.
