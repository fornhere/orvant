# İsteğe bağlı Jev kaynak–iddia incelemesi

Orvant dosya sürümü, ilan edilmiş kaynak bağı ve kabul ölçütü güncelliğini
deterministik olarak izler. Bir kaynağın *anlamca* belirli bir iddiayı destekleyip
desteklemediği ayrı bir incelemedir. Bu komut, tek bir iddia–kaynak çifti için
Jev'in `Choice` yanıtını salt okunur danışman görüşü olarak verir. Orvant kaydını
değiştirmez ve görevi tamamlamaz.

## Ne zaman kullanılır?

Yalnız şema 3 proje, kayıtlı görev kabul ölçütü, görevin çıktısında bulunan
metin iddiası ve o görevin `input_ids` veya `support_groups` dalında ilan
edilmiş bir `file` kaynak nesnesi ile çalışır. Kaynak ve iddia nesneleri
arasında kayıtlı bir ilişki bulunmalıdır; ilişki adı tek başına doğruluk kanıtı
değildir.
Kaynak dosyasının mevcut SHA-256 özeti ve dosyada birebir bulunan kısa alıntı
gerekir. Alıntı eksikse veya dosya sürümü değişmişse sağlayıcı çağrısı yapılmaz.
Önce iddiayı daralt; tek kaynak pasajının doğrudan kurmadığı genel sonucu
doğrulanmış sayma. Birden fazla bağımsız dayanak gereken iddialarda her dayanağı
ayrı incele ve insan değerlendirmesini kaydet.

Kaynak metni ve iddia dış sağlayıcıya gidebilir. Kullanıcı/kapsam bunu açıkça
yetkilendirmediyse `shadow` çalıştırma. Hassas veya özel kaynağı göndermeden
önce pasajı ve bağlamını incele. Basit sır taraması ek korumadır; gizlilik
onayının yerine geçmez.

## Girdi ve komut

`review.json` UTF-8 dosyası örneği:

```json
{
  "project_id": "proje-kimligi",
  "task_id": "iddia-incele",
  "criterion": 0,
  "claim": "Ölçüm sonucu 100.",
  "claim_object_id": "sonuc-iddiasi",
  "claim_property": "text",
  "source_object_id": "olcum-kaynagi",
  "source_property": "path",
  "source_sha256": "<dosyanın 64 karakterlik SHA-256 özeti>",
  "quote": "Ölçüm sonucu 0."
}
```

`criterion` sıfırdan başlayan kabul ölçütü indeksidir. `claim` açıkça incelenen
iddiayı söyler ve çıktı nesnesinin `string` özelliğiyle birebir aynı olmalıdır.
`quote` dosyada aynen bulunmalıdır. İstek dosyasını proje dışında
tutabilirsin; içeriğinde sır saklama.

```sh
python3 "<skill-dir>/scripts/jev_review.py" "<project-root>" --input-json "<review.json>"
```

Varsayılan `off`, yerel proje/kaynak kontrollerini çalıştırır; ağ çağrısı yapmaz.
Jev ile danışman incelemesi için, kaynak gönderimi yetkiliyken:

```sh
python3 "<skill-dir>/scripts/jev_review.py" "<project-root>" \
  --input-json "<review.json>" --mode shadow --send-source
```

`TYPESAFE_API_KEY` değişkenini komuttan önce güvenli ortamda ayarla; anahtarı
komut satırına, istek dosyasına veya proje kaydına yazma. Varsayılan model
`jev-1.13.0`, süre sınırı 3 saniyedir. `--base-url` yalnız HTTPS veya yerel HTTP
adreslerini kabul eder; yönlendirmeler izlenmez. Komut proje durumuna, önbelleğe
veya kanıt dosyasına yazmaz.

## Sonucu okuma

`status: off` yalnız yerel eşleşmelerin geçtiğini belirtir. `status:
advisory_only` yanıtı `supports`, `contradicts` veya `insufficient` görüşü,
olasılıklar ve güven değeri içerir; `approved` ve `state_written` her zaman
`false`tur. `status: degraded` zaman aşımı, geçersiz sağlayıcı yanıtı veya çağrı
sırasında kaynak/proje değişimi nedeniyle görüş verilemediğini belirtir.
Bu durumlarda `verdict: null` kalır. `ok: false` yerel girdi veya proje kapısı
hatasıdır; hata kodu kaynak metnini açığa çıkarmaz.

Kullanım kararı: `contradicts` ve `insufficient` için kaynak ve iddiayı insan
incelemesine aç. `supports` bile görev kapanışı, yayına çıkış veya kullanıcı
kabulü yetkisi vermez. İncelemeyi tekrar üretmek için iddia, alıntı, kaynak
hash'i, görev/ölçüt ve alınan görüşü ayrı bir raporda belgele; Orvant'ın
`submit_evidence` akışına ancak gerçek göreve özgü kontrol ve uygun insan/ajan
incelemesi yapıldıktan sonra gir.

Bu entegrasyonun çevrimdışı testleri API biçimi, sınırlar ve kayda yazmama
davranışını doğrular. Canlı Jev doğruluğu veya Orvant genel fayda artışı
iddiası değildir. Kaynak yaklaşımı: [TypeSafe citation check](https://docs.typesafe.ai/cookbooks/citation_check)
ve [HTTP API](https://docs.typesafe.ai/api).
