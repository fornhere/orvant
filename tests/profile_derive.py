"""Isolated derive latency/RSS and optional cProfile, w102 synthetic generator."""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import resource
import tempfile
import time
from w107_fixtures import modules, record, add_code_graph
from derive_compat import legacy_report


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', action='store_true')
    parser.add_argument('--graph', action='store_true')
    parser.add_argument('--count', type=int, default=12000)
    parser.add_argument('--proposals', type=int, default=40)
    parser.add_argument('--profile')
    parser.add_argument('--output')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp, modules(args.reference) as loaded:
        root = Path(temp)
        state = record(root, loaded, args.count, args.proposals)
        if args.graph:
            add_code_graph(state, root)
            loaded['ontology'].invalidate(state)
        profiler = cProfile.Profile() if args.profile else None
        started = time.perf_counter()
        if profiler:
            profiler.enable()
        report = loaded['derive'].derive(state, root)
        if profiler:
            profiler.disable()
            with open(args.profile, 'w') as stream:
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(25)
        elapsed = time.perf_counter() - started
        digest_report = ({'derive': report, 'lanes': loaded['derive'].lanes(state, root)}
                         if args.graph else legacy_report(report))
        payload = json.dumps(digest_report, ensure_ascii=False).encode()
        result = {'seconds': elapsed, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'objects': len(state['objects']), 'relations': len(state['relations']),
                  'state_bytes': len(json.dumps(state, ensure_ascii=False).encode()),
                  'proposals': len(report['proposals']), 'sha256': hashlib.sha256(payload).hexdigest()}
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))


if __name__ == '__main__':
    run()
