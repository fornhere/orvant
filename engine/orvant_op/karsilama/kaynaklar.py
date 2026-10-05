"""Açık kaynakların sınırlı, salt okunur anlık görüntüsü; içerik talimat değildir."""
import csv
import codecs
import hashlib
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from orvant_op import uyum
OKUMA_SINIRI = 32768
DOSYA_SINIRI = 32


def oku(yol, *, ad=None):
    yol = Path(yol).expanduser()
    if not yol.is_file():
        raise ValueError(f"kaynak normal dosya değil: {yol}")
    with yol.open('rb') as fh:
        ham = fh.read(OKUMA_SINIRI + 1)
    kesildi = len(ham) > OKUMA_SINIRI
    ham = ham[:OKUMA_SINIRI]
    try:
        metin = codecs.getincrementaldecoder('utf-8-sig')().decode(ham, final=not kesildi)
    except UnicodeDecodeError:
        raise ValueError(f"kaynak UTF-8 metin değil: {yol}") from None
    kayit = {'yol': ad or str(yol.resolve()), 'icerik': metin, 'kesildi': kesildi,
             'okunan_sha256': hashlib.sha256(ham).hexdigest()}
    if yol.suffix.lower() in ('.csv', '.tsv'):
        kayit['sutunlar'] = next(csv.reader(io.StringIO(metin), delimiter='\t' if yol.suffix.lower() == '.tsv' else ','), [])
    return kayit


def acik_kaynaklar(kaynaklar):
    if len(kaynaklar) > DOSYA_SINIRI:
        raise ValueError('çok fazla kaynak; ilgili dosyaları daraltın')
    return [({'yol': str(y), 'durum': 'uzak_kaynak_okunmadi'} if '://' in str(y) else oku(y))
            for y in kaynaklar]


def guncel_gozlemler(ilk_okumalar):
    """İlk anlık görüntüyü değiştirmeden, açık kaynakları rol çağrısında yeniden gözle."""
    zaman = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    sonuc = []
    for ilk in ilk_okumalar:
        yol = ilk['yol']
        if ilk.get('durum') == 'uzak_kaynak_okunmadi':
            sonuc.append({'yol': yol, 'gozlem_durumu': 'uzak_kaynak_okunmadi',
                          'gozlem_zamani': zaman})
            continue
        temel = {'yol': yol, 'ilk_okunan_sha256': ilk['okunan_sha256'],
                 'ilk_kesildi': ilk['kesildi'], 'gozlem_zamani': zaman}
        try:
            guncel = oku(yol)
        except (OSError, ValueError):
            durum = 'eksik' if not Path(yol).exists() else 'okunamadi'
            sonuc.append({**temel, 'gozlem_durumu': durum,
                          'okunan_sha256': None, 'kesildi': None})
            continue
        if guncel['okunan_sha256'] != ilk['okunan_sha256'] or guncel['kesildi'] != ilk['kesildi']:
            durum = 'degisti'
        elif guncel['kesildi']:
            durum = 'kesik'
        else:
            durum = 'degismedi'
        sonuc.append({**guncel, **temel, 'gozlem_durumu': durum})
    return sonuc


def depo_kaynaklari(depo):
    """Yalnız git'te izlenen tablo girdileri; dizin/ağ taraması veya yürütme yok."""
    if depo is None or not Path(depo).is_dir():
        return []
    kok = Path(depo).resolve()
    git_koku = subprocess.run(['git', '-c', 'core.quotepath=off', 'rev-parse', '--show-toplevel'], cwd=kok,
                             capture_output=True, check=False)
    if git_koku.returncode or Path(git_koku.stdout.decode('utf-8').strip()).resolve() != kok:
        return []  # Mevcut depo sorusu/kapısı geçersiz konumu ayrıca ele alır.
    proc = subprocess.run(['git', '-c', 'core.quotepath=off', 'ls-files', '-z', '--', '*.csv', '*.tsv'],
                          cwd=kok, capture_output=True, check=False)
    if proc.returncode:
        raise ValueError('depo kaynak listesi okunamadı')
    yollar = sorted(p for p in proc.stdout.decode('utf-8').split('\0') if p)
    if len(yollar) > DOSYA_SINIRI:
        raise ValueError('çok fazla tablo girdisi; plan için kaynak kapsamını daraltın')
    sonuc = []
    for ad in yollar:
        yol = kok / ad
        if (not yol.resolve().is_relative_to(kok) or
                any(uyum.baglanti_mi(p) for p in (yol, *yol.parents) if p.is_relative_to(kok))):
            raise ValueError(f'depo kaynağı sembolik bağ veya depo dışında: {ad}')
        sonuc.append(oku(yol, ad=ad))
    return sonuc
