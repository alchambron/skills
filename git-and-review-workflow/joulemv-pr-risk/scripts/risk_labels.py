#!/usr/bin/env python3
"""Apply a pinned deployment-risk plan. Read-only dry-run by default."""
import argparse
import hashlib
import json
import re
import subprocess

LABELS = {
    'risk:limited': ('0e8a16', 'Limited deployment impact: cosmetic or developer-only changes with easy recovery.'),
    'risk:disruptive': ('fbca04', 'Disruptive deployment impact: recoverable page, workflow, or calculation failures.'),
    'risk:critical': ('d73a4a', 'Critical impact: production migration, material data loss, or severe security/tenant risk.'),
}
MANAGED = set(LABELS)
MAX_PRS = 500
MISSING = object()
PR_KEYS = {'number', 'head_sha', 'base_sha', 'metadata_sha256', 'risk', 'reason',
           'failure', 'recovery', 'confidence', 'production_migration', 'file_count', 'evidence'}


class Failure(Exception):
    """A sanitized operational or validation failure."""


def validate(plan):
    if not isinstance(plan, dict) or set(plan) != {'repository', 'prs'}:
        raise Failure('Plan requires exactly repository and prs')
    repo = plan['repository']
    if (not isinstance(repo, str) or not re.fullmatch(
            r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}', repo)
            or repo.split('/')[-1] in {'.', '..'}):
        raise Failure('Invalid repository; expected owner/repository')
    if not isinstance(plan['prs'], list) or len(plan['prs']) > MAX_PRS:
        raise Failure(f'prs must be a list of at most {MAX_PRS} entries')
    seen = set()
    for pr in plan['prs']:
        if not isinstance(pr, dict) or set(pr) != PR_KEYS:
            raise Failure('Each PR must contain exactly the documented PR fields')
        number = pr['number']
        if type(number) is not int or number <= 0 or number in seen:
            raise Failure('PR numbers must be unique positive integers')
        seen.add(number)
        for key, length in [('head_sha', 40), ('base_sha', 40), ('metadata_sha256', 64)]:
            if not isinstance(pr[key], str) or not re.fullmatch(rf'[0-9a-fA-F]{{{length}}}', pr[key]):
                raise Failure(f'PR #{number}: invalid {key}; full {length}-character hex required')
        for key in ['risk', 'reason', 'failure', 'recovery', 'confidence']:
            if not isinstance(pr[key], str) or not pr[key].strip():
                raise Failure(f'PR #{number}: {key} must be a nonempty string')
        if pr['risk'] not in MANAGED or pr['confidence'] not in {'supported', 'provisional'}:
            raise Failure(f'PR #{number}: invalid risk or confidence')
        if type(pr['production_migration']) is not bool:
            raise Failure(f'PR #{number}: production_migration must be boolean')
        if pr['production_migration'] and pr['risk'] != 'risk:critical':
            raise Failure(f'PR #{number}: production migration requires risk:critical')
        if type(pr['file_count']) is not int or pr['file_count'] < 1:
            raise Failure(f'PR #{number}: file_count must be a positive integer')
        evidence = pr['evidence']
        if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 20
                or any(not isinstance(item, str) or not item.strip() for item in evidence)):
            raise Failure(f'PR #{number}: evidence requires 1 to 20 nonempty strings')


def metadata_digest(data):
    """Hash title/body only; labels never influence classification inputs."""
    metadata = {'title': data['title'], 'body': '' if data['body'] is None else data['body']}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def gh(args, *, missing_ok=False, json_output=True):
    """Bounded subprocess calls; errors never expose stdout/stderr or secrets."""
    try:
        result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise Failure('gh unavailable, timed out, or unreadable; outcome may be unknown; not retried') from None
    if result.returncode:
        if missing_ok and '(HTTP 404)' in result.stderr:
            return MISSING
        raise Failure(f'gh failed (exit {result.returncode}); outcome may be unknown; not retried')
    if not json_output:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Failure('gh returned invalid JSON; outcome may be unknown; not retried') from None


def state(repo, pr, *, audit=None):
    data = gh(['api', f'repos/{repo}/pulls/{pr["number"]}'])
    number = pr['number']
    if not isinstance(data, dict):
        raise Failure(f'PR #{number}: invalid pull response')
    labels = data.get('labels')
    if (not isinstance(labels, list) or any(not isinstance(item, dict)
            or not isinstance(item.get('name'), str) or not item['name'].strip() for item in labels)):
        raise Failure(f'PR #{number}: invalid labels response')
    names = {item['name'] for item in labels}
    if len(names) != len(labels):
        raise Failure(f'PR #{number}: duplicate labels response')
    if audit is not None:
        audit['after'] = sorted(names)  # Retain observations even if another postwrite pin fails.
    if data.get('state') != 'open':
        raise Failure(f'PR #{number}: closed or unavailable')
    for key in ['head', 'base']:
        ref = data.get(key)
        if (not isinstance(ref, dict) or not isinstance(ref.get('sha'), str)
                or ref['sha'].lower() != pr[f'{key}_sha'].lower()):
            raise Failure(f'PR #{number}: stale {key} SHA')
    if (not isinstance(data.get('title'), str) or 'body' not in data
            or not (data['body'] is None or isinstance(data['body'], str))):
        raise Failure(f'PR #{number}: invalid metadata response')
    if metadata_digest(data) != pr['metadata_sha256'].lower():
        raise Failure(f'PR #{number}: stale metadata digest; reclassify')
    if type(data.get('changed_files')) is not int or data['changed_files'] != pr['file_count']:
        raise Failure(f'PR #{number}: changed file count mismatch')
    return names


def change(entry, names):
    entry.update(before=sorted(names), add=[] if entry['target'] in names else [entry['target']],
                 remove=sorted((names & MANAGED) - {entry['target']}))
    return bool(entry['add'] or entry['remove'])


def report(apply):
    return dict(mode='apply' if apply else 'dry-run', status='ok', repository=None,
                created_labels=[], prs=[], failures=[])


def run_plan(plan, apply=False):
    summary = report(apply)
    active, phase = None, 'validation'
    try:
        validate(plan)  # Validate every entry before even reading remote state.
        repo = summary['repository'] = plan['repository']
        summary['prs'] = [dict(pr, target=pr['risk'], before=None, after=None,
                               add=[], remove=[], status='not_attempted') for pr in plan['prs']]
        phase = 'preflight'
        for pr, entry in zip(plan['prs'], summary['prs']):
            active = entry
            change(entry, state(repo, pr))
            entry['status'] = 'planned'
        if not apply:
            return summary
        for pr, entry in zip(plan['prs'], summary['prs']):
            active, phase = entry, 'refresh'
            if not change(entry, state(repo, pr)):
                entry.update(status='unchanged', after=entry['before'])
                continue
            target = entry['target']
            phase = 'label_definition'
            existing = gh(['api', f'repos/{repo}/labels/{target}'], missing_ok=True)
            if existing is not MISSING and (not isinstance(existing, dict) or existing.get('name') != target):
                raise Failure('Exact label definition unavailable; existing definition left unchanged')
            # A definition read is not a PR freshness check. Refresh just before any write.
            phase = 'refresh_before_definition'
            needed = change(entry, state(repo, pr))
            if needed and existing is MISSING:
                phase = 'create_label'
                color, description = LABELS[target]
                created = gh(['api', '--method', 'POST', f'repos/{repo}/labels',
                              '-f', f'name={target}', '-f', f'color={color}', '-f', f'description={description}'])
                if not isinstance(created, dict) or created.get('name') != target:
                    raise Failure('Label creation outcome unverified; not retried')
                summary['created_labels'].append(target)
            # Also refresh after definition creation, immediately before the PR edit.
            phase = 'refresh_before_edit'
            names = state(repo, pr)
            if not change(entry, names):
                entry.update(status='unchanged', after=entry['before'])
                continue
            args = ['pr', 'edit', str(pr['number']), '--repo', repo]
            if entry['add']:
                args += ['--add-label', target]
            if entry['remove']:
                args += ['--remove-label', ','.join(entry['remove'])]
            phase, entry['status'] = 'edit', 'edit_attempted'
            gh(args, json_output=False)
            phase = 'postwrite_verification'
            after = state(repo, pr, audit=entry)
            if after & MANAGED != {target} or after - MANAGED != names - MANAGED:
                raise Failure(f'PR #{pr["number"]}: postwrite label verification failed; not retried')
            entry['status'] = 'applied'
        active = None
    except Failure as exc:
        summary.update(status='error', error=str(exc))
        summary['failures'].append(dict(number=active['number'] if active else None,
                                        phase=phase, error=str(exc)))
        if active is not None:
            active['status'] = ('failed_or_unverified' if phase in {
                'create_label', 'edit', 'postwrite_verification', 'refresh_before_edit'} else 'blocked')
        for entry in summary['prs']:
            if entry['status'] == 'planned':
                entry['status'] = 'not_attempted'
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, help='Verified JSON plan (maximum 500 PRs)')
    parser.add_argument('--apply', action='store_true', help='Perform writes; default only reads GitHub')
    args = parser.parse_args(argv)
    try:
        with open(args.plan, encoding='utf-8') as source:
            result = run_plan(json.load(source), args.apply)
    except (OSError, ValueError):
        result = report(args.apply)
        result.update(status='error', error='Cannot read JSON plan')
        result['failures'].append(dict(number=None, phase='plan_read', error=result['error']))
    print(json.dumps(result, indent=2))
    return int(result['status'] != 'ok')


if __name__ == '__main__':
    raise SystemExit(main())
