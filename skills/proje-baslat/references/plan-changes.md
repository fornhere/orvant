# Proje büyürken kayıtları değiştirme

Bu akış yeni nesne, ilişki, görev veya mevcut görevin tanımı değiştiğinde kullanılır.
`extend_model` ekler; `revise_task` mevcut görevin yeni tanımını açıkça kaydeder.
İlk yeni model eylemi eski şema 1 kaydını şema 2'ye geçirir. Kayıt şekilleri
aynıdır; şema 2 yeni eylem geçmişini ve değişiklik öncesi/sonrası bilgisini taşır.

## Mevcut projedeki eski runtime

Yeni model eylemlerini kullanmadan önce yüklü skill'in kaynak betiğiyle yükselt:

```sh
python3 "<skill-dir>/scripts/project.py" upgrade "<project-root>"
```

Bu işlem proje state'ini ve kullanıcı dosyalarını değiştirmez; kontrol
betiklerini yedekleyerek günceller. Aynı sürüm zaten varsa no-op olur. Tek
yazıcıyla çalış. Sonraki komutlarda projeye kopyalanmış betiği kullan.
Eski runtime şema 2'yi okuyamaz; yalnız state yedeğini değil, uygun runtime'ı
da koru. Genel şema dönüştürme veya proje hedefi değiştirme burada desteklenmez.

Yedek `.project/runtime-backups/upgrade-*/` altındadır. `updated` başarıyı,
`restored` başarısız güncellemeden sonra eski betiklerin korunduğunu veya geri
yüklendiğini gösterir. `restore_failed` sonucunda ya da ani süreç kesilmesinde
yazıcıları durdur ve ilgili yedekteki iki betiği birlikte geri yükle. İki dosya
birlikte atomik değiştirilmez. Şema 2'ye geçmiş kayda v0.1 betiği döndürmek
uyumsuzdur; geri dönüş için state ve runtime sürümlerinin uyumlu olması gerekir.

## Yeni kayıtları birlikte ekle

```json
{
  "action": "extend_model", "actor": "agent", "reason": "İncelenecek yeni kaynak geldi.",
  "objects": [{"id": "S4", "type": "source", "label": "Yeni kaynak", "properties": {"path": "kaynaklar/S4.md"}}],
  "relations": [{"from": "S4", "type": "supports", "to": "C1"}],
  "tasks": [{"id": "T-S4", "title": "Yeni kaynağın iddiayı desteklediğini incele", "status": "todo",
             "object_ids": ["S4"], "depends_on": [], "decision_ids": [],
             "acceptance": ["İlgili pasaj ve dar iddia karşılaştırılıp değerlendirme kaydedildi."], "evidence": []}]
}
```

Örnek C1 nesnesinin zaten bulunduğunu varsayar. Listelerden bazıları boş olabilir;
hepsi boş olamaz. Var olan kimlikler güncellenmez, yinelenen bağlar reddedilir.
Yeni görevler todo ve kanıtsız başlar. Nesneler, ilişkiler ve görevler aynı işlemde
birbirini referanslayabilir. Geçersiz bağ/döngü varsa hiçbir kısmı yazılmaz.

Bir `supports` bağı eklemek tek başına iddiayı doğru veya kaynağı kullanılabilir
ilan etmez. İnceleme sonucunu, kullanım kararını ve kanıtını ayrıca kaydet.
Yeni bağı eklemek mevcut görevlerin kanıtını otomatik yenilemez.

## Mevcut görevin tanımını revize et

```json
{
  "action": "revise_task", "actor": "agent", "reason": "İddia artık incelenen yeni kaynağa dayanacak.",
  "task_id": "T-C1",
  "definition": {"title": "İddianın kaynak desteğini incele", "object_ids": ["C1"],
                 "depends_on": ["T-S4"], "decision_ids": [],
                 "acceptance": ["İddia yeni kaynağın ilgili pasajıyla karşılaştırıldı ve sonucu kaydedildi."]}
}
```

Beş tanım alanını mevcut kaydı okuyarak eksiksiz hazırla. Mevcut karar konuları
sessizce kaldırılamaz; değişen konuda güncel kabul edilmiş karara bağlan.
Görev todo olur ve kanıtları temizlenir. Transitif bağlı done/doing/review
işler de yeniden kanıt gerektirir. Değişiklik öncesi ve sonrası görev, şema 2
geçmişinde korunur. Eski tamamlanmayı yeni tanıma taşıma.

Her iki eylemi de normal `apply --event ... --expected-revision N` ile uygula.
Sonra `context` çalıştır, ilgili görevi gerçekten yap, güncel çıktılarla
`submit_evidence` ve `complete_task` akışını tamamla.

## Sınırlar

Görev sırası ile bir iddianın destek koşulunu karıştırma. Birlikte gerekli
incelemeleri `depends_on` açıklar; alternatif kaynakların hangisinin yeterli
olduğu ajan değerlendirmesidir. İlişki silme, genel nesne güncelleme ve otomatik
destek/çelişki hesabı bulunmaz. Tarihsel destek bağı korunuyorsa güncel kullanım
kararını ve incelemeyi görünür tut. Bu kayıtlar değiştirilemez denetim defteri
veya otomatik doğruluk garantisi değildir.
