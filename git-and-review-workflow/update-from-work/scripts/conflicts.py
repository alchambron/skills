"""Validate conflict evidence for the pre-push decision and morning report.

Semantic coherence is established by the agent, not inferred from Git status.
Each verification has command, result (passed/failed/unavailable/inapplicable),
reason for non-passes, and an evidence path. Essential checks must pass.
"""
from copy import deepcopy


def resolution_report(number, conflicted_files, resolutions, verification, abort_reason=''):
    reasons = []
    if abort_reason:
        reasons.append(abort_reason)
    if not conflicted_files or {r.get('file') for r in resolutions} != set(conflicted_files):
        reasons.append('Missing conflict file evidence')
    if len(resolutions) != len(set(conflicted_files)):
        reasons.append('Duplicate or missing resolution records')
    for item in resolutions:
        if (item.get('method') not in {'kept ours', 'kept theirs', 'combined', 'regenerated'}
                or not item.get('reason') or item.get('coherent') is not True):
            reasons.append('Unverified coherent resolution: ' + str(item.get('file')))
        if item.get('method') == 'regenerated' and not item.get('command'):
            reasons.append('Missing regeneration command: ' + str(item.get('file')))
    if not verification:
        reasons.append('Missing verification')
    for check in verification:
        result = check.get('result')
        if (not check.get('command') or not check.get('evidence')
                or result not in {'passed', 'failed', 'unavailable', 'inapplicable'}
                or result == 'failed'
                or (result != 'passed' and (check.get('essential', True) or not check.get('reason')))):
            reasons.append('Verification incomplete or failed: ' + str(check.get('command')))
    return deepcopy(dict(number=number, decision='abort' if reasons else 'push',
                         resolutions=resolutions, verification=verification,
                         abort_reason='; '.join(reasons)))
