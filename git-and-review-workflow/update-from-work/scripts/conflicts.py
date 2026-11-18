"""Validate conflict evidence before the merge commit and for the morning report.

Semantic coherence is established by the agent, not inferred from Git status.
Each resolution has file, method (kept ours/kept theirs/combined/regenerated),
reason, coherent (true only when both intents survive) and, for regenerated,
command. Each verification has command, result
(passed/failed/unavailable/inapplicable), essential (default true), reason for
non-passes and an evidence path. Pushing requires at least one essential check
that passed; essential checks cannot be skipped, and nonessential skips need a
reason. Report keys: number, decision (push/abort), conflicted_files,
resolutions, verification, abort_reason. conflicted_files is always the
original Git list, even when the attempt aborts before any resolution.
"""
from copy import deepcopy

METHODS = {'kept ours', 'kept theirs', 'combined', 'regenerated'}
RESULTS = {'passed', 'failed', 'unavailable', 'inapplicable'}


def resolution_report(number, conflicted_files, resolutions, verification, abort_reason=''):
    reasons = []
    if abort_reason:
        reasons.append(abort_reason)
    if not conflicted_files or {r.get('file') for r in resolutions} != set(conflicted_files):
        reasons.append('Missing conflict file evidence')
    if len(resolutions) != len(set(conflicted_files)):
        reasons.append('Duplicate or missing resolution records')
    for item in resolutions:
        if (item.get('method') not in METHODS
                or not item.get('reason') or item.get('coherent') is not True):
            reasons.append('Unverified coherent resolution: ' + str(item.get('file')))
        if item.get('method') == 'regenerated' and not item.get('command'):
            reasons.append('Missing regeneration command: ' + str(item.get('file')))
    if not any(check.get('essential', True) and check.get('result') == 'passed'
               and check.get('command') and check.get('evidence') for check in verification):
        reasons.append('Missing passed essential verification')
    for check in verification:
        result = check.get('result')
        if (not check.get('command') or not check.get('evidence')
                or result not in RESULTS or result == 'failed'
                or (result != 'passed' and (check.get('essential', True) or not check.get('reason')))):
            reasons.append('Verification incomplete or failed: ' + str(check.get('command')))
    return deepcopy(dict(number=number, decision='abort' if reasons else 'push',
                         conflicted_files=list(conflicted_files or []),
                         resolutions=resolutions, verification=verification,
                         abort_reason='; '.join(reasons)))
