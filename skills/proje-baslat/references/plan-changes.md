# Modeli değiştirme, geçiş ve kurtarma

Bu akış mevcut alan kayıtları, ontoloji kuralları, görev tanımları veya eski kayıt
şeması değiştiğinde kullanılır. Önce canlı `context --json` oku; bütün eylemlerde
`actor`, `reason` ve son okuduğun `--expected-revision` gerekir.

## Her değişimde aynı döngü

1. Kullanıcının isteğini mevcut kayıtla karşılaştır; nesne ekleme mi, düzeltme mi,
   ilişki değişimi mi, görev kapsamı değişimi mi olduğunu belirle.
2. Desteklenen eylem JSON'unu `.project` dışında hazırla.
3. `preview` ile doğrula; etkilenmesi gereken yol ve görevlerin raporda bulunduğunu
   kontrol et. Eksik etki varsa girdileri/etki yönünü düzelt. Yalnız preview'ın
   başarılı olması modelin amaca uygun olduğu anlamına gelmez.
4. Yetkili kapsam içindeyse aynı eylemi `apply` ile uygula; preview'ın verdiği
   `preview_digest` değerini `--preview-digest` ile geçir, yeniden izin isteme.
5. `context` ve gerekirse `ontology` oku. Eski tamamlanmayı yeni girdiler için
   kullanma; etkilenen görevi gerçekten inceleyip yeni kanıt sun.

```sh
python3 "<root>/.project/scripts/project.py" preview "<root>" --event "<eylem.json>" --expected-revision 4
python3 "<root>/.project/scripts/project.py" apply "<root>" --event "<eylem.json>" --expected-revision 4 --preview-digest "<preview_digest>"
python3 "<root>/.project/scripts/project.py" context "<root>"
```

Preview kalıcı revizyonu artırmaz ve dosya yazmaz. Apply öncesinde başka geçerli
bir eylem olmuşsa revizyon çatışması normaldir; yeni kaydı okuyup etkiyi tekrar
incele. Digest state, eylem ve önceki/sonraki modelin bütün evidence/file
referanslarını gözler; dosyalar değişirse revizyon aynı kalsa bile yeniden preview
gerekir. Dış editörler kilitlenmez; bu dosya sistemi snapshot'ı değildir. State commit olup görünüm yazımı başarısızsa aynı eylemi tekrar uygulama.

## Alan grafiğinde atomik değişiklik

Şema 3'te `mutate_graph` eylemi `operations` listesi alır:

| `op` | Diğer alan | Anlamı |
| --- | --- | --- |
| `add_object` | `object` | Yeni `{id,type,label,properties}` kaydı |
| `replace_object` | `object` | Var olan kimliğin tam yeni kaydı |
| `remove_object` | `object_id` | Var olan nesneyi silme |
| `add_relation` | `relation` | Yeni `{id,from,type,to}` bağı |
| `remove_relation` | `relation_id` | Var olan bağı silme |
| `replace_ontology` | `ontology` | Tam yeni tür/ilişki şeması |

Son grafik doğrulanır; ara adımda eksilen zorunlu bağın yerine aynı transaction'da
yenisini koyabilirsin. Yinelenen kimlik, yanlış tip, çokluk ihlali, boşa düşen
ilişki/görev bağı veya değişimsiz işlem reddedilir. Görevin bağlandığı bir nesneyi
silmek için önce görevi desteklenen `revise_task` eylemiyle uzlaştır; graf işlemi
görev bağlarını gizlice değiştirmez. `immutable: true` türündeki tarihsel nesne
özellikleri/türü ve immutable ilişkiler düzenlenemez/silinemez; yeni sürüm kimliği
kullan. Daha önce silinen kimliği yeni eklemeyle yeniden kullanmak reddedilir. Şema kuralı ve etkilenen nesne değerleri aynı
graf işlemi içinde birlikte revize edilebilir.

[Model örneğindeki](model.md) metni düzeltme:

```json
{
  "action": "mutate_graph",
  "actor": "agent",
  "reason": "İncelenecek örnek metin iki cümle olarak düzeltildi; önceki tek cümle incelemesi yenilenmeli.",
  "operations": [
    {"op": "replace_object", "object": {
      "id": "metin-a", "type": "input", "label": "Kurmaca metin A",
      "properties": {"text": "Bugün aynı komutu denedim. Sonucu kaydettim."}
    }}
  ]
}
```

`metin-a` değişimi, `examines` ilişkisinin reverse etkisiyle `inceleme-a`ya gider.
Girdisi metin olan inceleme görevi ve onun çıktısını tüketen kontrol görevi
etkilenir. Başlanmış/bitmiş işler review olur; kanıtları ve snapshot'ları temizlenir.
Todo işler todo kalır. Sonradan aynı metne dönmek eski kabulü otomatik canlandırmaz.

İlişki silme ve yeniden bağlama etkisi eski ve yeni graf birlikte kullanılarak
hesaplanır; silinen bağın eski tüketicisi unutulmaz. `none` bağ değişimi incelemeyi
bozmaz, çünkü yalnız bağlam bağlantısıdır. Etki kuralını değiştirmek model değişimidir;
önceden ilan edilmiş bağımlılıklar sessizce yok sayılmaz.

## Modeli büyütme

`extend_model` üç listeyi birlikte alır: `objects`, `relations`, `tasks`; en az biri
dolu olmalı. V3 nesne/ilişki biçimleri ve bütün v3 görev alanları gerekir. Nesneler,
ilişkiler ve yeni görevler aynı işlemde birbirine bağlanabilir. Yeni görevler todo,
kanıtsız, snapshot null, generation 0 ve boş review_reasons ile başlar; karar
bağları yalnız güncel accepted kararlara gider. Mevcut kimlikler güncellenmez.

V3'te yeni etkili bağ/nesne mevcut girdileri değiştiriyorsa, mevcut işler de alan
etkisi üzerinden yeniden incelemeye düşebilir. Yeni bir görevde eski bir nesneyi
çıktı ilan etmek üretici bağını değiştirir; yalnız isim listesini büyütmek değildir.

Eski şema 1'de `extend_model` veya `revise_task` şema 2'ye geçer; bu ontoloji
migrasyonu değildir. Şema 2'de hâlâ tür kuralları ve alan etkisi yoktur.

## Görev kapsamını revize etme

Görev tanımının bütün alanlarını güncel kayıttan hazırla. Şema 3 örneği:

```json
{
  "action": "revise_task", "actor": "agent",
  "reason": "Tek cümle kontrolüne açık özne kontrolü eklendi.",
  "task_id": "T-INCELE",
  "definition": {
    "title": "Örnek metnin tek cümle ve açık özne koşulunu incele",
    "object_ids": ["metin-a", "inceleme-a"],
    "input_ids": ["metin-a"], "output_ids": ["inceleme-a"],
    "depends_on": [], "decision_ids": [],
    "acceptance": ["Kayıtlı metin tek cümle ve açık özne koşullarına göre incelenmiş; iki sonuç gerekçesiyle kaydedilmiş."]
  }
}
```

Görev todo olur, kanıt/snapshot temizlenir. Önceki ve sonraki görev geçmişe girer.
Bağlı başlanmış/bitmiş görevler yeniden inceleme ister. Eski karar konuları
sessizce kaldırılamaz; aynı tanım veya cancelled görev revizyonu reddedilir.
Şema 1/2'de definition input/output içermez.

## Runtime yükseltme

Şema geçişinden önce yüklü skill'in kaynak betiğini kullan:

```sh
python3 "<skill-dir>/scripts/project.py" upgrade "<root>"
```

Yükseltme state'i değiştirmez. Projede `core.py`, `project.py`, `ontology.py`, `acceptance.py`
dörtlüsünü kurar; yalnız iki betiği olan eski kurulum desteklenir. Kaynakla aynıysa
`noop`; değiştiyse `updated` ve kalıcı yedek dizini döner.

Yedek `.project/runtime-backups/upgrade-*/` içindedir. `manifest.json` her modülün
önceden bulunup bulunmadığını kaydeder. Hata sonucunda `restored` eski dosyaların
korunduğunu/geri alındığını, `restore_failed` eksik geri alma olduğunu belirtir.
Dört dosya topluca atomik değildir; süreç kesilirse yazıcıları durdur, manifest'e
göre eski dosyaları geri koy ve önceden bulunmayan ontology.py/acceptance.py dosyalarını kaldır. Uyumlu
runtime olmadan yeni şema state'ini eski betikle açmaya çalışma.

CLI işlemleri işletim sistemi advisory lock ile sıraya girer; bu kilit doğrudan/elle dosya
editörünü engellemez. Alt ajanlar bulgularını ana yazıcıya getirmelidir.

## Şema 1/2 → 3 ontoloji migrasyonu

`upgrade` alan nesnelerini yorumlamaz. `migrate_ontology` ayrı bir eylemdir:

| Alan | Gerekli içerik |
| --- | --- |
| `ontology` | Tam yeni object_types/relation_types |
| `objects` | Tam yeni somut nesne listesi |
| `relations` | Kimlikleriyle tam yeni ilişki listesi |
| `bindings` | Mevcut **her görev** için `{task_id,object_ids,input_ids,output_ids}` |

Eski `properties.alanlar` gibi tür tariflerini gerçek örnek verisi sayma. Türleri
ayır, gerçek/sentetik örnekleri kaynaklarından kur, her görev bağını bilinçli seç.
Migrasyon hedefi, görev tanımlarını, kararları ve önceki geçmişi sıfırlamaz. Yeni
bağlar değişen görev anlamını gerektiriyorsa sonrasında `revise_task` uygula.

Bir yeni şema taslağından eylem üretirken agent şu yapıyı hazırlayabilir; dosya
adları örnektir, kullanıcıya JSON doldurtulmaz:

```python
import json
from pathlib import Path

legacy = json.loads(Path("<root>/.project/state.json").read_text())
draft = json.loads(Path("<ontoloji-taslagi.json>").read_text())
bindings = json.loads(Path("<gorev-eslemeleri.json>").read_text())
assert {b["task_id"] for b in bindings} == {t["id"] for t in legacy["tasks"]}
event = {
    "action": "migrate_ontology", "actor": "agent",
    "reason": "Tür tarifleri ile gerçek örnekler ayrıldı; görev girdileri ve çıktıları kaynaklardan eşlendi.",
    "ontology": draft["ontology"], "objects": draft["objects"],
    "relations": draft["relations"], "bindings": bindings,
}
Path("<gecis-eylemi.json>").write_text(json.dumps(event, ensure_ascii=False, indent=2))
```

Önce preview; mevcut görevlerin kaybolmadığını ve eşlemelerin doğru olduğunu
incele. Apply geçerli migrasyondan önce **eski state'in tam byte yedeğini**
`.project/migration-backups/revision-*/state.json` altına yazar ve yolunu döndürür.
Geçmişte eski nesneler, ilişkiler ve görevlerin önceki görüntüsü korunur.
Eski doing/review/done görevler review olur; aktif evidence ve snapshot boşalır,
generation 0 olur. Todo işler todo kalır. Eski kabul yeni ontoloji için otomatik
geçerli sayılmaz; bu veri kaybı değil, inceleme kapsamının değişmesidir.

Geçişten sonra check/context/ontology çalıştır, ilgili işi yeniden kontrol et ve
yeni kanıt sun. `ready` boşsa `repair_actions` mevcut onarım adımlarını gösterebilir.
Snapshot veya kullanıcı kabulünü JSON'a elle doldurmak gerçek incelemenin yerine
geçmez. Desteklenmeyen genel hedef/iptal değişikliği için sessiz state patch yapma.
