"""Standart kitaplıkla deterministik Python/JS/TS grafı ve fark olayı."""
from __future__ import annotations

import ast
import copy
import fnmatch
import hashlib
import io
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import subprocess
import tokenize

import core

EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts'}
IGNORED = {'.git', 'node_modules', 'dist', 'build', '__pycache__', '.venv', '.project'}
PREFIX = 'code-graph:'
REGEX_START = {'=', '(', '[', ',', ':', ';', '!', '?', '=>', '&&', '||',
               '{', '}', '+', '-', '*', '%', '<', '>', '&', '|', '^', '~',
               'typeof', 'void', 'delete', 'in', 'of', 'new', 'throw', 'yield',
               'await', 'return', 'case', 'else', 'do'}
JSX_DECLARATION = re.compile(r'^[ \t]*(?:import|export)\b', re.MULTILINE)
JSX_CLOSING = re.compile(r'</\s*([\w.$:-]*)\s*>')
JSX_START = re.compile(r'<(?:[A-Za-z_$]|>)')
JSX_OPENING = re.compile(r'<([A-Za-z_$][\w.$:-]*|)(?=[\s/>])')
TS_GENERIC = re.compile(r'<[A-Za-z_$][\w$]*\s*(?:,|=|extends\b)')
TS_CALLABLE = re.compile(r'<[A-Za-z_$][\w$]*\s*>\s*\(')
TS_SIGNATURE_PART = re.compile(
    r'(?P<ignore>\s+|//[^\r\n]*|/\*[\s\S]*?\*/)|'
    r'''(?P<string>"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*')|'''
    r'(?P<word>[\w$]+)|(?P<punct>=>|.)')


def _match(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)


def _selected(path, include, exclude):
    return (not include or any(_match(path, p) for p in include)) and not any(_match(path, p) for p in exclude)


def _posix_norm(path):
    """Dosya sistemi ayırıcısından bağımsız iç graf yolu üret."""
    return posixpath.normpath(str(path).replace('\\', '/'))


def _symlink_below(root, path):
    """Kökün üstündeki platform symlink/junction'larını güvenlik kapsamına alma."""
    current = path
    while current != root:
        if core.is_link(current):
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    return False


def _ignore_glob(pattern, value):
    # Git'in tek yıldızı yol ayırıcısını geçmez; ** birden fazla dizini geçer.
    regex, i = '', 0
    while i < len(pattern):
        char = pattern[i]
        if char == '*':
            if pattern[i:i + 2] == '**':
                i += 1
                if i + 1 < len(pattern) and pattern[i + 1] == '/':
                    regex += '(?:.*/)?'
                    i += 1
                else:
                    regex += '.*'
            else:
                regex += '[^/]*'
        elif char == '?':
            regex += '[^/]'
        elif char == '[':
            end = pattern.find(']', i + 1)
            if end >= 0:
                group = pattern[i + 1:end]
                regex += '[' + ('^' + group[1:] if group.startswith('!') else group) + ']'
                i = end
            else:
                regex += r'\['
        elif char == '\\' and i + 1 < len(pattern):
            i += 1
            regex += re.escape(pattern[i])
        else:
            regex += re.escape(char)
        i += 1
    return re.fullmatch(regex, value) is not None


def _ignored(path, rules, directory):
    ignored = False
    for base, pattern in rules:
        prefix = '' if base == '.' else base + '/'
        if not path.startswith(prefix):
            continue
        local = path[len(prefix):]
        negate = pattern.startswith('!')
        if negate:
            pattern = pattern[1:]
        if pattern.endswith('/') and not directory:
            continue
        pattern = pattern.rstrip('/')
        anchored = pattern.startswith('/') or '/' in pattern
        pattern = pattern.lstrip('/')
        if _ignore_glob(pattern, local if anchored else local.rsplit('/', 1)[-1]):
            ignored = not negate
    return ignored


def _files(root, include, exclude, max_files, metadata=False):
    if max_files is not None and max_files < 0:
        raise ValueError('max-files must be nonnegative')
    try:
        run = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.'], capture_output=True, check=True)
        candidates = [root / os.fsdecode(p) for p in run.stdout.split(b'\0') if p]
    except (OSError, subprocess.CalledProcessError):
        candidates = []
        rules = []
        for directory, dirs, names in os.walk(root, followlinks=False):
            folder = Path(directory)
            ignore = folder / '.gitignore'
            if ignore.is_file() and not core.is_link(ignore):
                base = folder.relative_to(root).as_posix()
                rules.extend((base, line.rstrip()) for line in ignore.read_text(encoding='utf-8').splitlines()
                             if line.rstrip() and not line.startswith('#'))
            dirs[:] = sorted(d for d in dirs if d not in IGNORED
                             and not core.is_link(folder / d)
                             and not _ignored((folder / d).relative_to(root).as_posix(), rules, True))
            candidates.extend(folder / name for name in sorted(names)
                              if not _ignored((folder / name).relative_to(root).as_posix(), rules, False))
    result = []
    for file in sorted(set(candidates)):
        relative = file.relative_to(root).as_posix()
        if (file.suffix not in EXTENSIONS and not (metadata and file.name in {'package.json', 'tsconfig.json'})) or any(p in IGNORED for p in file.relative_to(root).parts):
            continue
        if _symlink_below(root, file) or not file.is_file():
            continue
        if _selected(relative, include, exclude):
            result.append(relative)
    if max_files is not None and len(result) > max_files:
        raise ValueError(f'max-files exceeded: {len(result)} > {max_files}')
    return result


def _python(path, data, available):
    tree = ast.parse(data.decode(tokenize.detect_encoding(io.BytesIO(data).readline)[0]), filename=path)
    symbols = [(node.name, 'class' if isinstance(node, ast.ClassDef) else 'function', node.lineno)
               for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    dependencies, unresolved, external = set(), [], 0
    def resolve(name):
        stem = name.replace('.', '/')
        return next((p for p in (stem + '.py', stem + '/__init__.py') if p in available), None)
    package = path.split('/')[:-1]
    for node in ast.walk(tree):
        relative = False
        if isinstance(node, ast.Import):
            requests = [(alias.name, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            relative = bool(node.level)
            if node.level:
                if node.level > len(package):
                    unresolved.append((node.lineno, '.' * node.level + (node.module or '')))
                    continue
                base = '.'.join(package[:len(package) - node.level + 1] + ([node.module] if node.module else []))
            else:
                base = node.module or ''
            requests = []
            for alias in node.names:
                child = base + '.' + alias.name if base else alias.name
                requests.append((child if resolve(child) else base, '.' * node.level + (node.module or alias.name)))
        elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == '__import__'
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'importlib'):
            argument = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'name'), None)
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                unresolved.append((node.lineno, ast.unparse(argument) if argument is not None else '<missing>'))
                continue
            name = argument.value
            relative = name.startswith('.')
            token = name
            if relative:
                argument = node.args[1] if len(node.args) > 1 else next((kw.value for kw in node.keywords if kw.arg == 'package'), None)
                level = len(name) - len(name.lstrip('.'))
                parts = argument.value.split('.') if isinstance(argument, ast.Constant) and isinstance(argument.value, str) else []
                if isinstance(node.func, ast.Name) or level > len(parts):
                    unresolved.append((node.lineno, token))
                    continue
                name = '.'.join(parts[:len(parts) - level + 1] + [name[level:]]).rstrip('.')
            requests = [(name, token)]
        else:
            continue
        for name, token in requests:
            target = resolve(name)
            if target:
                dependencies.add(target)
            elif relative:
                unresolved.append((node.lineno, token))
            else:
                external += 1
    return symbols, dependencies, unresolved, external


def _generic_signature(text, start, call_signature=False):
    """Sınırlı ileri bakış: JSX metni yerine tamamlanmış TS imzası gerekir."""
    # Her aday en fazla sabit sayıda karakter okur; iç içe adaylar da doğrusal.
    end = min(len(text), start + 4096)
    parts = TS_SIGNATURE_PART.finditer(text, start, end)
    stack = []
    phase, annotated = 'generic', False
    pairs = {'>': '<', ')': '(', ']': '[', '}': '{'}
    for part in parts:
        kind, value = part.lastgroup, part[0]
        if kind == 'ignore':
            continue
        if value in {"'", '"', '`'}:
            return False
        if phase == 'after_parameters':
            if value == '=>':
                return True
            if value != ':':
                return False
            phase, annotated = 'return_type', True
            continue
        if phase == 'return_type' and not stack:
            if value == '=>':
                return annotated
            # Interface çağrı imzası: <T>(x: T): T; veya son alanın } sınırı.
            if value in {';', '}'}:
                return call_signature and annotated
            if value == '=':
                return False
        if phase == 'parameters_start':
            if value != '(':
                return False
            phase = 'parameters'
        if kind == 'punct':
            if value in {'<', '(', '[', '{'}:
                stack.append(value)
            elif value in pairs:
                if not stack or stack.pop() != pairs[value]:
                    return False
                if not stack:
                    if phase == 'generic':
                        phase = 'parameters_start'
                    elif phase == 'parameters':
                        phase = 'after_parameters'
    return False


def _tokens(text, jsx=False):
    """Yorumları ve dizge içeriklerini kod belirteçlerinden ayır."""
    tokens = []
    i, line = 0, 1
    contexts = []
    # Kurtarma yerleri bir kez bulunur; her öznitelikte kalan dosya taranmaz.
    declarations = [match.start() for match in JSX_DECLARATION.finditer(text)] if jsx else []
    declaration_index = 0
    type_statement, type_scopes = False, []
    while i < len(text):
        while declaration_index < len(declarations) and declarations[declaration_index] < i:
            declaration_index += 1
        c = text[i]
        mode = contexts[-1]['mode'] if contexts else 'js'
        if mode in {'text', 'tag'}:
            # Eksik JSX sonraki üst düzey bildirimi yutmasın. Bu satır metin
            # de olabilir; kesin kabul yerine belirsizlik bildiriyoruz.
            if declaration_index < len(declarations) and declarations[declaration_index] == i:
                # Yanlış JSX tahmininin ürettiği sentetik parantezleri ve
                # ifade belirteçlerini geri al. Yutulan JS kapanışlarını aynı
                # aralıkta JSX kapalı olarak tekrar okuyarak derinliği koru.
                opening_context = contexts[0]
                del tokens[opening_context['token_start']:]
                tokens.extend((kind, value, number + opening_context['line'] - 1)
                              for kind, value, number in _tokens(
                                  text[opening_context['start']:i], jsx=False))
                tokens.append(('unresolved', 'unclosed JSX', line))
                contexts.clear()
                continue
            if mode == 'text':
                closing = JSX_CLOSING.match(text, i) if c == '<' else None
                if closing:
                    if closing[1] != contexts[-1]['name']:
                        tokens.append(('unresolved', closing[0], line))
                    contexts.pop()
                    tokens.append(('punct', ')', line))
                    line += closing[0].count('\n')
                    i += len(closing[0])
                    continue
                if c != '{' and not (c == '<' and JSX_START.match(text, i)):
                    line += c == '\n'
                    i += 1
                    continue
            else:
                if text.startswith('/>', i) or c == '>':
                    if c == '>':
                        contexts[-1]['mode'] = 'text'
                        i += 1
                    else:
                        contexts.pop()
                        tokens.append(('punct', ')', line))
                        i += 2
                    continue
                if c in "'\"":
                    end = text.find(c, i + 1)
                    recovery = declarations[declaration_index] if declaration_index < len(declarations) else len(text)
                    if end < 0 or recovery < end:
                        tokens.append(('unresolved', 'unclosed JSX attribute', line))
                        end = recovery
                    else:
                        end += 1
                    line += text[i:end].count('\n')
                    i = end
                    continue
                if c != '{':
                    line += c == '\n'
                    i += 1
                    continue
            if c == '{':
                contexts.append({'mode': 'expr', 'depth': 0})
                tokens.append(('punct', '{', line))
                i += 1
                continue
        # İfade başlangıcı karşılaştırmalardan ayrıdır; .ts hiç JSX değildir.
        expression_start = not tokens or tokens[-1][0] in {'word', 'punct'} and tokens[-1][1] in REGEX_START - {'<', '>'}
        opening = JSX_OPENING.match(text, i) if jsx and c == '<' else None
        type_context = (tokens and tokens[-1][1] == ':') or type_statement or (type_scopes and type_scopes[-1])
        generic_arrow = (jsx and mode != 'text' and c == '<'
                         and (TS_GENERIC.match(text, i) or type_context and TS_CALLABLE.match(text, i))
                         and _generic_signature(text, i, call_signature=bool(type_scopes and type_scopes[-1])))
        if opening and not generic_arrow and (mode == 'text' or expression_start):
            contexts.append({'mode': 'tag', 'name': opening[1], 'start': i,
                             'line': line, 'token_start': len(tokens)})
            tokens.append(('punct', '(', line))
            i += len(opening[0])
            continue
        if text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith('/*', i):
            end = text.find('*/', i + 2)
            end = len(text) if end < 0 else end + 2
            line += text[i:end].count('\n')
            i = end
            continue
        if c == '/' and (not tokens or tokens[-1][0] in {'word', 'punct'} and tokens[-1][1] in REGEX_START):
            # İfade başlangıcındaki regex gövdesi de kod belirteci değildir.
            start, start_line, bracket, closed = i, line, False, False
            i += 1
            while i < len(text) and text[i] not in '\r\n':
                if text[i] == '\\':
                    i += 2 if i + 1 < len(text) and text[i + 1] not in '\r\n' else 1
                    continue
                if text[i] == '[':
                    bracket = True
                elif text[i] == ']':
                    bracket = False
                elif text[i] == '/' and not bracket:
                    i += 1
                    closed = True
                    break
                i += 1
            while closed and i < len(text) and text[i].isalpha():
                i += 1
            # Kesin olmayan tahmin sonraki satırdaki kodu yutmamalı.
            tokens.append(('regex' if closed else 'unresolved', '' if closed else text[start:i], start_line))
        elif c in "'\"`":
            start, quote, start_line = i, c, line
            i += 1
            value = ''
            while i < len(text) and text[i] != quote:
                if quote != '`' and text[i] in '\r\n':
                    break
                if text[i] == '\\' and i + 1 < len(text) and (quote == '`' or text[i + 1] not in '\r\n'):
                    i += 1
                value += text[i]
                i += 1
            closed = i < len(text) and text[i] == quote
            if closed:
                i += 1
            line += text[start:i].count('\n')
            kind = ('string' if quote != '`' else 'template') if closed else 'unresolved'
            tokens.append((kind, value if closed else text[start:i], start_line))
        elif c.isalpha() or c in '_$':
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] in '_$'):
                i += 1
            tokens.append(('word', text[start:i], line))
            if text[start:i] in {'type', 'interface'}:
                type_statement = True
        elif text[i:i + 2] in {'=>', '&&', '||', '++', '--'}:
            tokens.append(('punct', text[i:i + 2], line))
            i += 2
        else:
            if contexts and contexts[-1]['mode'] == 'expr':
                if c == '{':
                    contexts[-1]['depth'] += 1
                elif c == '}':
                    if contexts[-1]['depth'] == 0:
                        contexts.pop()
                    else:
                        contexts[-1]['depth'] -= 1
            if not c.isspace():
                tokens.append(('punct', c, line))
                if c == '{':
                    type_scopes.append(type_statement or bool(type_scopes and type_scopes[-1]))
                    type_statement = False
                elif c == '}':
                    if type_scopes:
                        type_scopes.pop()
                    type_statement = False
                elif c == ';':
                    type_statement = False
            line += c == '\n'
            i += 1
    if contexts:
        tokens.append(('unresolved', 'unclosed JSX', line))
    return tokens


JS_EXTENSIONS = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')


def _js_target(stem, available):
    stem = _posix_norm(stem)
    if stem in available:
        return stem
    suffix = PurePosixPath(stem).suffix
    substitutes = {'.js': ('.ts', '.tsx', '.mts'), '.mjs': ('.mts',), '.cjs': ('.cts',)}
    if suffix in substitutes:
        variants = [stem[:-len(suffix)] + ext for ext in substitutes[suffix]]
    else:
        variants = [stem + ext for ext in JS_EXTENSIONS]
        variants += [stem + '/index' + ext for ext in JS_EXTENSIONS]
    return next((p for p in variants if p in available), None)


def _json_config(file):
    # Dizgelerdeki // ve virgülleri koruyarak JSONC yorumlarını çıkar.
    text = file.read_text(encoding='utf-8-sig')
    text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text)
    text = re.sub(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])',
                  lambda m: m[0] if m[0].startswith('"') else '', text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('configuration must be an object')
    return value


def _paths_index(paths):
    exact, prefixes = {}, {}
    for pattern, targets in paths.items():
        if not isinstance(targets, list):
            continue
        if '*' not in pattern:
            exact[pattern] = targets
        elif pattern.count('*') == 1:
            prefix, suffix = pattern.split('*')
            node = prefixes
            for char in prefix:
                node = node.setdefault(char, {})
            node = node.setdefault(None, {})
            for char in reversed(suffix):
                node = node.setdefault(char, {})
            node[None] = (pattern, targets, len(suffix))
    return exact, prefixes


def _paths_match(index, value):
    exact, node = index
    if value in exact:
        return '', exact[value]
    best = None
    # İki karakter ağacı yalnız isteğin önek/son eklerini gezer;
    # eşleşmeyen binlerce alias her importta yeniden taranmaz.
    for length in range(len(value) + 1):
        suffixes = node.get(None, {})
        endings = [suffixes.get(None)]
        for char in reversed(value[length:]):
            if char not in suffixes:
                break
            suffixes = suffixes[char]
            endings.append(suffixes.get(None))
        for ending in endings:
            if ending is not None:
                pattern, targets, suffix_length = ending
                candidate = (-length, pattern, value[length:len(value) - suffix_length if suffix_length else None], targets)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
        if length == len(value) or value[length] not in node:
            break
        node = node[value[length]]
    return (best[2], best[3]) if best else None


class _JSResolver:
    """Depo metadata'sı bir kez okunur; dizin, config ve import sonuçları saklanır."""
    def __init__(self, root, available, metadata):
        self.root, self.available = root, available
        self.scannable = available
        self.packages, self.configs, self.nearest, self.results = {}, {}, {}, {}
        self.warnings, self.counts, self.reasons = [], {'local_package': 0, 'alias': 0}, {}
        self.metadata = set(metadata)
        for path in metadata:
            if PurePosixPath(path).name == 'package.json':
                manifest = self.read(path)
                name = manifest.get('name')
                if isinstance(name, str) and name:
                    self.packages.setdefault(name, (str(PurePosixPath(path).parent), manifest))

    def read(self, path):
        file = self.root / path
        if not file.resolve().is_relative_to(self.root) or _symlink_below(self.root, file):
            self.warnings.append({'file': path, 'reason': 'configuration outside regular repository files'})
            return {}
        try:
            return _json_config(file)
        except (OSError, ValueError) as exc:
            self.warnings.append({'file': path, 'reason': 'cannot read configuration', 'detail': str(exc)})
            return {}

    def config(self, path, active=None):
        if path in self.configs:
            return self.configs[path]
        active = set() if active is None else active
        if path in active:
            self.warnings.append({'file': path, 'reason': 'extends cycle skipped'})
            return {}
        active.add(path)
        data, options = self.read(path), {}
        parent = data.get('extends')
        if isinstance(parent, str):
            if parent.startswith('.'):
                target = _posix_norm(PurePosixPath(path).parent / parent)
                if not target.endswith('.json'):
                    target += '.json'
                options.update(self.config(target, active))
            else:
                self.warnings.append({'file': path, 'reason': 'package extends skipped', 'token': parent})
        compiler = data.get('compilerOptions', {})
        if isinstance(compiler, dict):
            for key, name in (('outDir', 'out_dir'), ('rootDir', 'root_dir')):
                if isinstance(compiler.get(key), str):
                    options[name] = _posix_norm(PurePosixPath(path).parent / compiler[key])
            base = compiler.get('baseUrl')
            if isinstance(base, str):
                options['base_url'] = str(PurePosixPath(path).parent / base)
            paths = compiler.get('paths')
            if isinstance(paths, dict):
                options['paths_index'] = _paths_index(paths)
                options['paths_origin'] = str(PurePosixPath(path).parent)
        options['config_origin'] = str(PurePosixPath(path).parent)
        active.remove(path)
        self.configs[path] = options
        return options

    def options(self, folder):
        if folder in self.nearest:
            return self.nearest[folder]
        config = str(PurePosixPath(folder) / 'tsconfig.json')
        if config in self.metadata:
            result = self.config(config)
        elif folder == '.':
            result = {}
        else:
            result = self.options(str(PurePosixPath(folder).parent))
        self.nearest[folder] = result
        return result

    def entry(self, value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ('types', 'import', 'default'):
                target = self.entry(value.get(key))
                if target is not None:
                    return target
        return None

    def source_entry(self, base, target):
        """Depoda olmayan derleme girişini yalnız mevcut TS kaynağına eşle."""
        entry = PurePosixPath(_posix_norm(PurePosixPath(base) / target))
        if (self.root / entry).exists():
            return None
        options = self.options(base)
        if 'out_dir' in options:
            output = PurePosixPath(options['out_dir'])
            source = PurePosixPath(options.get('root_dir',
                                             str(PurePosixPath(options['config_origin']) / 'src')))
        else:
            # Geleneksel dizinler ancak aşağıdaki kaynak üyeliğiyle doğrulanır.
            local = entry.relative_to(base) if entry.is_relative_to(base) else None
            if not local or local.parts[0] not in {'dist', 'build', 'lib'}:
                return None
            output, source = PurePosixPath(base) / local.parts[0], PurePosixPath(base) / 'src'
        if not entry.is_relative_to(output):
            return None
        relative = entry.relative_to(output).as_posix()
        suffix = next((ext for ext in ('.d.ts', '.js', '.mjs') if relative.endswith(ext)), None)
        if suffix is None:
            return None
        stem = str(source / relative[:-len(suffix)])
        candidates = [stem + ext for ext in ('.ts', '.tsx', '.mts')]
        candidates += [stem + '/index' + ext for ext in ('.ts', '.tsx', '.mts')]
        return next((path for path in candidates if path in self.scannable), None)

    def resolve(self, path, value):
        folder = str(PurePosixPath(path).parent)
        key = (folder, value)
        if key not in self.results:
            self.results[key] = self.find(folder, value)
        target, category, reason = self.results[key]
        if target not in self.available:
            target = None
        if target and category in self.counts:
            self.counts[category] += 1
        if reason:
            self.reasons[(path, value)] = reason
        return target, category

    def find(self, folder, value):
        if value.startswith('.'):
            return _js_target(str(PurePosixPath(folder) / value), self.scannable), 'relative', None
        options = self.options(folder)
        # Tam eşleşme önce gelir; yıldızlarda en uzun prefix kazanır.
        match = _paths_match(options.get('paths_index', ({}, {})), value)
        if match is not None:
            wildcard, targets = match
            base = options.get('base_url', options.get('paths_origin', folder))
            for target in targets:
                if isinstance(target, str):
                    found = _js_target(str(PurePosixPath(base) / target.replace('*', wildcard)), self.scannable)
                    if found:
                        return found, 'alias', None
            return None, 'alias', None
        if 'base_url' in options:
            found = _js_target(str(PurePosixPath(options['base_url']) / value), self.scannable)
            if found:
                return found, 'alias', None
        parts = value.split('/')
        name = '/'.join(parts[:2]) if value.startswith('@') else parts[0]
        if name not in self.packages:
            return None, 'external', None
        base, manifest = self.packages[name]
        subpath = value[len(name):].lstrip('/')
        if 'exports' in manifest:
            exports = manifest['exports']
            if isinstance(exports, dict) and any(k.startswith('.') for k in exports):
                exports = exports.get('./' + subpath if subpath else '.')
            elif subpath:
                exports = None
            target = self.entry(exports)
        elif subpath:
            target = './' + subpath
        else:
            target = next((manifest[k] for k in ('module', 'main', 'types') if isinstance(manifest.get(k), str)), None)
            if target is None:
                for stem in ('src/index', 'index'):
                    found = _js_target(str(PurePosixPath(base) / stem), self.scannable)
                    if found:
                        return found, 'local_package', None
        found = _js_target(str(PurePosixPath(base) / target), self.scannable) if target else None
        if not found and target:
            found = self.source_entry(base, target)
        return found, 'local_package', None if found else 'local package found, entry missing'


def _javascript(path, data, available, resolver=None):
    if resolver:
        resolver.options(str(PurePosixPath(path).parent))
    tokens = _tokens(data.decode('utf-8-sig'), jsx=Path(path).suffix in {'.tsx', '.jsx', '.js', '.mjs', '.cjs'})
    symbols, dependencies, unresolved, external = [], set(), [(line, value) for kind, value, line in tokens if kind == 'unresolved'], 0
    depth = 0
    def request(token, dynamic=False):
        nonlocal external
        kind, value, line = token
        if dynamic or kind != 'string':
            unresolved.append((line, value))
            return
        if resolver:
            target, category = resolver.resolve(path, value)
        else:
            category = 'relative' if value.startswith('.') else 'external'
            target = _js_target(str(PurePosixPath(path).parent / value), available) if category == 'relative' else None
        if target:
            dependencies.add(target)
        elif category == 'external':
            external += 1
        else:
            unresolved.append((line, value))
    for i, (kind, value, line) in enumerate(tokens):
        if kind != 'word':
            if kind == 'punct':
                depth += value == '{'
                depth -= value == '}'
            continue
        rest = tokens[i + 1: i + 5]
        if value == 'export' and depth == 0:
            j = i + 1
            while j < len(tokens) and tokens[j][1] in {'default', 'async', 'declare'}:
                j += 1
            if j + 1 < len(tokens) and tokens[j][1] in {'function', 'class', 'const'} and tokens[j + 1][0] == 'word':
                symbols.append((tokens[j + 1][1], tokens[j][1], line))
                if tokens[j][1] == 'const':
                    nesting = 0
                    for k in range(j + 2, len(tokens)):
                        token = tokens[k]
                        if nesting == 0 and token[1] in {';', 'export', 'import'}:
                            break
                        if token[0] == 'punct':
                            nesting += token[1] in {'(', '[', '{'}
                            nesting -= token[1] in {')', ']', '}'}
                            if token[1] == ',' and nesting == 0 and k + 1 < len(tokens) and tokens[k + 1][0] == 'word':
                                symbols.append((tokens[k + 1][1], 'const', tokens[k + 1][2]))
        if value not in {'import', 'export', 'require'} or (i and tokens[i - 1][1] == '.'):
            continue
        if rest and rest[0][1] == '(' and value != 'export':
            if len(rest) >= 3:
                request(rest[1], rest[2][1] != ')')
            continue
        if value == 'require':
            continue
        if value == 'export' and rest and rest[0][1] in {'function', 'class', 'const', 'default', 'async', 'declare', 'interface', 'type', 'enum', 'namespace'}:
            continue
        if rest and rest[0][0] == 'string' and value == 'import':
            request(rest[0])
            continue
        j = i + 1
        while j < len(tokens):
            if tokens[j][1] == ';' or (tokens[j][0] == 'word' and tokens[j][1] in {'import', 'export'}):
                break
            if tokens[j][0] == 'word' and tokens[j][1] == 'from':
                if j + 1 < len(tokens):
                    request(tokens[j + 1])
                break
            j += 1
    return symbols, dependencies, unresolved, external


def _property(kind, enum=None):
    return {'type': kind, 'required': True, 'enum': enum}


def _relation_type(name, target, impact):
    return {'id': name, 'label': name, 'from_type': 'code_module', 'to_type': target,
            'from_min': 0, 'from_max': None, 'to_min': 0, 'to_max': None, 'impact': impact}


def _review_candidates(old_objects, objects, old_relations, relations, operations):
    """Değişen modülün bir adım import komşuları ve testleri; bilgi amaçlıdır.

    İçe doğru komşular doğrudan import edenlerdir. Dışa doğru komşular,
    değişen modülün kullandığı bağımlılıklardır. Eski bağlar silmede de görünür.
    """
    modules = {key: obj['properties']['path']
               for key, obj in {**old_objects, **objects}.items()
               if obj['type'] == 'code_module'}
    changed = set()
    for op in operations:
        if op['op'] in {'add_object', 'replace_object'}:
            key = op['object']['id']
        elif op['op'] == 'remove_object':
            key = op['object_id']
        else:
            continue
        if key in modules:
            changed.add(key)
    importers, dependencies, tests = set(), set(), set()
    for relation in list(old_relations.values()) + list(relations.values()):
        source, target = relation['from'], relation['to']
        if relation['type'] == 'tests' and target in changed and source not in changed:
            tests.add(modules[source])
        if relation['type'] != 'imports':
            continue
        if target in changed and source not in changed:
            importers.add(modules[source])
        if source in changed and target not in changed:
            dependencies.add(modules[target])
    candidates = sorted(importers | dependencies | tests)
    return {'review_candidates': candidates, 'review_candidate_count': len(candidates),
            'direct_importers': sorted(importers), 'direct_importer_count': len(importers),
            'direct_dependencies': sorted(dependencies), 'direct_dependency_count': len(dependencies),
            'direct_tests': sorted(tests), 'direct_test_count': len(tests)}


def generate(root, state=None, *, include=(), exclude=(), max_files=10000):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('graph root must be a regular directory')
    state = state or {'schema_version': 3, 'ontology': {'object_types': [], 'relation_types': []}, 'objects': [], 'relations': [], 'history': []}
    if state['schema_version'] != 3:
        raise ValueError('graph requires schema 3')
    if max_files < 0:
        raise ValueError('max-files must be nonnegative')
    # Eski tip sözleşmesi kaynak farkına karışmaz; önce ayrı göç uygulanır.
    migration_event, migration_objects, migration_tasks = None, 0, 0
    migrated_ontology = copy.deepcopy(state['ontology'])
    directions = {'imports': 'none', 'tests': 'forward', 'defines': 'none'}
    for definition in migrated_ontology['relation_types']:
        if definition['id'] in directions:
            definition['impact'] = directions[definition['id']]
    if migrated_ontology != state['ontology']:
        import core
        migration_event = {'action': 'sync_code_graph', 'actor': 'deterministic-code-graph',
                           'reason': 'Migrate code graph relation types',
                           'operations': [{'op': 'replace_ontology', 'ontology': migrated_ontology}]}
        preview = core.preview_event(state, migration_event, root)
        migration_objects = len(preview['impact']['affected_objects'])
        migration_tasks = len(preview['impact']['affected_tasks'])
        # Olay biçimi ontoloji önkoşulunu desteklemiyor. Göç uygulanmadan
        # kaynak olayı vermek eski yayılımı sessizce geri getirir.
        return None, {'migration_event': migration_event,
                      'migration_object_count': migration_objects,
                      'migration_task_count': migration_tasks,
                      'migration_summary': f'göç: {migration_objects} nesne / {migration_tasks} görev yeniden doğrulanacak',
                      'next_step': 'önce göç olayını uygula, sonra graph\'ı yeniden koş'}
    scanned = _files(root, (), (), None, metadata=True)
    scannable = {p for p in scanned if PurePosixPath(p).suffix in EXTENSIONS}
    resolver = _JSResolver(root, scannable, [p for p in scanned if p not in scannable])
    paths = sorted(p for p in scannable if _selected(p, include, exclude))
    if len(paths) > max_files:
        raise ValueError(f'max-files exceeded: {len(paths)} > {max_files}')
    # Yalnız seçim filtresiyle dışarıda kalan taranabilir hedefler korunur.
    # Yok sayılan, silinen veya sembolik bağa dönen modüller çözüme katılmaz.
    existing_modules = {o['properties']['path']: o['id'] for o in state['objects']
                        if o['type'] == 'code_module' and o['properties']['path'] in scannable
                        and not _selected(o['properties']['path'], include, exclude)}
    available = set(existing_modules) | set(paths)
    resolver.available = available
    ontology = copy.deepcopy(state['ontology'])
    definitions = [
        {'id': 'code_module', 'label': 'Code module', 'properties': {'path': _property('file'), 'language': _property('string'), 'sha256': _property('string')}},
        {'id': 'code_symbol', 'label': 'Code symbol', 'properties': {'module': _property('string'), 'name': _property('string'), 'kind': _property('string', ['function', 'class', 'const']), 'line': _property('integer')}}]
    for definition in definitions:
        if not any(t['id'] == definition['id'] for t in ontology['object_types']):
            ontology['object_types'].append(definition)
    relation_types = [_relation_type('imports', 'code_module', 'none'), _relation_type('tests', 'code_module', 'forward'), _relation_type('defines', 'code_symbol', 'none')]
    old_objects = {o['id']: o for o in state['objects']}
    old_relations = {r['id']: r for r in state['relations']}
    retired = set()
    for entry in state['history']:
        change = entry.get('change', {})
        for collection in ('objects', 'relations'):
            retired.update(x['id'] for x in change.get('before_graph', {}).get(collection, []))
        for op in change.get('operations', []):
            if op['op'] in {'add_object', 'add_relation'}:
                retired.add(op.get('object', op.get('relation'))['id'])
    def identifier(kind, key, current):
        base = PREFIX + kind + ':' + hashlib.sha256(key.encode()).hexdigest()
        n, candidate = 0, base
        while candidate in retired and candidate not in current:
            n += 1
            candidate = base + ':' + str(n)
        return candidate
    modules = dict(existing_modules)
    modules.update({p: identifier('module', p, old_objects) for p in paths})
    objects, relations, unresolved, external = {}, {}, [], 0
    def link(source, kind, target):
        key = source + '\0' + kind + '\0' + target
        rid = identifier(kind, key, old_relations)
        relations[rid] = {'id': rid, 'from': source, 'type': kind, 'to': target}
    for path in paths:
        data = (root / path).read_bytes()
        mid = modules[path]
        objects[mid] = {'id': mid, 'type': 'code_module', 'label': path, 'properties': {'path': path, 'language': 'python' if path.endswith('.py') else 'typescript' if Path(path).suffix in {'.ts', '.tsx', '.mts', '.cts'} else 'javascript', 'sha256': hashlib.sha256(data).hexdigest()}}
        try:
            symbols, imports, missing, outside = (_python(path, data, available) if path.endswith('.py')
                                                  else _javascript(path, data, available, resolver))
        except (SyntaxError, UnicodeError) as exc:
            raise ValueError(f'Cannot parse {path}: {exc}') from exc
        for name, kind, line in symbols:
            sid = identifier('symbol', path + '\0' + kind + '\0' + name, old_objects)
            objects[sid] = {'id': sid, 'type': 'code_symbol', 'label': name, 'properties': {'module': mid, 'name': name, 'kind': kind, 'line': line}}
            link(mid, 'defines', sid)
        test = bool(re.search(r'(^test_.*\.py$|_test\.py$|\.(test|spec)\.)', Path(path).name))
        for target in sorted(imports):
            link(mid, 'imports', modules[target])
            if test:
                link(mid, 'tests', modules[target])
        external += outside
        unresolved.extend({'file': path, 'line': line, 'token': token,
                           **({'reason': resolver.reasons[(path, token)]} if (path, token) in resolver.reasons else {})}
                          for line, token in missing)
    file_properties = {t['id']: [key for key, spec in t.get('properties', {}).items() if spec.get('type') == 'file']
                       for t in state['ontology']['object_types']}
    selected_paths = set(paths)
    for obj in state['objects']:
        if obj['id'].startswith(PREFIX):
            continue
        properties = obj.get('properties', {})
        source = properties.get('source', {})
        sources = [source.get('path')] if isinstance(source, dict) else []
        sources.extend(properties.get(key) for key in file_properties.get(obj['type'], []))
        for path in sorted({p for p in sources if isinstance(p, str) and p in selected_paths}):
            kind = 'grounds' if obj['type'] == 'code_module' else 'grounds:' + obj['type']
            relation_types.append(_relation_type(kind, obj['type'], 'forward'))
            link(modules[path], kind, obj['id'])
    known_relation_types = {t['id'] for t in ontology['relation_types']}
    for definition in relation_types:
        if definition['id'] not in known_relation_types:
            ontology['relation_types'].append(definition)
            known_relation_types.add(definition['id'])
    managed_modules = {o['id'] for o in state['objects'] if o['id'].startswith(PREFIX + 'module:') and _selected(o['properties']['path'], include, exclude)}
    managed_objects = managed_modules | {o['id'] for o in state['objects'] if o['id'].startswith(PREFIX + 'symbol:') and o['properties']['module'] in managed_modules}
    removed_objects = managed_objects - objects.keys()
    managed_relations = {r['id'] for r in state['relations'] if r['id'].startswith(PREFIX)
                         and (r['from'] in managed_objects or r['to'] in removed_objects)}
    operations = []
    if ontology != state['ontology']:
        operations.append({'op': 'replace_ontology', 'ontology': ontology})
    for key in sorted(managed_relations - relations.keys()):
        operations.append({'op': 'remove_relation', 'relation_id': key})
    for key in sorted(managed_objects - objects.keys()):
        operations.append({'op': 'remove_object', 'object_id': key})
    for key, obj in sorted(objects.items()):
        if old_objects.get(key) != obj:
            operations.append({'op': 'replace_object' if key in old_objects else 'add_object', 'object': obj})
    for key, relation in sorted(relations.items()):
        if key not in old_relations:
            operations.append({'op': 'add_relation', 'relation': relation})
    event = {'action': 'sync_code_graph', 'actor': 'deterministic-code-graph', 'reason': 'Reconcile code sources', 'operations': operations}
    review = _review_candidates(old_objects, objects, old_relations, relations, operations)
    return event, {**review, 'migration_event': migration_event,
                   'migration_object_count': migration_objects, 'migration_task_count': migration_tasks,
                   'migration_summary': f'göç: {migration_objects} nesne / {migration_tasks} görev yeniden doğrulanacak',
                   'modules': len(paths), 'symbols': len(objects) - len(paths), 'relations': len(relations), 'external': external, **resolver.counts, 'unresolved_count': len(unresolved),
                   'config_warnings': sorted(resolver.warnings, key=lambda x: (x['file'], x['reason'], x.get('token', ''))),
                   'unresolved': sorted(unresolved, key=lambda x: (x['file'], x['line'], x['token']))}


def command(args):
    import project
    root = project.root_path(args.root)
    state = project.load_project(root) if (root / '.project').exists() else None
    event, report = generate(root, state, include=args.include, exclude=args.exclude, max_files=args.max_files)
    output = Path(os.path.abspath(args.out))
    if output.is_relative_to(root) and '.project' in output.relative_to(root).parts:
        raise ValueError('graph output must be outside .project')
    if event is None:
        migration_output = Path(str(output) + '.migration.json')
        # Eski kaynak çıktısını da bırakma; bu koşuda yalnız göç uygulanabilir.
        project.optional_file(output)
        project.atomic_write(migration_output, project.json_text(report['migration_event']))
        if output.exists():
            output.unlink()
        project.emit({'ok': False, 'out': None, 'migration_out': str(migration_output), **report})
        return 1
    project.atomic_write(output, project.json_text(event))
    project.emit({'ok': True, 'out': str(output), **report})
    return 0
