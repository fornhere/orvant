"""Compare legacy proposal semantics while excluding w109's additive metadata."""
import copy


def legacy_report(report):
    report = copy.deepcopy(report)
    for proposal in report['proposals']:
        for field in ('verification', 'write_scope', 'truncated'):
            proposal.pop(field, None)
    return report
