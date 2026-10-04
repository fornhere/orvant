# Örnek çıktı üretim komutları

Bu komutlar depo kökünde Bash ile çalıştırılır. Çıktılara yapılan tek düzenleme,
rastgele üretilen mutlak geçici dizin önekinin `<demo>` ile değiştirilmesidir.

```bash
set -euo pipefail
demo="$(mktemp -d)/demo"
trap 'rm -rf "${demo%/demo}"' EXIT

python3 skills/orvant/scripts/project.py init "$demo" --spec examples/demo-spec.json \
  | sed "s|$demo|<demo>|g" > docs/sample-output/init.txt
python3 "$demo/.project/scripts/project.py" check "$demo" > /dev/null
python3 "$demo/.project/scripts/project.py" context "$demo" \
  | sed "s|$demo|<demo>|g" > docs/sample-output/context.txt
python3 "$demo/.project/scripts/project.py" ontology "$demo" \
  | sed "s|$demo|<demo>|g" > docs/sample-output/ontology.md
python3 "$demo/.project/scripts/project.py" derive "$demo" \
  | sed "s|$demo|<demo>|g" > docs/sample-output/derive.json
python3 "$demo/.project/scripts/project.py" lanes "$demo" \
  | sed "s|$demo|<demo>|g" > docs/sample-output/lanes.json
```
