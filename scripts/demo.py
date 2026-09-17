#!/usr/bin/env python3
"""Create a reproducible, explicitly synthetic project demo in a fresh directory."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / 'skills/orvant/scripts/project.py'


def run_demo(root):
    if root.exists():
        raise SystemExit('Demo hedefi zaten var; yeni bir klasör seçin.')
    log = []
    with tempfile.TemporaryDirectory(prefix='proje-demo-input-') as td:
        event_file = Path(td) / 'event.json'

        def call(args, expected=0):
            result = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                                    capture_output=True, text=True, encoding="utf-8")
            log.append({'args': list(map(str, args)), 'exit_code': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode != expected:
                raise RuntimeError(f'Unexpected result: {result.stdout}\n{result.stderr}')
            return result

        def event(action, **fields):
            revision = json.loads((root / '.project/state.json').read_text(encoding="utf-8"))['revision']
            payload = {'action': action, 'actor':'demo-runner',
                       'reason':'Sentetik, kontrollü iş akışı deneyi', **fields}
            event_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return ['apply', root, '--event', event_file, '--expected-revision', revision]

        call(['init', root, '--spec', REPO / 'examples/ai-karsilastirma.json'])
        initial = (root / '.project/state.json').read_bytes()
        call(event('start_task', task_id='T-COMPARE'), expected=1)
        assert (root / '.project/state.json').read_bytes() == initial
        (root / 'data').mkdir()
        tools = [{'name':'Örnek A','synthetic':True,'purpose':'Metin'},
                 {'name':'Örnek B','synthetic':True,'purpose':'Görsel'},
                 {'name':'Örnek C','synthetic':True,'purpose':'Kod'}]
        (root / 'data/tools.json').write_text(json.dumps(tools,ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
        call(event('start_task', task_id='T-DATA'))
        inspected = json.loads((root / 'data/tools.json').read_text(encoding="utf-8"))
        assert len(inspected) == 3 and all(item['synthetic'] for item in inspected)
        call(event('submit_evidence', task_id='T-DATA', items=[{
            'path':'data/tools.json','criterion':0,'reviewer':'demo-runner',
            'note':'Demo betiği JSON okudu; len==3 ve tüm synthetic alanları true kontrolü geçti.'}]))
        call(event('complete_task', task_id='T-DATA'))
        (root / 'demo-notu.md').write_text('Bu çalışma sentetik bir örnektir.\n', encoding="utf-8")
        call(event('start_task', task_id='T-NOTE'))
        assert 'sentetik' in (root / 'demo-notu.md').read_text(encoding="utf-8")
        call(event('submit_evidence', task_id='T-NOTE', items=[{
            'path':'demo-notu.md','criterion':0,'reviewer':'demo-runner',
            'note':'Demo betiği açıklamada sentetik ifadesini kontrol etti; yalnız bu ölçüt denendi.'}]))
        call(event('complete_task', task_id='T-NOTE'))
        call(['check', root])
        clean_context = call(['context', root]).stdout
        (root / 'KANIT-DEGISMEDEN-ONCE.md').write_text(clean_context, encoding="utf-8")
        (root / 'demo-notu.md').write_text('Bu çalışma sentetik bir örnektir. Açıklama sonradan değiştirildi.\n', encoding="utf-8")
        call(['check', root], expected=1)
        report = json.loads(call(['context', root, '--json']).stdout)
        assert 'T-COMPARE' in report['ready'] and 'T-CHECK' not in report['ready']
        assert next(t for t in report['tasks'] if t['id']=='T-NOTE')['effective_status']=='needs_review'
        (root / 'GUNCEL-DURUM.md').write_text(call(['context', root]).stdout, encoding="utf-8")
        # Re-init must not reset either state or user content, even after state changed.
        snapshot = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        call(['init', root, '--spec', REPO / 'examples/ai-karsilastirma.json'])
        assert snapshot == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in root.rglob('*') if p.is_file()}
    (root / 'DEMO-KANITI.json').write_text(json.dumps({
        'synthetic':True,'expected_check_after_change':1,
        'purpose':'Bağımlılık reddi, geçerli kontrol, eski kanıt tespiti ve no-op kurulum',
        'quality_claim':'Sentetik ölçütler dışında ürün kalitesi veya insan kabulü iddia edilmez.',
        'steps':log}, ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
    print(json.dumps({'ok':True,'root':str(root),'steps':len(log),
                      'note':'T-NOTE bilerek eski kanıt taşır; check=1 beklenen demo sonucudur.'},ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    run_demo(parser.parse_args().root.absolute())
