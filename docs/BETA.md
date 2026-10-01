# Orvant 0.1.0b1

Bu açık beta, küçük yazılım ve veri otomasyonu işlerinde hedefi sözleşmeye,
planı görev bağlarına ve çıktıyı bağımsız kabul ölçütlerine bağlayan motoru
ve proje kayıt/ontoloji skill'ini içerir.

Motor Python 3.11+, Linux, Git ve gerçek yürütmede Codex CLI gerektirir.
Varsayılan model `gpt-6.1-sol`dur. Geliştirmede kullanılan Codex CLI sürümü
0.155.1'dir; diğer sürümlerin uyumluluğu henüz doğrulanmadı.
Kayıt/ontoloji runtime'ı ayrı olarak Python 3.10+ ile kullanılabilir.

Yayımlanan motorun kapsamı hedef netleştirme, planlama, yürütme, bağımsız
kalite kapısı ve aynı oturumdan devam etmektir. Kendini geliştirme/terfi
araçları bu dağıtımın kapsamında değildir. Wheel motoru kurar; skill
`skills/orvant` dizininden ayrıca kurulur.

İşçinin işi tamamladığını söylemesi kabul anlamına gelmez. Kullanıcı cevabı,
sözleşme onayı ve izin kararları gerçek kullanıcıya aittir. Açık soru,
kesinti, reddedilen çıktı veya eksik yetki ayrı durumlar olarak gösterilir.
Genel otonomluk, gerçek model başarısı ve kullanıcı emeğinde ölçülmüş
kazanç bu beta için doğrulanmış sonuçlar değildir.

[Kurulum](../README.tr.md) · [Motor](MOTOR.md) ·
[Ontoloji ve kayıt](KULLANIM.md) ·
[GitHub Issues](https://github.com/fornhere/orvant/issues)

Bir sorun bildirirken sürümü, işletim sistemi/Codex sürümünü, komutu ve
hata mesajını belirt. Özel proje içeriği veya kimlik bilgisi paylaşma.
