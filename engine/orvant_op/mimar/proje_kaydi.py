"""Onaylı motor oturumunu şema-3 proje kaydına deterministik aktarır.

Kalıcı yazımlar yalnız skill'in init/preview/apply CLI'sinden geçer.
Kalıp kapsamları dosya değildir; bunlar özette bildirilir.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from orvant_op.ayarlar import depo_koku, proje_kaydi_script
from orvant_op import uyum
from orvant_op.karsilama.sozlesme import _spec_govdesi
from .dogrulama import dogrula


def metin(veri):
    return json.dumps(veri, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _oku(yol):
    return json.loads(Path(yol).read_text(encoding='utf-8'))


def _kimlik(tur, *parcalar):
    return 'motor-' + tur + '-' + hashlib.sha256(metin(parcalar).encode()).hexdigest()[:24]


def _ozellik(tur):
    return {'type': tur, 'required': True, 'enum': None}


def _iliski_tipi(ad, kaynak, hedef, etki):
    return {'id': ad, 'label': ad, 'from_type': kaynak, 'to_type': hedef,
            'from_min': 0, 'from_max': None, 'to_min': 0, 'to_max': None, 'impact': etki}


def _temel_spec(sozlesme):
    """Köprüye özgü onay koruması; v03_spec'in tam onay koşulu değişmez."""
    onay = sozlesme.get('onay')
    if (not onay or onay['revizyon'] != sozlesme['revizyon']
            or onay['durum'] not in ('onaylandi', 'fizibilite_onayli')):
        raise ValueError('onaylı sözleşme revizyonu olmadan proje kaydı üretilmez')
    return _spec_govdesi(sozlesme, proje_kaydi=True)


def donustur(sozlesme, plan):
    """Saf dönüşüm; kabul edilmiş işler de kanıt aktarılmadan açık kalır."""
    spec = _temel_spec(sozlesme)
    spec['ontology'] = {'object_types': [
        {'id': 'deliverable', 'label': 'Teslim dosyası', 'properties': {'path': _ozellik('file')}},
        {'id': 'acceptance', 'label': 'Kabul ölçütü', 'properties': {'acceptance': _ozellik('string_list')}}],
        'relation_types': [_iliski_tipi('verifies', 'acceptance', 'deliverable', 'reverse'),
                           _iliski_tipi('feeds', 'deliverable', 'deliverable', 'forward')]}
    ciktilar, olcutler, belirsiz = {}, {}, []
    for g in plan['gorevler']:
        ciktilar[g['id']], olcutler[g['id']] = [], []
        for yol in dict.fromkeys(g['yazilabilir']):
            if any(c in yol for c in '*?[]') or yol.endswith('/'):
                belirsiz.append({'gorev': g['id'], 'kapsam': yol})
                continue
            oid = _kimlik('cikti', g['id'], yol)
            spec['objects'].append({'id': oid, 'type': 'deliverable', 'label': yol, 'properties': {'path': yol}})
            ciktilar[g['id']].append(oid)
        for kabul in g['kabul']:
            oid = _kimlik('kabul', g['id'], kabul['id'])
            kriter = [kabul['beklenen']]
            for alan in ('komut', 'rubrik'):
                if kabul.get(alan):
                    kriter.append(kabul[alan])
            spec['objects'].append({'id': oid, 'type': 'acceptance', 'label': kabul['id'],
                                    'properties': {'acceptance': kriter}})
            olcutler[g['id']].extend(kriter)
            for cikti in ciktilar[g['id']]:
                spec['relations'].append({'id': _kimlik('verifies', oid, cikti), 'type': 'verifies', 'from': oid, 'to': cikti})
    for karar in sozlesme['kararlar']:
        kabul = karar['durum'] == 'cozuldu'
        kaynak = karar.get('kaynak_olay_id') or sozlesme['onay'].get('olay_id')
        if kabul and not kaynak:
            raise ValueError('çözülmüş kararın onay kaynağı yok: ' + karar['id'])
        spec['decisions'].append({'id': karar['id'], 'topic': karar['id'],
            'statement': karar.get('deger') or karar['soru']['metin'],
            'rationale': karar.get('erteleme_gerekcesi') or karar.get('gerekce') or karar['baslik'],
            'status': 'accepted' if kabul else 'proposed', 'source': kaynak or '',
            'supersedes': None, 'accepted_by': 'kullanici' if kabul else None})
    kararlar = {d['id']: d for d in spec['decisions']}
    yetkiler = {y['id']: y for y in plan['yetki_istekleri']}
    engeller = {}
    harita = {g['id']: g for g in plan['gorevler']}

    def engel(gid):
        if gid in engeller:
            return engeller[gid]
        g = harita[gid]
        acik = {kid for kid in g['bekleyen_kararlar']
                if kid not in kararlar or kararlar[kid]['status'] != 'accepted'}
        izinler = {yid for yid in g['yetki_istek_ids']
                   if yetkiler[yid]['durum'] != 'verildi' or not yetkiler[yid]['onay_olay_id']}
        for dep in g['bagimliliklar']:
            k, y = engel(dep)
            acik.update(k)
            izinler.update(y)
        engeller[gid] = (acik, izinler)
        return acik, izinler

    aktarilmayan = []
    for g in plan['gorevler']:
        acik, izinler = engel(g['id'])
        if acik or izinler:
            aktarilmayan.append({'id': g['id'], 'acik_kararlar': sorted(acik),
                'acik_yetkiler': sorted(izinler),
                'neden': 'Görevin veya bağımlılığının çözülmemiş önkoşulu var'})
            continue
        girdiler = list(dict.fromkeys(o for dep in g['bagimliliklar'] for o in ciktilar[dep]))
        for kaynak in girdiler:
            for hedef in ciktilar[g['id']]:
                spec['relations'].append({'id': _kimlik('feeds', kaynak, hedef), 'type': 'feeds', 'from': kaynak, 'to': hedef})
        spec['tasks'].append({'id': g['id'], 'title': g['baslik'], 'status': 'todo',
            # Kabul nesnesi ayrı doğrulama işi türetilebilir; teslim işi dosyaya bağlıdır.
            'object_ids': list(dict.fromkeys(girdiler + ciktilar[g['id']])),
            'input_ids': girdiler, 'output_ids': ciktilar[g['id']], 'depends_on': list(g['bagimliliklar']),
            'decision_ids': list(g['bekleyen_kararlar']), 'acceptance': olcutler[g['id']], 'evidence': [],
            'input_snapshot': None, 'generation': 0, 'review_reasons': []})
    return spec, {'kanitsiz_kabul': sum(g['durum'] == 'kabul' for g in plan['gorevler']),
                  'dosyaya_cevrilmeyen_kapsamlar': belirsiz, 'aktarilmayan_gorevler': aktarilmayan}


def _hedef(kok, calisma):
    """Depo kontrolündeki dar kök ilkesini korur; git yazımı istemez."""
    ham = Path(os.path.abspath(Path(kok).expanduser()))
    for parca in (ham, *ham.parents):
        if uyum.baglanti_mi(parca):
            raise ValueError('hedef kökte sembolik bağ kullanılamaz')
    hedef = ham.resolve()
    ev = Path.home().resolve()
    from .envanter import hassas_ev_kokleri, ev_kapi_kokleri, sistem_kokleri
    yasaklar = (depo_koku(), Path(calisma).resolve(), *hassas_ev_kokleri(ev),
                *ev_kapi_kokleri(ev), *sistem_kokleri())
    if (hedef in (ev, Path(hedef.anchor)) or '.git' in hedef.parts or '.project' in hedef.parts
            or any(hedef.is_relative_to(p) for p in yasaklar)):
        raise ValueError('hedef proje kökü güvenli kapsam dışında')
    if hedef.exists() and not hedef.is_dir():
        raise ValueError('hedef proje kökü dizin değil')
    ust = hedef if hedef.exists() else hedef.parent
    while not ust.exists():
        ust = ust.parent
    proc = subprocess.run(['git', '-C', str(ust), 'rev-parse', '--show-toplevel'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    if proc.returncode == 0 and Path(proc.stdout.strip()).resolve() != hedef:
        raise ValueError('hedef başka bir git deposunun içinde; kök değil')
    return hedef


def _script():
    return proje_kaydi_script()


def _calistir(argv, *, veri=None):
    sonuc = subprocess.run(argv, input=metin(veri) if veri is not None else None,
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    if sonuc.returncode:
        try:
            hata = json.loads(sonuc.stdout)['error']
        except (ValueError, KeyError, TypeError):
            hata = (sonuc.stderr or sonuc.stdout).strip().splitlines()[-1]
        raise ValueError(hata)
    return json.loads(sonuc.stdout)


def _sinama(spec, mevcut, olaylar, kok):
    # Ayrı süreç, skill'in core/ontology modüllerini motor ad alanına karıştırmaz.
    kod = ('import sys,json; sys.path.insert(0,sys.argv[1]); import core; from pathlib import Path; '
           'v=json.load(sys.stdin); core.validate(v["spec"]); s=v["state"]; '
           '\nfor e in v["events"]: s=core.apply_event(s,e,Path(sys.argv[2]))'
           '\nprint(json.dumps(s))')
    return _calistir([sys.executable, '-c', kod, str(_script().parent), str(kok)],
                     veri={'spec': spec, 'state': mevcut, 'events': olaylar})


def olaylari_uret(spec, mevcut):
    """Mevcut kaydı korur; tüm kimlik çakışmalarını yazmadan önce reddeder."""
    if mevcut['schema_version'] != 3:
        raise ValueError('mevcut kayıt şema 3 değil; önce açık ontoloji geçişi gerekir')
    olaylar = []
    def olay(eylem, **veri):
        olaylar.append({'action': eylem, 'actor': 'motor-kopru', 'reason': 'Onaylı motor planını aktar', **veri})
    def ekler(istenen, eski, ad, alanlar=None):
        harita = {x['id']: x for x in eski}
        sonuc = []
        for x in istenen:
            onceki = harita.get(x['id'])
            if onceki is None:
                sonuc.append(copy.deepcopy(x))
            elif any(onceki.get(k) != x.get(k) for k in (alanlar or x.keys())):
                raise ValueError(f'çakışan {ad} kimliği: {x["id"]}')
        return sonuc
    ontology = copy.deepcopy(mevcut['ontology'])
    for alan in ('object_types', 'relation_types'):
        ontology[alan].extend(ekler(spec['ontology'][alan], ontology[alan], alan))
    if ontology != mevcut['ontology']:
        olay('mutate_graph', operations=[{'op': 'replace_ontology', 'ontology': ontology}])
    # Core karar içeriğini yerinde değiştirmez. Çözülmüş yeni içerik ayrı,
    # kararlı kimlikle önerilir; eski öneri ve kullanıcının kaydı korunur.
    spec = copy.deepcopy(spec)
    eski_kararlar = {d['id']: d for d in mevcut['decisions']}
    for d in spec['decisions']:
        onceki = eski_kararlar.get(d['id'])
        if onceki and onceki['status'] == 'proposed' and d['status'] == 'accepted':
            alanlar = ('topic', 'statement', 'rationale', 'source', 'supersedes')
            if any(onceki[k] != d[k] for k in alanlar):
                eski_id = d['id']
                d['id'] = _kimlik('karar', eski_id, {k: d[k] for k in alanlar})
                for g in spec['tasks']:
                    g['decision_ids'] = [d['id'] if kid == eski_id else kid for kid in g['decision_ids']]
    for d in spec['decisions']:
        onceki = eski_kararlar.get(d['id'])
        proposal = {k: v for k, v in d.items() if k not in ('status', 'accepted_by')}
        if onceki is None:
            olay('propose_decision', decision=proposal)
        elif onceki != d:
            if (onceki['status'] != 'proposed' or d['status'] != 'accepted'
                    or any(onceki[k] != v for k, v in proposal.items())):
                raise ValueError(f'çakışan karar kimliği: {d["id"]}')
        if d['status'] == 'accepted' and (onceki is None or onceki['status'] == 'proposed'):
            olay('accept_decision', decision_id=d['id'])
            olaylar[-1]['actor'] = d['accepted_by']
    nesneler = ekler(spec['objects'], mevcut['objects'], 'nesne')
    iliskiler = ekler(spec['relations'], mevcut['relations'], 'ilişki')
    gorevler = ekler(spec['tasks'], mevcut['tasks'], 'görev',
                    ('title', 'object_ids', 'input_ids', 'output_ids', 'depends_on', 'decision_ids', 'acceptance'))
    if nesneler or iliskiler or gorevler:
        olay('extend_model', objects=nesneler, relations=iliskiler, tasks=gorevler)
    return olaylar


def aktar(calisma, kok, *, kuru=False):
    calisma = Path(calisma)
    sozlesme = _oku(calisma / 'karsilama/sozlesme.json')
    try:
        _temel_spec(sozlesme)
    except (KeyError, TypeError) as exc:
        raise ValueError(f'sözleşme proje kaydı üretimine uygun değil: {exc}') from exc
    plan = _oku(calisma / 'plan/plan.json')
    olay_yolu = calisma / 'plan/olaylar.jsonl'
    if not olay_yolu.is_file() or not any(json.loads(s).get('tur') == 'plan_dogrulandi'
                                         for s in olay_yolu.read_text(encoding='utf-8').splitlines() if s.strip()):
        raise ValueError('plan doğrulanmamış: plan_dogrulandi olayı yok')
    # Yürütme durumlarını başlangıç doğrulayıcısına sunmadan önce yalnız kopyada normalize et.
    from orvant_op.karsilama.roller import veri_dogrula
    from .roller import SEMA_YOLU
    veri_dogrula(plan, _oku(SEMA_YOLU))
    aday = copy.deepcopy(plan)
    for g in aday['gorevler']:
        g['durum'] = 'hazir'
    for y in aday['yetki_istekleri']:
        y.update(durum='acik', onay_olay_id=None)
    birlesik = {k['id']: k for k in sozlesme['kararlar']}
    karar_yolu = calisma / 'plan/kararlar.json'
    if karar_yolu.is_file():
        plan_kararlari = _oku(karar_yolu)
        if not isinstance(plan_kararlari, list):
            raise ValueError('plan kararları liste olmalı')
        ids = [k['id'] for k in plan_kararlari]
        if len(ids) != len(set(ids)):
            raise ValueError('plan karar kimliği yinelenmiş')
        birlesik.update({k['id']: k for k in plan_kararlari})
    sozlesme = copy.deepcopy(sozlesme)
    sozlesme['kararlar'] = list(birlesik.values())
    kararlar = copy.deepcopy(sozlesme['kararlar'])
    for karar in kararlar:
        if karar['id'] in {k for g in plan['gorevler'] for k in g['bekleyen_kararlar']}:
            karar['durum'] = 'ertelendi'
    dogrula(aday, sozlesme, kararlar)
    hedef = _hedef(kok, calisma)
    spec, ozet = donustur(sozlesme, plan)
    state_yolu = hedef / '.project/state.json'
    if (hedef / '.project').exists():
        if uyum.baglanti_mi(hedef / '.project') or uyum.baglanti_mi(state_yolu):
            raise ValueError('proje kaydında sembolik bağ kullanılamaz')
        mevcut = _oku(state_yolu)
        olaylar = olaylari_uret(spec, mevcut)
        _sinama(spec, mevcut, olaylar, hedef)
        sonuc = {**ozet, 'eylem': 'apply', 'beklenen_revizyon': mevcut['revision'], 'olaylar': olaylar,
                 'korunan_proje_farklari': [k for k in spec['project'] if spec['project'][k] != mevcut['project'][k]]}
    else:
        _sinama(spec, spec, [], hedef)
        sonuc = {**ozet, 'eylem': 'init', 'spec': spec, 'olaylar': []}
    if kuru:
        return sonuc
    with tempfile.TemporaryDirectory(prefix='orvant-proje-kaydi-') as gecici:
        yol = Path(gecici) / 'veri.json'
        if sonuc['eylem'] == 'init':
            yol.write_text(metin(spec), encoding='utf-8')
            kurulum = _calistir([sys.executable, str(_script()), 'init', str(hedef), '--spec', str(yol)])
            if kurulum.get('result') == 'already_initialized':
                raise ValueError('hedef eşzamanlı kurulmuş; mevcut kaydı yeniden okuyarak aktarın')
        else:
            revizyon = mevcut['revision']
            for olay in olaylar:
                yol.write_text(metin(olay), encoding='utf-8')
                args = [str(hedef), '--event', str(yol), '--expected-revision', str(revizyon)]
                preview = _calistir([sys.executable, str(_script()), 'preview', *args])
                _calistir([sys.executable, str(_script()), 'apply', *args, '--preview-digest', preview['preview_digest']])
                revizyon += 1
    return sonuc
