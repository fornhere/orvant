# Orvant teknik tasarımı

Orvant, yerel proje kaydı kullanan bir skill ile yazılım yürütme motorunu birleştirir. Skill; hedef, tür, nesne, ilişki, karar, görev ve kabul dayanaklarını kurar. Motor onaylı sözleşmeden görev grafiğine ve bağımsız kabul kapısına gider. İki tarafın kayıtları ayrı tutulur.

## Kayıt runtime'ı

| Modül | Sorumluluk |
| --- | --- |
| `ontology.py` | Tür/özellik/uç/çokluk kuralları, değişim yönü ve görev girdi kapanışı. |
| `acceptance.py` | İncelenmiş alternatif destekler; sayım, küme eşitliği/ayrıklığı ve `all`/`any` gibi sınırlı alan kuralları. |
| `core.py` | Görev ve karar geçişleri, üretici bağımlılıkları, kabul güncelliği, geçmiş ve önizleme. |
| `project.py` | CLI, dosya sınırları, yazıcı kilidi, revizyon/digest kontrolü, atomik state yazımı, yükseltme ve geçiş yedekleri. |

Yetkili kayıt `.project/state.json` dosyasıdır. Markdown görünümleri bu kayıttan türetilir. Yeni kayıtlar şema 3 kullanır; eski şemaların okunması ontolojiye kendiliğinden geçiş anlamına gelmez. [Model sözleşmesi](skills/orvant/references/model.md) ve [ontoloji rehberi](skills/orvant/references/ontology.md).

Tür tanımı ile somut nesne ayrıdır. İlişki türleri izinli uçları, çokluğu ve değişiklik etkisini bildirir. Görevler `object_ids`, `input_ids`, `output_ids` ve `depends_on` ile konu, dayanak, çıktı ve önkoşullara bağlanır. Bir çıktının tek üreticisi bulunur; etkin görev grafında döngü reddedilir.

## Kabul ve değişimin etkisi

Göreve başlanırken `run_snapshot` girdileri kaydeder. Teslimde incelenen dosyaların hash'leri, girdi/çıktı manifestleri ve alan kurallarının dayanakları tutulur. Başlangıçtan beri değişen girdilere eski çalışmanın sonucunu bağlamak reddedilir. Güncel context, kaydedilmiş durumdan ayrı olarak kabul güncelliğini ve işin hazır/engelli durumunu hesaplar.

`support_groups` yalnız gerçekten incelenmiş destek dallarını sayar. `any` için bir güncel incelenmiş dal, `all` için tüm dallar gerekir. Yeni kaynak kendiliğinden kabul edilmiş destek olmaz. İçeriğin doğruluğu ile kaydedilen dayanağın güncel olması ayrı değerlendirmelerdir.

`preview` ve `apply` aynı geçiş kurallarını kullanır. `--expected-revision` eski kaydı, `--preview-digest` önizlemeden beri değişen state/eylem/dosya gözlemlerini reddeder. State geçici dosya ve `os.replace` ile atomik yazılır; görünümler sonra üretilir. Görünüm hatasında yazılmış eylem tekrar uygulanmaz.

CLI yazıcıları yerel işletim sistemi kilidiyle sıraya alınır. Başka editörler bu kilide uymak zorunda değildir. Dosya gözlemleri atomik dosya sistemi snapshot'ı oluşturmaz; kayıt geçmişi imzalı veya dışarıdan değiştirilemez bir denetim defteri değildir.

## Yazılım motoru

S1 hedefi onaylı sözleşmeye çevirir. S2 görev grafiğini ve yetki isteklerini kurar; kehanet sözleşmenin kabul koşullarına bağlanır. S3 işi Codex ile ayrı Git worktree'lerinde yürütür, sonucu bağımsız kapıdan geçirir. S4 başarısızlığı teşhis eder; S6 mevcut planı sınırlar içinde sürdürür ve karar sorularını birleştirir.

İşçinin `complete` bildirimi kapı kararı değildir. Kehanetin pozitif referans ve uygulanabilir kusurlu çıktı kontrolleri, kabul ölçütünün çalışabilirliğini denetler. Sözleşmenin yeterliliği ve modelin semantik yorumu bu kontrollerle bütünüyle ispatlanmaz. [Motor rehberi](docs/MOTOR.md).

Motor oturumu ürün deposunu içermez; ürün deposu da oturumu içermez. `.project` kabulü, motor sözleşmesi revizyonunun kullanıcı onayı yerine geçmez. İzin ve karar kayıtları gerçek kullanıcı taahhüdüne dayanır.

## Yükseltme ve kapsam

Skill runtime yükseltmesi kaynak paketten çalıştırılır; önceki dosyaları yedekleyerek değiştirir ve state'i değiştirmez. Ontoloji migrasyonu ayrı eylemdir; eski görevlerin girdi/çıktı bağları açıkça tanımlanır ve eski state yedeklenir. [Değişiklik ve geçiş](skills/orvant/references/plan-changes.md).

Kayıt runtime'ı Python 3.10+, motor Python 3.11+ kullanır. Gerçek motor yürütücüsü Codex'tir. Bu beta yerel dosya/CLI düzeninde çalışır; çok kullanıcılı kurumsal yetki sistemi veya genel sorgu motoru içermez. Kabul komutları, kehanet sandbox'ı ve yazma kapsamı ayrı kontrollerdir; gerçek plan ve izinler yürütmeden önce incelenir.
