# v0.2 modeli ve komutları

Python 3.10+; bütün komutlarda hedef kök açık verilir. `init` ve `apply` JSON
dosyasını okur; içinde komut çalıştırmaz. Türkçe metinler desteklenir. Sürümlü
tek kaynak `.project/state.json`, insan görünümü `.project/CONTEXT.md`.

## Başlangıç spec'i

Şu alanların hepsi bulunur. Kimlikler kısa, benzersiz ve ASCII olsun.

```json
{
  "schema_version": 1,
  "revision": 0,
  "project": {
    "id": "ornek", "name": "Örnek proje", "goal": "Üç sentetik aracı karşılaştırmak",
    "audience": "AI kullanıcıları", "scope": ["Yerel karşılaştırma metni"],
    "out_of_scope": ["Canlı fiyatlar"], "constraints": ["Kurmaca veri açıkça etiketlenecek"],
    "open_questions": ["Yayın tarihi henüz belli değil"]
  },
  "objects": [
    {"id": "veri", "type": "dataset", "label": "Sentetik araç verisi", "properties": {}},
    {"id": "karsilastirma", "type": "comparison", "label": "Karşılaştırma", "properties": {}}
  ],
  "relations": [{"from": "karsilastirma", "type": "uses", "to": "veri"}],
  "tasks": [
    {"id": "T1", "title": "Sentetik veriyi hazırla", "status": "todo", "object_ids": ["veri"],
     "depends_on": [], "decision_ids": ["D1"], "acceptance": ["Üç kurmaca araç var"], "evidence": []},
    {"id": "T2", "title": "Karşılaştırmayı yaz", "status": "todo", "object_ids": ["karsilastirma"],
     "depends_on": ["T1"], "decision_ids": ["D1"], "acceptance": ["Üç araç aynı ölçütlerle karşılaştırılıyor"], "evidence": []}
  ],
  "decisions": [
    {"id": "D1", "topic": "storage", "statement": "Yerel JSON kullan",
     "status": "accepted", "rationale": "Örnek kullanıcı bunu istedi", "source": "Örnek senaryo; gerçek kullanıcı kararı değildir",
     "supersedes": null, "accepted_by": "demo-user"}
  ],
  "history": []
}
```

Bu örnek değerleri gerçek projeye kopyalayıp kabul edilmiş tercih sayma.
`constraints` yalnız gerçek kısıtları taşır; belirsiz öneriler decision/proposed
veya open_questions olarak kalır. Her görevde en az bir ölçüt gerekir.

Yeni runtime şema 1 ve 2'yi okur; başlangıç spec'i her ikisiyle de revision=0,
history=[] olabilir. İlk `extend_model` veya `revise_task`, şema 1'i şema 2'ye
geçirir. Şema 2 bu eylemler için `history.change` kaydını taşır: eklemede eklenen
listeler, revizyonda görevin `before` ve `after` görüntüleri. Önceki geçmiş
kayıtları korunur. Bu, değiştirilemez veya bütün durumun yeniden üretilebildiği
bir olay defteri değildir. Bilinmeyen şema sürümü reddedilir.

## Çalıştırma

```
python3 <skill-dir>/scripts/project.py init <root> --spec <spec.json>
python3 <skill-dir>/scripts/project.py upgrade <root>
python3 <root>/.project/scripts/project.py check <root>
python3 <root>/.project/scripts/project.py context <root> --json
python3 <root>/.project/scripts/project.py apply <root> --event <event.json> --expected-revision 0
```

`init` mevcut tam kurulumu sıfırlamaz; farklı spec'i uygulamaz. Yarım/bozuk
kurulumda çakışma raporlar. Mevcut AGENTS.md'yi korur; entegrasyon notu verir.
`check` yapı ve güncellik denetimidir. `context` güncel yapılabilir işler,
kararlar, öneriler ve uyarıları çıkarır. Hazır olmayan todo görev hata değildir.

`upgrade` kaynak skill'den çalıştırılır. State'i doğrular ama değiştirmez;
projeye kopyalı `core.py` ve `project.py` çiftini kalıcı yedekle yeniler.
Kaynakla byte'ları aynıysa `noop`; başarılı yenilemede `updated` ve `backup`
dizini döner. Hata halinde `restored` veya geri yükleme de başarısızsa
`restore_failed` bildirir. İki dosya birlikte atomik değildir; tek yazıcı gerekir.
Eski runtime şema 2'yi okuyamaz. [Yükseltme ve kurtarma](plan-changes.md).

## Eylemler

Her eylem `action`, `actor`, `reason` içerir. actor gerçek uygulayıcı rolüdür;
kimlik doğrulama sağlamaz. Geçerli eylem revizyonu bir artırır. Başarısız eylem
durumu değiştirmez. Örnekler:

```json
{"action":"start_task","actor":"agent","reason":"İlk göreve başlıyorum","task_id":"T1"}
```

Kontrolü yaptıktan sonra kanıt teslimi (criterion ölçütün sıfır tabanlı indeksi):

```json
{"action":"submit_evidence","actor":"agent","reason":"Veriyi kontrol ettim","task_id":"T1",
 "items":[{"path":"data/tools.json","criterion":0,"note":"JSON açıldı; üç kurmaca araç kaydı var","reviewer":"agent"}]}
```

Submit yeni listeyle önceki kanıt listesini değiştirir. Hash'i betik hesaplar.
Kanıt göreve ait kontrol/inceleme beyanıdır; kendiliğinden kalite ispatı değildir.
Her ölçüte kayıt bulunmadan `complete_task` başarılı olmaz.

Rapor kullanıldığında **rapor ve onun kontrol ettiği çıktı/kaynak dosyalarını
birlikte** kaydet. Örneğin aynı criterion=0 için hem `test-sonucu.txt` hem
`data/tools.json` ayrı evidence öğeleri olabilir. Rapor değişmeden verinin
değişmesi böylece eski kanıt olarak görünür. Script raporun gerçekte hangi
dosyaları test ettiğini keşfetmez; doğru kapsamı seçmek inceleyenin sorumluluğudur.

```json
{"action":"complete_task","actor":"agent","reason":"Ölçüt kontrolü tamam","task_id":"T1"}
```

Diğer eylemler:

- `reopen_task`: task_id; görevi todo yapar, eski kanıtı temizler. Transitif bağlı
  done/doing/review işler review olur ve yeni kanıt ister.
- `propose_decision`: decision {id,topic,statement,rationale,source,supersedes}.
- `accept_decision` / `reject_decision`: decision_id.
- `reconcile_task`: task_id, decision_ids; mevcut accepted kararlara bağlar,
  görevi todo yapar ve önceki kanıtları temizler. Önce yeni kararın etkisini incele.
  Transitif bağlı done/doing/review işler de review olur ve yeni kanıt ister.
- `extend_model`: objects, relations, tasks listeleri; en az biri dolu olmalı.
  Yeni kimliklerle nesne, ilişki ve görevleri tek işlemde ekler. Yeni görevler
  todo/boş evidence ve yalnız güncel accepted karar bağlarıyla başlar.
  Yinelenen kimlik/ilişki, geçersiz bağ veya döngü işlemi reddettirir.
- `revise_task`: task_id, definition {title,object_ids,depends_on,decision_ids,acceptance}.
  Beş tanım alanı eksiksiz verilmelidir; aynı tanım ve cancelled görev reddedilir.
  Mevcut karar konuları korunur, bağlar güncel accepted kararlara gider. Görev
  todo olur ve eski evidence temizlenir; transitif bağlı done/doing/review
  işler review olur ve kanıtları temizlenir. Önceki ve sonraki görev geçmişe yazılır.

Aynı konuda etkin karar varsa değişiklik önerisi `supersedes` ile onu gösterir.
Yeni öneri eski kararı geçersiz kılmaz. Kabul edildiğinde önceki karar superseded
olur, ona bağlı görevler yeniden inceleme gerektirir. Bitmiş görevde yeniden
bağlama için önce reopen_task gerekir.

Görev: todo → doing → review → done. Yapılabilirlik bağımlılıklardan hesaplanır.
`cancelled` bağımlılığı karşılamaz. Dosyası değişen, kaybolan veya kararı
geçersizleşen done görev otomatik olarak güncel başarı sayılmaz; context bunu
ve transitif bağımlılık etkisini gösterir. Model değişikliği için açık eylemler
kullanılır: ekleme ve revizyon örnekleri
[plan-changes.md](plan-changes.md) içindedir. Görev iptali, genel proje hedefi
güncelleme, nesne düzenleme, ilişki silme ve genel şema migrasyonu yoktur;
sessiz genel JSON patch yolu kullanılmaz.

Nesne ilişkilerinin `supports`, `uses` gibi tipleri kayıtlı anlam etiketleridir.
Kod destek veya çelişki doğruluğunu, alternatif kaynakların yeterliliğini
hesaplamaz. Yeni ilişki mevcut görevlerin kanıtını kendiliğinden yenilemez.
Görev önkoşulları `depends_on` üzerinden hesaplanır.

## Onarım önerileri

`context --json`, nesneler/ilişkiler ve görevlerin ölçüt/bağımlılık/karar
kimlikleri yanında `repair_actions` döndürür. Güncelliğini kaybeden done görev
için `reopen_task`; eski karara bağlı todo/doing/review görev için aynı konularda
güncel kararlar bulunuyorsa `reconcile_task` önerir. Markdown görünümü de bunları
gösterir. Liste salt okunur öneridir: gerekçeyi değerlendir, `actor` ekle ve güncel
revizyonla uygula. Her adımdan sonra yeniden context oku. `ready` boşken onarım
gerekebilir; bu liste bütün olası sorunları otomatik çözmez.

## Yol ve erişim sınırı

Kanıt yolu köke göre göreli normal dosyadır. Mutlak yol, `..`, symlink ve
`.project` içindeki kayıtlar kanıt olamaz. Bu, bütün ajanın dosya erişimini
kısıtlamaz; yalnız bu komutların desteklediği kanıt yolunu sınırlar.
Tek yazar sözleşmesi vardır. Revizyon kontrolü kilit değildir; eşzamanlı
yazma/elektrik kesintisi dayanıklılığı kanıtlanmış özellik olarak sunulmaz.
