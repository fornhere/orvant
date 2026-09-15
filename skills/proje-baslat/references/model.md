# v0.3 model ve komut sözleşmesi

Tek kaynak `.project/state.json`; `.project/CONTEXT.md` ve `.project/ONTOLOJİ.md`
türetilen görünümlerdir. Python 3.10+, standart kütüphane, Linux. Yeni proje şema 3
kullanır; şema 1/2 kayıtlar okunur ve açık migrasyonla geçer. Genel JSON patch yoktur.

## Başlangıç spec'i

Alanlar kapalıdır: bilinmeyen alanlar reddedilir. Kimlikleri kısa, benzersiz ve
ASCII tut. Aşağıdaki sentetik örnek doğrulanabilir tam spec'tir; içerdiği kararları
ve modeli gerçek kullanıcı tercihi diye kopyalama.

```json
{
  "schema_version": 3,
  "revision": 0,
  "project": {
    "id": "ornek-inceleme",
    "name": "Sentetik çıktı incelemesi",
    "goal": "Bir örnek metnin incelemesini ve dayanağını izlemek",
    "audience": "Örneği deneyen kullanıcı",
    "scope": ["Yerel metin ve inceleme"],
    "out_of_scope": ["Gerçek model performansı iddiası"],
    "constraints": ["Örnek veri kurmacadır"],
    "open_questions": []
  },
  "ontology": {
    "object_types": [
      {"id": "input", "label": "İncelenen metin", "properties": {
        "text": {"type": "string", "required": true, "enum": null}
      }},
      {"id": "assessment", "label": "İnceleme", "properties": {
        "verdict": {"type": "string", "required": true, "enum": ["pending", "pass", "fail"]}
      }}
    ],
    "relation_types": [
      {"id": "examines", "label": "İnceler", "from_type": "assessment", "to_type": "input",
       "from_min": 1, "from_max": 1, "to_min": 0, "to_max": null, "impact": "reverse"}
    ]
  },
  "objects": [
    {"id": "metin-a", "type": "input", "label": "Kurmaca metin A", "properties": {"text": "Bugün aynı komutu iki kez denedim."}},
    {"id": "inceleme-a", "type": "assessment", "label": "Metin A incelemesi", "properties": {"verdict": "pending"}}
  ],
  "relations": [
    {"id": "bag-inceleme-a", "from": "inceleme-a", "type": "examines", "to": "metin-a"}
  ],
  "tasks": [
    {"id": "T-INCELE", "title": "Örnek metnin tek cümle oluşunu incele", "status": "todo",
     "object_ids": ["metin-a", "inceleme-a"], "input_ids": ["metin-a"], "output_ids": ["inceleme-a"],
     "depends_on": [], "decision_ids": [], "acceptance": ["Metin ve tek cümle ölçütü incelenmiş; sonuç gerekçesiyle kaydedilmiş."],
     "evidence": [], "input_snapshot": null, "generation": 0, "review_reasons": []},
    {"id": "T-KONTROL", "title": "İnceleme gerekçesini kontrol et", "status": "todo",
     "object_ids": ["inceleme-a"], "input_ids": ["inceleme-a"], "output_ids": [],
     "depends_on": [], "decision_ids": [], "acceptance": ["Gerekçenin kayıtlı metinle uyumu gerçekten incelenmiş."],
     "evidence": [], "input_snapshot": null, "generation": 0, "review_reasons": []}
  ],
  "decisions": [],
  "history": []
}
```

`T-KONTROL`, inceleme nesnesinin üreticisi olan `T-INCELE` bitmeden başlayamaz.
Bu bağ `depends_on` boş olsa da hesaplanır ve context'te açıklanır. `pending`
gerçek bir inceleme sonucu değildir; çıktı nesnesinin hazırlanacak kaydıdır.

### Ontoloji alanları

Nesne türü: `{id,label,properties}`; isteğe bağlı `immutable` boolean alanı
bulunabilir. Özellik tanımı tam olarak `{type,required,enum}`.
Türler: `string`, `integer`, `number`, `boolean`, `string_list`, `file`, `json`.
`enum` liste veya null; `required` boolean. Bilinmeyen özellikler reddedilir.
`integer`/`number`, boolean kabul etmez; sonsuz/NaN kabul edilmez. `string` boş
olabilir; boş olmama, sayısal aralık, kalıtım gibi ek kurallar bu sözleşmede yoktur.
`json` serbest JSON değeridir; alan kuralı gerektiren veriyi buraya gömmemek gerekir.

`file` proje köküne göre normal dosya yoludur; içerik hash'i bağlı görevin girdi
snapshot'ına alınır. Mutlak yol, `..`, symlink, `.project` yolu kabul edilmez.
Şema doğrulaması yol biçimini; snapshot ve kanıt kontrolü gerçek dosyayı denetler.
Sıradan `string` içinde dosya yolu yazmak dosyayı izletmez.

İlişki türü `{id,label,from_type,to_type,from_min,from_max,to_min,to_max,impact}`;
isteğe bağlı `immutable` boolean alanı bulunabilir.
İlişki örneği `{id,from,type,to}`. Uç türleri kayıtlı tek birer nesne türüdür.
`from_min/max` her kaynak nesnenin giden bağ sayısı; `to_min/max` her hedef
nesnenin gelen bağ sayısıdır. Min sıfır veya pozitif tamsayı; max tamsayı veya
sınırsız için null. Bağsız nesneler de alt sınır kontrolünden geçer. Aynı üçlü iki
ayrı ilişki kimliğiyle çoğaltılamaz. Alan grafiği döngüsü tek başına hata değildir. `immutable: true` türündeki
mevcut nesnenin özellik/tür değiştirmesi veya silinmesi reddedilir; görünüm
etiketi düzeltilebilir. Immutable ilişki değiştirilemez/silinemez. Tarihsel kayıt
için yeni kimlik kullan. Silinen nesne/ilişki kimlikleri işlem geçmişinden izlenir
ve yeniden eklenemez; bu yerel geçmişi değiştirilemez yapmaz.

Etki yönü: `forward` kaynak değişince hedef; `reverse` hedef değişince kaynak;
`both` iki yön; `none` yalnız bağlam bağlantısı. İlişkinin okunma yönü ile
etki yönü ayrı kavramlardır. Ayrıntılı modelleme: [ontoloji rehberi](ontology.md).

### Görevler ve güncellik

- `object_ids`: görevin ilgili olduğu kayıtlar.
- `input_ids`: görevin incelediği dayanaklar; `object_ids` alt kümesi.
- `output_ids`: görevin ürettiği kayıtlar; `object_ids` alt kümesi, girdilerle kesişmez.
- `depends_on`: alan üretimi dışında açık iş önkoşulları.
- `input_snapshot`: başlangıçta null; kanıt sunumunda motorun ürettiği `{sha256,manifest}`.
- `generation`: başlangıçta 0; her başarılı tamamlamada artar.
- `review_reasons`: başlangıçta boş; değişiklikten doğan yeniden inceleme açıklamaları.
- İsteğe bağlı `run_snapshot`: start sırasında motorun yakaladığı girdi manifest'i;
  submit sırasında koşunun başladığı girdilerle aynı olup olmadığı kontrol edilir.
- İsteğe bağlı `output_snapshot`: submit sırasında motorun yakaladığı çıktı
  bütünlüğü; complete ve sonraki context denetimlerinde kontrol edilir.
- İsteğe bağlı `support_groups`, `acceptance_rules`: aşağıdaki açık kabul sözleşmeleri.
- İsteğe bağlı `support_snapshot`: motorun ürettiği incelenmiş destek dalları ve
  manifest'leri; kullanıcı/ajan elle kabul snapshot'ı hazırlamaz.
- İsteğe bağlı `input_fields`: `{nesne_kimliği: [özellik_adları]}`; girdi kapanımındaki
  bir nesnenin yalnız belirtilen özelliklerini girdi snapshot'ına seçer. Olmayan
  nesne/özellik reddedilir; verilmezse tüm özellikler kapsanır. Daraltmayı gerçek
  görev dayanaklarına göre yap; gizli bağımlılığı saklamak için kullanma.

Bir nesnenin en fazla bir üretici görevi vardır. Bir görevin girdi kapanımı kendi
çıktısını içeremez. Girdi kapanımı, ilan edilmiş etki yönlerini ters gezerek
bulunur. Etkin önkoşullar, açık `depends_on` ile bu girdileri üreten görevlerin
birleşimidir; birleşik görev grafiğinde döngü reddedilir. Bağlamdaki hesaplanan
önkoşulları görmek için sırf görünür olsun diye gereksiz `depends_on` kopyalama.

Snapshot; bağlı nesneleri, ilgili tür tanımlarını ve etkili ilişkileri, `file`
özelliklerinin içerik hash'lerini ve etkin önkoşul görevlerin tamamlanma kuşaklarını
kapsar. Global revizyonu kapsamaz. `manifest` hangi girdilerin yakalandığını
incelemek içindir; dosyaların bütün eski byte'larını arşivlediği anlamına gelmez.
`none` bağlantıları normal girdi snapshot'ına girmez. Görünüm etiketleri hesaplama
verisi sayılmaz; nesne ve tür label'ları bağımlılık hash'inden çıkarılır. Açık
destek tanıklığı ayrı incelenir. Complete/context güncel girdilerle yeniden
karşılaştırır. Üreticinin tekrar tamamlanması, aynı byte'lar üretilse de eski
tüketici kanıtını yeniden geçerli yapmaz.

Kayıtlı `status` ile hesaplanan `effective_status` farklı olabilir. V3 context
ayrıca `freshness` (current/stale/unverified) ve `readiness` (ready/blocked) verir;
iş sırası ile önceki kabulün güncelliği ayrı okunur. Dosya veya
snapshot eskidiyse done görev güncel başarı sayılmaz. Üreticinin etkin durumundan
nesne durumu hesaplanır: `input`, `pending`, `current`, `needs_review`.

## Komutlar

```sh
python3 "<skill-dir>/scripts/project.py" init "<root>" --spec "<spec.json>"
python3 "<skill-dir>/scripts/project.py" upgrade "<root>"
python3 "<root>/.project/scripts/project.py" check "<root>"
python3 "<root>/.project/scripts/project.py" context "<root>" --json
python3 "<root>/.project/scripts/project.py" ontology "<root>" --json
python3 "<root>/.project/scripts/project.py" preview "<root>" --event "<event.json>" --expected-revision 0
python3 "<root>/.project/scripts/project.py" apply "<root>" --event "<event.json>" --expected-revision 0 --preview-digest "<preview_digest>"
```

`init` mevcut tam kaydı sıfırlamaz; yeni spec'i üstüne uygulamaz. Yarım/bozuk kayıt
çakışmadır. `check` yapı ve güncellik denetimidir; içerik doğruluğu hükmü değildir.
Normal önkoşul bekleyen todo görev tek başına kontrol hatası değildir.
`context` canlı görünüm ve `repair_actions` üretir. `ontology` canlı tür/örnek/görev
görünümünü verir; JSON seçeneği programatik okumadır, ayrı sorgu dili yoktur.

`preview` apply ile aynı motoru kopyada çalıştırır; sonraki revizyonu, değişimi,
etkilenen nesne/yolları, değişen görevleri ve sonraki bağlamı verir. Projeye yazmaz ve `preview_digest` verir. Apply bu değeri `--preview-digest`
olarak almalı; aynı revizyonda state, eylem veya referans dosya değişimini reddeder.
CLI seçeneği geriye uyum için isteğe bağlı olsa da bu skill'in preview/apply
akışında kullanılır. Digest, önceki/sonraki kayıttaki bütün evidence ve ilan
edilmiş file referanslarını tutucu biçimde gözler; görev snapshot'ından daha
kapsamlı olabilir. Dış editörleri kilitlemez, bütün dosya sistemi için atomik
snapshot garantisi vermez. `apply` başarılı eylemde revizyonu bir artırır. Ret veya revizyon çatışması state'i
değiştirmez. State commit olup görünüm yazımı bozulursa `state_committed_view_failed`
döner; eylemi tekrar etme, güncel context oku.

## Görev ve karar eylemleri

Her eylem `action`, `actor`, `reason` taşır. Actor gerçek uygulayıcı rolüdür,
kimlik doğrulaması değildir. Örnek:

```json
{"action":"start_task","actor":"agent","reason":"İlgili girdiler hazır; çalışma başlangıcını kaydediyorum.","task_id":"T-INCELE"}
```

Start, mevcut girdi manifest'ini yakalar. Çalışma sırasında girdiler değiştiyse
eski koşunun sonucu yeni girdilere bağlanamaz; submit reddedilir. Görevi güncel
girdilerle yeniden başlat ve gerçek kontrolü tekrarla.

Örnekte incelemenin sonucunu gerçekten belirledikten sonra `mutate_graph` ile
`inceleme-a.properties.verdict` değerini `pass`/`fail` olarak kaydet; `pending`
bırakıp sonucu tamamlanmış sayma. Gerekçeyi gerçek `İNCELEME.md` dosyasına yaz.
Sonra kanıt sun:

```json
{"action":"submit_evidence","actor":"agent","reason":"Metni ve sonucu karşılaştırdım.","task_id":"T-INCELE","items":[{"path":"İNCELEME.md","criterion":0,"note":"Kayıtlı metnin tek cümle olduğu okunarak kontrol edildi; gerekçe dosyada.","reviewer":"agent"}]}
```

`criterion` sıfır tabanlı kabul ölçütü indeksidir. Submit eski evidence listesini
yenisiyle değiştirir; dosya hash'leri, girdi ve çıktı snapshot'ları motor tarafından üretilir.
Raporla birlikte gerçekten kontrol ettiği zorunlu kaynak/çıktı dosyalarını aynı
ölçüte ekle. Alternatif destek kaynakları aşağıdaki dal manifest'lerinde izlenir;
hepsini zorunlu evidence'a eklemek `any` yeterliliğini etkisizleştirir. Dosya kanıtı
veya snapshot tek başına içeriğin doğru olduğunu ispatlamaz.

```json
{"action":"complete_task","actor":"agent","reason":"Ölçütler güncel girdilerle karşılandı.","task_id":"T-INCELE"}
```

Her ölçütte güncel kanıt, güncel girdi/çıktı snapshot'ı ve etkin olarak bitmiş
önkoşullar gerekir. Tanımlı kabul kuralları ve destek grupları da karşılanmalıdır.
Akış todo → doing → review → done. Diğer eylemler:

| Eylem | Ek alanlar ve davranış |
| --- | --- |
| `reopen_task` | `task_id`; todo yapar, kanıtı temizler; bağlı işler yeniden incelenir |
| `propose_decision` | `decision: {id,topic,statement,rationale,source,supersedes}` |
| `accept_decision` / `reject_decision` | `decision_id` |
| `reconcile_task` | `task_id,decision_ids`; güncel accepted kararlara bağlar, todo yapar, eski kanıtı temizler |
| `extend_model` | `objects,relations,tasks`; en az bir liste dolu; yeni görevler todo/boş kanıt/null snapshot/0 generation/boş review_reasons |
| `revise_task` | `task_id,definition`; tanımın bütün alanları gerekir |
| `migrate_ontology` | `ontology,objects,relations,bindings`; şema 1/2 → 3 |
| `mutate_graph` | `operations`; atomik alan modeli değişimi |

Şema 3 görev tanımı: `{title,object_ids,input_ids,output_ids,depends_on,decision_ids,acceptance}`.
İsteğe bağlı `input_fields`, `support_groups`, `acceptance_rules` tanım revizyonuna
eklenebilir; değiştirirken mevcut anlamı ve snapshot etkisini incele. Şema 1/2
tanımında input/output yoktur. Revizyon aynı tanımı veya cancelled görevi
reddeder; eski karar konuları sessizce kaldırılamaz. Task todo olur, downstream
başlanmış/bitmiş işler review olur. Ayrıntı: [değişiklikler](plan-changes.md).

Karar kaydı `{id,topic,statement,status,rationale,source,supersedes,accepted_by}`.
Başlangıçta proposed veya accepted; accepted için kaynak ve kabul eden gerekir.
Bir konuda tek etkin accepted karar bulunur. Yeni öneri mevcut kararı etkilemez;
kabul edildiğinde doğru `supersedes` selefini göstermeli. Done görevi yeni karara
bağlamadan önce reopen gerekir. Eski karara dayanan işler etkin başarı sayılmaz.

## İsteğe bağlı destek grupları ve kabul kuralları

Şema 3 görevinde `support_groups` ve `acceptance_rules` alanları isteğe bağlıdır;
yoksa boş liste kabul edilir. Bunlar normal `input_ids` bağımlılığının yerine
örtük çıkarım yapmaz; açık ek kabul koşullarıdır.

Bir destek grubu ve iki alternatif dal örneği:

```json
{
  "support_groups": [{
    "id": "iddia-destegi", "mode": "any",
    "branches": [
      {"id": "kaynak-a", "input_ids": ["kaynak-a"], "relation_ids": ["a-iddiayi-destekler"]},
      {"id": "kaynak-b", "input_ids": ["kaynak-b"], "relation_ids": ["b-iddiayi-destekler"]}
    ]
  }]
}
```

Alternatif dayanakların witness ilişkisini `impact: none` tanımla; aynı kaynakları
zorunlu `input_ids` içine koyma, yoksa her ikisi zorunlu hale gelir. Gerçek hesap
girdilerini `input_ids` içinde tut. Destek tanıklıkları `none` olsa da grup
snapshot'ında ayrıca izlenir. Alternatif kaynak nesnesindeki `file` özelliklerinin
gerçek içerik hash'leri dal manifest'ine alınır; bu dosyaları tekrar zorunlu
evidence listesine koymak gerekmez. Her durumda gerekli rapor/çıktılar evidence'da,
alternatif kaynak sürümleri `support_snapshot` altında kalır.

Bu parça task'a eklenir; tek başına eylem değildir. Grup ve dal kimlikleri `/`
içermez. Dalın en az bir girdi veya ilişki kimliği olmalıdır. `any` en az bir
incelenmiş güncel dalı, `all` bütün tanımlı dalları gerektirir. Nesne/bağ kaybı
incelenebilir bir destek sorunu olarak kalır. Kaynak doğruluğu değerlendirmesi
motor tarafından yapılmaz.

Gruplu görevde gerçek incelemeden sonra submit eylemine
`"reviewed_supports": ["iddia-destegi/kaynak-a"]` eklenir. Bu, seçilen dalın
incelendiğine dair rol beyanıdır; kimlik doğrulaması veya otomatik kaynak değerlendirmesi
değildir. İkinci dal incelenmediyse otomatik yedek kabul sayılmaz.

Bir selector tam olarak şu alanlara sahiptir:

```json
{"roots":["degerlendirme-a"],"path":[{"relation_type":"applies","direction":"out"}],"property":null}
```

`roots` kimlik listesi; `path` sıralı ilişki adımları; direction `out`/`in`;
property null ise nesne kimlikleri, metinse her seçilen nesnenin o alanındaki
değerler alınır. Liste değerli özellik tek JSON değeri olarak kalır; düzleştirilmez.

Kural biçimleri:

| `op` | `id`, `label`, `op` dışında alanlar |
| --- | --- |
| `equal_sets` | `left`, `right` selector'ları; kanonik JSON değer kümeleri eşit |
| `disjoint` | `left`, `right`; değer kümeleri kesişmiyor |
| `count` | `selector`, `min`, `max`; seçilen nesne/değer sayısı, max null olabilir |
| `all` / `any` | Boş olmayan `rules` alt kural listesi |

Kimlikler kural ağacında benzersiz olmalı. Count, aynı property değerine sahip
iki ayrı nesneyi iki sayar. Equal_sets/disjoint küme semantiği kullanır; tekrarlar
önemsizdir. Boş kümelerin eşitliği ve ayrıklığı geçer; doluluk için count ekle.
Kuralın geçtiği ilişki tanıklıkları, seçilen kimlikler/özellikler ve boş seçimler
çıktı kabul gözlemine alınır. Aynı kural sonucu hâlâ doğru olsa bile kabulün
dayandığı kayıt veya yol değişimi yeniden inceleme gerektirebilir. Kuralın
kullandığı dış veri ayrıca işin gerçek girdisiyse `input_ids` ile bağla; selector
görev üreticisi önkoşulu tanımlamanın yerine geçmez. Kök/özellik kaybı açıklamalı
kural başarısızlığıdır. Geçersiz operatör veya
bildirilmemiş ilişki türü yapı hatasıdır. İç içe kural derinliği sınırlıdır.

## Sınırlar ve kurtarma

Genel proje hedefini değiştirme veya görev iptal etme eylemi yoktur. Alan işlem
kümesi kapalıdır; keyfi Python/SQL/eval, OWL çıkarımı, insan yetki doğrulaması veya
otomatik içerik doğruluğu hesabı yoktur. Şema 3 eylemleri değişen görevlerin önceki/sonraki görüntülerini geçmişte korur;
kabul snapshot'larının tarihi oradan incelenebilir. `history` revizyon ve işlem geçmişidir;
değiştirilemez veya tüm durumu geri oynatabilen bir olay defteri değildir.

CLI init/apply/upgrade işlemleri aynı kullanıcı altında proje köküne göre Linux
advisory kilidi alır. Başka betiğin/insanın doğrudan yazmasını engellemez. State
tek dosyada atomik değiştirilir; runtime modülleri topluca atomik değildir.
[Yükseltme ve yedekten kurtarma](plan-changes.md) talimatlarını kullan.
