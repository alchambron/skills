#!/usr/bin/env python3
"""Plan directly requested PR reviews across repositories and reserve shared Mac review slots."""
import argparse
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
import uuid

from queue_digest import DEFAULT_REPOSITORY, digest_date, export, review_result, write_atomic

LOGIN = 'alchambron'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / '.codex/review-state/review-queue'
REPORT = Path('report/scenario-guide.html')
SUMMARY = Path('report/summary.md')
SHA = re.compile(r'[0-9a-f]{40}')
COMPONENT = re.compile(r'[A-Za-z0-9_.-]+\Z')
REMOTE_GITHUB_RE = re.compile(r'(?:github\.com[:/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\Z', re.IGNORECASE)
DEFAULT_CHECKOUTS = {
    'EnerZam/JouleMV': '/Users/alchambron/Documents/Projects/Joule_MV/JouleMV',
    'EnerZam/WebSynco_React_Front': '/Users/alchambron/Documents/Projects/WebSynco/WebSynco_React_Front',
    'EnerZam/WebSynco': '/Users/alchambron/Documents/Projects/WebSynco/WebSynco',
}
SKIP_LIMIT = 200


def normalize_repo(value):
    if not isinstance(value, str):
        raise ValueError('repository must be an owner/name string')
    parts = value.strip().split('/')
    if len(parts) != 2 or any(not COMPONENT.fullmatch(part) or part in ('.', '..') for part in parts):
        raise ValueError(f'invalid repository identity: {value!r}')
    return f'{parts[0]}/{parts[1]}'


def pr_key(repository, number):
    return f'{normalize_repo(repository)}#{int(number)}'


def parse_pr_key(key):
    repo, sep, num = str(key).rpartition('#')
    if not sep or not num.isdigit():
        raise ValueError(f'invalid PR key: {key!r}')
    return normalize_repo(repo), int(num)


def repo_slug_parts(repository):
    owner, name = normalize_repo(repository).split('/')
    return owner, name


def suggested_run_dir(root, repository, number, head_sha):
    owner, name = repo_slug_parts(repository)
    return Path(root) / 'runs' / owner / name / f'pr-{int(number)}-{str(head_sha)[:8]}'


def load_slots(respond_queue):
    path = respond_queue.resolve() / 'scripts/shared_slots.py'
    spec = importlib.util.spec_from_file_location('review_queue_slots', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_review(path, respond_queue):
    scripts = str(path.resolve() / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import check_dependencies
    import history
    import skill_versions

    limits = skill_versions.requirements(ROOT, ('review-all', 'respond-queue'))
    for name, folder in (('review-all', path), ('respond-queue', respond_queue)):
        _, error = skill_versions.version_status(folder, limits[name])
        if error:
            raise ValueError(f'{name} at {folder}: {error}')
    skill_versions.parse_version(skill_versions.read_version(ROOT))
    result = check_dependencies.check(skill_dir=path)
    if not result['ok']:
        raise ValueError(check_dependencies.notice(result))
    return history


def load_renderer(review_all_path):
    scripts = str(Path(review_all_path).resolve() / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import render_results
    return render_results


def now():
    return dt.datetime.now(dt.timezone.utc)


def _extract_repo_from_project(project):
    identity = project.get('repositoryIdentity')
    if isinstance(identity, dict):
        display = identity.get('displayName')
        if isinstance(display, str) and '/' in display:
            try:
                return normalize_repo(display)
            except ValueError:
                pass
        owner, name = identity.get('owner'), identity.get('name')
        if isinstance(owner, str) and isinstance(name, str):
            try:
                return normalize_repo(f'{owner}/{name}')
            except ValueError:
                pass
        canonical = identity.get('canonicalKey')
        if isinstance(canonical, str):
            match = REMOTE_GITHUB_RE.search(canonical)
            if match:
                try:
                    return normalize_repo(f'{match.group(1)}/{match.group(2)}')
                except ValueError:
                    pass
        locator = identity.get('locator')
        if isinstance(locator, dict) and isinstance(locator.get('remoteUrl'), str):
            match = REMOTE_GITHUB_RE.search(locator['remoteUrl'])
            if match:
                try:
                    return normalize_repo(f'{match.group(1)}/{match.group(2)}')
                except ValueError:
                    pass
    return None


def _parse_t3_projects(payload):
    projects = payload.get('projects') if isinstance(payload, dict) else payload
    if not isinstance(projects, list):
        raise ValueError('T3 projects file must be a list or an object with a projects array')
    resolved = {}
    for project in projects:
        if not isinstance(project, dict) or project.get('deletedAt') or project.get('archivedAt'):
            continue
        workspace = project.get('workspaceRoot')
        if not isinstance(workspace, str) or not workspace.strip():
            continue
        repo = _extract_repo_from_project(project)
        if not repo:
            continue
        key = repo.casefold()
        if key not in resolved or (not Path(resolved[key]).is_dir() and Path(workspace).is_dir()):
            resolved[key] = workspace
    return resolved


def _parse_repos_config(payload):
    if not isinstance(payload, dict):
        raise ValueError('repos config must be a JSON object')
    raw = payload.get('repositories', payload)
    if not isinstance(raw, dict):
        raise ValueError('repos config repositories field must be a JSON object')
    resolved = {}
    for repo, path in raw.items():
        norm = normalize_repo(repo)
        if path is None or path == '':
            resolved[norm.casefold()] = None
        elif isinstance(path, str):
            resolved[norm.casefold()] = path
        else:
            raise ValueError(f'checkout path for {norm} must be a string or null')
    return resolved


def load_checkout_map(root, repos_config=None, t3_projects=None):
    """Resolve repo -> local checkout path from config map, T3 project list, or Air defaults."""
    explicit_config = repos_config or (Path(os.environ['REVIEW_QUEUE_REPOS_CONFIG'])
                                       if os.environ.get('REVIEW_QUEUE_REPOS_CONFIG') else None)
    state_config = Path(root) / 'repos.json'
    explicit_t3 = t3_projects or (Path(os.environ['REVIEW_QUEUE_T3_PROJECTS'])
                                  if os.environ.get('REVIEW_QUEUE_T3_PROJECTS') else None)

    mapping = {}
    used_source = False
    if explicit_t3 is not None:
        mapping.update(_parse_t3_projects(json.loads(Path(explicit_t3).read_text(encoding='utf-8'))))
        used_source = True
    if explicit_config is not None:
        mapping.update(_parse_repos_config(json.loads(Path(explicit_config).read_text(encoding='utf-8'))))
        used_source = True
    elif state_config.is_file():
        mapping.update(_parse_repos_config(json.loads(state_config.read_text(encoding='utf-8'))))
        used_source = True

    if not used_source:
        mapping = {repo.casefold(): path for repo, path in DEFAULT_CHECKOUTS.items()}
    return mapping


def resolve_checkout(repository, checkout_map):
    """Return (resolved_path, None) or (None, skip_reason). Never clones."""
    norm = normalize_repo(repository)
    raw = (checkout_map or {}).get(norm.casefold())
    if not raw:
        return None, f'no local checkout configured for {norm}; skipping without cloning'
    path = Path(raw).expanduser()
    if not path.is_dir() or not (path / '.git').exists():
        return None, f'configured local checkout missing or not a git repository for {norm}: {path}'
    return str(path.resolve()), None


def _job_repo(job):
    if isinstance(job.get('repository'), str) and job['repository'].strip():
        return normalize_repo(job['repository'])
    if isinstance(job.get('pr_key'), str) and '#' in job['pr_key']:
        return parse_pr_key(job['pr_key'])[0]
    return DEFAULT_REPOSITORY


def migrate_state(raw_state):
    """Idempotently migrate v1 single-repo state to v2 repo+number-keyed state without losing history."""
    if (not isinstance(raw_state, dict) or raw_state.get('version') not in (1, 2)
            or not isinstance(raw_state.get('jobs'), dict)
            or not isinstance(raw_state.get('skips', []), list)
            or not isinstance(raw_state.get('pending_digest_dates', []), list)
            or not isinstance(raw_state.get('prs', {}), dict)):
        raise ValueError('invalid review queue state')

    state = copy.deepcopy(raw_state)
    state.setdefault('skips', [])
    state.setdefault('pending_digest_dates', [])

    migrated_jobs = {}
    for token, raw_job in state['jobs'].items():
        if not isinstance(raw_job, dict):
            raise ValueError('invalid review queue state')
        job = dict(raw_job)
        repo = _job_repo(job)
        job['repository'] = repo
        if isinstance(job.get('number'), int):
            job['pr_key'] = pr_key(repo, job['number'])
            job.setdefault('url', f'https://github.com/{repo}/pull/{job["number"]}')
        result = job.get('result')
        if job.get('status') == 'finished' and isinstance(result, dict):
            result = dict(result)
            if (result.get('status') == 'completed' and result.get('html_path')
                    and not result.get('ping') and job.get('pins') and job.get('finished_at')):
                computed = review_result(token, job, result)
                result['ping'] = computed['ping']
                result.setdefault('pinged_at', job['finished_at'])
            job['result'] = result
        migrated_jobs[token] = job
    state['jobs'] = migrated_jobs

    migrated_skips = []
    for raw_skip in state['skips']:
        if not isinstance(raw_skip, dict):
            continue
        skip = dict(raw_skip)
        if skip.get('number') is not None:
            repo = _job_repo(skip)
            skip['repository'] = repo
            skip['pr_key'] = pr_key(repo, skip['number'])
        migrated_skips.append(skip)
    state['skips'] = migrated_skips[-SKIP_LIMIT:]

    migrated_prs = {}
    raw_prs = state.get('prs', {})
    # First preserve already-qualified repo#number entries.
    for key, value in raw_prs.items():
        if isinstance(key, str) and '#' in key and isinstance(value, dict):
            repo, num = parse_pr_key(key)
            canon = pr_key(repo, num)
            entry = dict(value)
            entry.setdefault('repository', repo)
            entry.setdefault('number', num)
            entry['pr_key'] = canon
            migrated_prs[canon] = entry
    # Then migrate legacy bare-number keys (JouleMV) without overwriting qualified entries.
    for key, value in raw_prs.items():
        if isinstance(key, str) and key.isdigit() and isinstance(value, dict):
            num = int(key)
            canon = pr_key(DEFAULT_REPOSITORY, num)
            if canon not in migrated_prs:
                entry = dict(value)
                entry['repository'] = entry.get('repository') or DEFAULT_REPOSITORY
                entry['number'] = entry.get('number') or num
                entry['pr_key'] = canon
                migrated_prs[canon] = entry

    # Backfill/sync per-PR state from durable jobs in chronological order.
    ordered_jobs = sorted(
        state['jobs'].items(),
        key=lambda pair: (pair[1].get('finished_at') or pair[1].get('started_at') or '', pair[0]),
    )
    for token, job in ordered_jobs:
        key = job.get('pr_key')
        if not key:
            continue
        entry = migrated_prs.setdefault(key, {
            'pr_key': key,
            'repository': job['repository'],
            'number': job['number'],
        })
        entry['last_token'] = token
        entry['last_job_status'] = job.get('status')
        if job.get('pins'):
            entry['last_pins'] = copy.deepcopy(job['pins'])
        result = job.get('result')
        if job.get('status') == 'finished' and isinstance(result, dict):
            entry['last_finished_at'] = job.get('finished_at')
            entry['last_outcome'] = result.get('status')
            entry['last_verdict'] = result.get('verdict')
            entry['last_publication'] = result.get('publication')
            entry['last_live'] = result.get('live')
            entry['last_html_path'] = result.get('html_path')
            if result.get('status') == 'completed' and result.get('ping') and job.get('pins'):
                if not (job.get('kind') == 'rerequested' and result.get('publication') == 'reused'
                        and not result.get('ping_alexis')):
                    entry['pinged_pins'] = copy.deepcopy(job['pins'])
                    entry['pinged_at'] = result.get('pinged_at') or job.get('finished_at')
                    entry['last_ping'] = result['ping']
                    entry['pinged_token'] = token
    state['prs'] = migrated_prs
    state['version'] = 2
    return state


def read_state(root):
    path = Path(root) / 'state.json'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raw = {'version': 2, 'jobs': {}, 'prs': {}, 'skips': [], 'pending_digest_dates': []}
    return migrate_state(raw)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2) + '\n')


def save_state(root, state):
    save_json(Path(root) / 'state.json', migrate_state(state))


def eligibility_reason(pr, expected_repo=None):
    if pr.get('state') != 'open':
        return 'PR is not open'
    if pr.get('draft'):
        return 'draft PR'
    reviewers = pr.get('requested_reviewers')
    if not isinstance(reviewers, list):
        raise ValueError('incomplete direct reviewer inventory')
    base_repo = ((pr.get('base') or {}).get('repo') or {}).get('full_name')
    if not isinstance(base_repo, str) or not base_repo.strip():
        raise ValueError('missing base repository on PR metadata')
    norm = normalize_repo(base_repo)
    if expected_repo is not None and norm.casefold() != normalize_repo(expected_repo).casefold():
        return f'PR repository {norm} does not match expected {expected_repo}'
    if LOGIN not in {user['login'].casefold() for user in reviewers if isinstance(user, dict) and user.get('login')}:
        return 'no current direct request on an open non-draft PR'
    return None


def eligible(pr, expected_repo=None):
    return eligibility_reason(pr, expected_repo) is None


def pins(pr):
    revision = {key: pr[key]['sha'] for key in ('head', 'base')}
    if any(not isinstance(sha, str) or not SHA.fullmatch(sha) for sha in revision.values()):
        raise ValueError('missing or invalid GitHub head/base pin')
    return revision


def fetch_pr(api, repository, number):
    repo = normalize_repo(repository)
    pr = api.get(f'repos/{repo}/pulls/{int(number)}')
    if pr['number'] != int(number):
        raise ValueError('GitHub returned a different PR number')
    return pr


def authenticated_api(history):
    api = history.GitHub()
    if api.get('user')['login'].casefold() != LOGIN:
        raise ValueError('authenticated account must be alchambron')
    return api


def _hit_identity(hit):
    pr_url = ((hit.get('pull_request') or {}).get('url') or hit.get('url') or '')
    if pr_url:
        components = urlparse(pr_url).path.strip('/').split('/')
        if components[:2] == ['api', 'v3']:
            components = components[2:]
        if len(components) == 5 and components[0] == 'repos' and components[3] in ('pulls', 'issues') and components[4].isdigit():
            return normalize_repo(f'{components[1]}/{components[2]}'), int(components[4])
    repo_url = hit.get('repository_url') or ''
    if repo_url and isinstance(hit.get('number'), int):
        components = urlparse(repo_url).path.strip('/').split('/')
        if components[:2] == ['api', 'v3']:
            components = components[2:]
        if len(components) == 3 and components[0] == 'repos':
            return normalize_repo(f'{components[1]}/{components[2]}'), hit['number']
    html_url = hit.get('html_url') or ''
    if html_url:
        components = urlparse(html_url).path.strip('/').split('/')
        if len(components) == 4 and components[2] == 'pull' and components[3].isdigit():
            return normalize_repo(f'{components[0]}/{components[1]}'), int(components[3])
    raise ValueError(f'search hit missing PR identity: {hit.get("html_url") or hit.get("url")!r}')


def search_open_requests(api, repository=None):
    query = f'is:pr is:open draft:false user-review-requested:{LOGIN}'
    if repository:
        query += f' repo:{normalize_repo(repository)}'
    hits, seen, warnings = [], set(), []
    search_complete = True
    received = 0
    total_count = 0
    for page in range(1, 11):
        payload = api.get('search/issues', {'q': query, 'per_page': 100, 'page': page})
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise ValueError('incomplete GitHub search response')
        total_count = payload.get('total_count')
        if not isinstance(total_count, int):
            raise ValueError('GitHub search omitted total_count')
        if payload.get('incomplete_results'):
            search_complete = False
            warnings.append('GitHub marked search incomplete_results')
        items = payload['items']
        for item in items:
            if not isinstance(item, dict):
                continue
            repo, num = _hit_identity(item)
            key = pr_key(repo, num).casefold()
            if key not in seen:
                seen.add(key)
                hits.append((repo, num, item.get('html_url')))
        received += len(items)
        if len(items) < 100 or received >= min(total_count, 1000):
            break
    if total_count > 1000:
        search_complete = False
        warnings.append('GitHub search exposes at most 1000 results; narrow with --repo')
    if received < min(total_count, 1000):
        search_complete = False
        warnings.append('GitHub search count exceeds received results')
    return hits, search_complete, warnings


def inventory(api, number=None, repository=None):
    if number is not None:
        repo = normalize_repo(repository or DEFAULT_REPOSITORY)
        return [fetch_pr(api, repo, number)], True, [], []
    hits, search_complete, warnings = search_open_requests(api, repository=repository)
    prs, fetch_skips = [], []
    for repo, num, url in hits:
        try:
            prs.append(fetch_pr(api, repo, num))
        except Exception as exc:
            key = pr_key(repo, num)
            warnings.append(f'{key}: {exc}')
            fetch_skips.append({
                'key': key,
                'repository': repo,
                'number': num,
                'url': url or f'https://github.com/{repo}/pull/{num}',
                'reason': f'failed to fetch PR metadata: {exc}',
            })
    return prs, search_complete, warnings, fetch_skips


def _lookup_prior(prior, key):
    prs = (prior or {}).get('prs') or {}
    if key in prs:
        return prs[key]
    lower = key.casefold()
    for candidate_key, entry in prs.items():
        if isinstance(candidate_key, str) and candidate_key.casefold() == lower:
            return entry
    return None


def evidence_field(row, section, key='status'):
    evidence = row.get(section)
    value = evidence.get(key) if isinstance(evidence, dict) else None
    return value if isinstance(value, str) else None


def has_finished_review_at_pins(previous_run, repository, number, revision):
    """True only when previous_run holds a finished review ledger and HTML report for this exact PR and head/base."""
    if not previous_run:
        return False
    run_path = Path(previous_run)
    report = run_path / REPORT
    try:
        if not report.is_file() or not report.stat().st_size:
            return False
    except OSError:
        return False
    norm_repo = normalize_repo(repository).casefold()
    for candidate in (run_path / 'results.json', report.with_name('results.json')):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
            prs = data.get('prs') if isinstance(data, dict) else None
            if not isinstance(prs, list):
                continue
        except (OSError, ValueError, TypeError):
            continue
        for pr in prs:
            if not isinstance(pr, dict):
                continue
            if str(pr.get('repository') or '').casefold() != norm_repo or pr.get('number') != number:
                continue
            if pr.get('head_sha') != revision['head'] or pr.get('base_sha') != revision['base']:
                continue
            review_status = evidence_field(pr, 'review')
            live_status = evidence_field(pr, 'live')
            pub_status = evidence_field(pr, 'publication')
            if (review_status in ('clean', 'issues')
                    and live_status in ('complete', 'not_required')
                    and pub_status in ('verified', 'reused', 'not_required', 'not_requested')):
                return True
    return False


def was_pinged_at_pins(state, key, revision):
    """True only when review-queue completed a review and emitted an Alexis ping at this exact head/base."""
    lower = key.casefold()
    matching_jobs = [
        job for job in state.get('jobs', {}).values()
        if isinstance(job, dict) and str(job.get('pr_key') or '').casefold() == lower
    ]
    if matching_jobs:
        last = matching_jobs[-1]
        if last.get('pins') == revision and (last.get('result') or {}).get('status') in ('blocked', 'failed'):
            return False
        for job in reversed(matching_jobs):
            if job.get('status') != 'finished' or job.get('pins') != revision:
                continue
            result = job.get('result') or {}
            if result.get('status') != 'completed' or not result.get('ping'):
                continue
            if (job.get('kind') == 'rerequested' and result.get('publication') == 'reused'
                    and not result.get('ping_alexis')):
                continue
            return True
        return False

    prs = state.get('prs') or {}
    for candidate_key, entry in prs.items():
        if isinstance(candidate_key, str) and candidate_key.casefold() == lower and isinstance(entry, dict):
            return entry.get('pinged_pins') == revision and bool(entry.get('last_ping'))
    return False


def _latest_attempt(state, key):
    lower = key.casefold()
    found = (None, {})
    for token, job in state.get('jobs', {}).items():
        if isinstance(job, dict) and str(job.get('pr_key') or '').casefold() == lower:
            found = (token, job)
    return found


def add_plan_skips(state, skipped):
    """Record each plan skip once in state['skips'] (deduplicated by PR, command, reason, pins)."""
    existing = list(state.get('skips', []))
    known = {
        (row.get('pr_key'), row.get('command'), row.get('reason'), row.get('head'), row.get('base'))
        for row in existing if isinstance(row, dict)
    }
    added = False
    timestamp = now().isoformat()
    for row in skipped:
        record = {
            'at': timestamp,
            'command': 'plan',
            'repository': row.get('repository'),
            'number': row.get('number'),
            'pr_key': row.get('key'),
            'url': row.get('url'),
            'head': row.get('head'),
            'base': row.get('base'),
            'reason': row['reason'],
        }
        identity = (record['pr_key'], record['command'], record['reason'], record['head'], record['base'])
        if identity in known:
            continue
        known.add(identity)
        existing.append(record)
        added = True
    state['skips'] = existing[-SKIP_LIMIT:]
    return added


def plan_reviews(api, history, prior, state, number=None, repository=None, checkout_map=None, root=None):
    selected, skipped, warnings = [], [], []
    prs, search_complete, inv_warnings, fetch_skips = inventory(api, number=number, repository=repository)
    warnings.extend(inv_warnings)
    skipped.extend(fetch_skips)
    active = {
        str(job.get('pr_key') or '').casefold()
        for job in state['jobs'].values()
        if job.get('status') == 'active'
    }
    expected_repo = normalize_repo(repository) if (number is not None and repository) else None
    for pr in prs:
        repo = normalize_repo(((pr.get('base') or {}).get('repo') or {}).get('full_name') or repository or DEFAULT_REPOSITORY)
        pr_number = pr['number']
        key = pr_key(repo, pr_number)
        item = {'key': key, 'number': pr_number, 'repository': repo, 'url': pr['html_url']}
        ineligible = eligibility_reason(pr, expected_repo=expected_repo)
        if ineligible is not None:
            skipped.append({**item, 'reason': ineligible})
            continue
        revision = pins(pr)
        if key.casefold() in active:
            skipped.append({**item, **revision, 'reason': 'resume existing reservation'})
            continue
        checkout, checkout_error = resolve_checkout(repo, checkout_map)
        if checkout_error is not None:
            skipped.append({**item, **revision, 'reason': checkout_error})
            continue
        entry = _lookup_prior(prior, key)
        kind, basis = history.classify(entry, revision['head'], False)
        if entry and entry.get('reviewedHead') and entry.get('reviewedBase') != revision['base']:
            kind, basis = 'moved', {'mode': 'full', 'previousRun': entry.get('runDir'),
                                    'reason': 'base_changed_or_unrecorded'}
        if kind == 'moved' and basis['mode'] == 'incremental':
            basis = history.carry_basis(api, repo, entry, revision['head'], basis, warnings)
        token, _ = _latest_attempt(state, key)
        if kind == 'awaiting_decision':
            previous_run = basis.get('previousRun')
            has_finished = has_finished_review_at_pins(previous_run, repo, pr_number, revision)
            pinged = was_pinged_at_pins(state, key, revision)
            if has_finished and pinged:
                skipped.append({**item, **revision, 'reason': 'awaiting Alexis decision at current pins'})
                continue
            if not has_finished:
                kind, basis = 'new', {'mode': 'full', 'reason': 'no_finished_review_at_current_pins'}
            else:
                basis = {**basis, 'reason': 'not_pinged_at_current_pins'}
        row = {
            **item,
            **revision,
            'kind': kind,
            'basis': basis,
            'previous_attempt': token,
            'checkout': checkout,
            'title': pr.get('title'),
            'updatedAt': pr.get('updated_at'),
            'createdAt': pr.get('created_at'),
        }
        if root is not None:
            row['run_dir_hint'] = str(suggested_run_dir(root, repo, pr_number, revision['head']))
        selected.append(row)
    selected.sort(key=lambda row: (history.PRIORITY[row['kind']], row['createdAt'] or '', row['repository'].casefold(), row['number']))
    return {'login': LOGIN, 'searchComplete': search_complete, 'selected': selected, 'skipped': skipped, 'warnings': warnings}


def recheck(api, repository, number, expected):
    repo = normalize_repo(repository)
    pr = fetch_pr(api, repo, number)
    reason = eligibility_reason(pr, expected_repo=repo)
    if reason is not None:
        raise ValueError('PR no longer directly requested, open and non-draft')
    if pins(pr) != expected:
        raise ValueError('head or base moved; replan the current revision')


def claim(root, slots, state, selection, run_dir, thread):
    repo = normalize_repo(selection['repository'])
    number = int(selection['number'])
    key = pr_key(repo, number)
    run_dir = str(run_dir.resolve())
    attempts = [
        (token, job) for token, job in state['jobs'].items()
        if str(job.get('pr_key') or '').casefold() == key.casefold()
    ]
    if (attempts[-1][0] if attempts else None) != selection.get('previous_attempt'):
        raise ValueError('selection predates a newer attempt; replan')
    jobs = [job for job in state['jobs'].values() if job['status'] == 'active']
    slots.check_capacity(root, 'review-queue', 'static', number, jobs, repository=repo)
    if any(job['run_dir'] == run_dir for job in state['jobs'].values()):
        raise ValueError('start each attempt in a new run directory; resume active reservations with their token')
    token = uuid.uuid4().hex
    state['jobs'][token] = {
        'repository': repo,
        'number': number,
        'pr_key': key,
        'url': selection.get('url') or f'https://github.com/{repo}/pull/{number}',
        'pins': {k: selection[k] for k in ('head', 'base')},
        'kind': selection.get('kind'),
        'basis_mode': (selection.get('basis') or {}).get('mode'),
        'checkout': selection.get('checkout'),
        'run_dir': run_dir,
        'thread': thread,
        'phase': 'static',
        'status': 'active',
        'started_at': now().isoformat(),
    }
    return token


def active_job(state, token):
    job = state['jobs'].get(token)
    if not job or job['status'] != 'active':
        raise ValueError('active review reservation required')
    return job


def defer(state, command, reason, number=None, token=None, repository=None):
    repo = normalize_repo(repository) if repository else None
    if token:
        job = state['jobs'].get(token)
        if job:
            number = job.get('number')
            repo = job.get('repository') or repo
            if job.get('status') == 'active':
                job['defer_reason'] = reason
    if number is not None and repo is None:
        repo = DEFAULT_REPOSITORY
    entry = {
        'at': now().isoformat(),
        'command': command,
        'reason': reason,
        'number': number,
    }
    if repo is not None:
        entry['repository'] = repo
    if repo is not None and number is not None:
        entry['pr_key'] = pr_key(repo, number)
    state['skips'].append(entry)
    state['skips'] = state['skips'][-SKIP_LIMIT:]


def read_result(path, job):
    try:
        (pr,) = json.loads(path.read_text(encoding='utf-8'))['prs']
        if str(pr['repository']).casefold() == _job_repo(job).casefold() and pr['number'] == job['number']:
            if all(pr.get(f'{key}_sha') in (None, sha) for key, sha in job['pins'].items()):
                return pr
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return {}


def run_result(job):
    run = Path(job['run_dir'])
    report = run / REPORT
    ledger = read_result(run / 'results.json', job)
    rendered = read_result(report.with_name('results.json'), job)
    try:
        if rendered and report.is_file() and report.stat().st_size:
            return ledger, rendered, str(report)
    except OSError:
        pass
    return ledger, {}, None


def _is_same_head_rerequest(job, pr):
    if job.get('kind') == 'rerequested' or job.get('basis_mode') == 'discussion':
        return True
    basis = pr.get('review_basis') if isinstance(pr, dict) else None
    return isinstance(basis, dict) and (basis.get('mode') == 'discussion' or basis.get('kind') == 'rerequested')


def finish(state, token, status, reason, cleanup, ping_alexis=False):
    job = state['jobs'].get(token)
    if not job:
        raise ValueError('unknown reservation token')
    if job.get('result'):
        return job
    if not cleanup:
        raise ValueError('verify worker and owned runtime cleanup before release')
    pr, rendered, report = run_result(job)
    verdict = evidence_field(pr, 'review') or 'pending'
    raw_live = evidence_field(pr, 'live')
    raw_publication = evidence_field(pr, 'publication')
    rendered_live = evidence_field(rendered, 'live', 'display_status')
    rendered_publication = evidence_field(rendered, 'publication', 'display_status')
    if status == 'completed':
        if (not report or verdict not in ('clean', 'issues') or raw_live not in ('complete', 'not_required')
                or raw_publication not in ('verified', 'reused', 'not_required', 'not_requested')
                or rendered_live != raw_live or rendered_publication != raw_publication
                or evidence_field(rendered, 'live', 'cleanup_status') == 'pending'
                or rendered.get('freshness') != 'current' or evidence_field(rendered, 'review') != verdict
                or not Path(report).with_name('inline-report.html').is_file()
                or pr.get('current_head_sha') != job['pins']['head']
                or pr.get('current_base_sha') != job['pins']['base']):
            raise ValueError('completion requires the matching fresh review-all ledger, settled publication and runtime, and HTML')
        if _is_same_head_rerequest(job, pr) and raw_publication == 'reused':
            if not ping_alexis:
                raise ValueError(
                    'same-head re-request cannot resolve silently as publication: reused; '
                    'post a fresh review or pass --ping-alexis with a --reason to ping Alexis'
                )
            if not reason or not reason.strip():
                raise ValueError('--ping-alexis requires a concrete --reason explaining why Alexis needs to decide')
    elif not reason or not reason.strip():
        raise ValueError('blocked and failed reviews require a reason')

    finished_at = now().isoformat()
    summary_file = Path(report).with_name('summary.md') if report else None
    summary_path = str(summary_file) if summary_file and summary_file.is_file() else None
    pub_obj = pr.get('publication') if isinstance(pr.get('publication'), dict) else {}
    rendered_pub = rendered.get('publication') if isinstance(rendered.get('publication'), dict) else {}
    review_url = pub_obj.get('review_url') or rendered_pub.get('review_url')

    result = {
        'status': status,
        'verdict': verdict,
        'html_path': report,
        'summary_path': summary_path,
        'review_url': review_url,
        'reason': reason,
        'live': rendered_live or raw_live,
        'publication': rendered_publication or raw_publication,
        'ping_alexis': bool(ping_alexis),
    }
    job.update(status='finished', finished_at=finished_at, result=result)
    computed = review_result(token, job, result)
    result['ping'] = computed['ping']
    if status == 'completed' or ping_alexis:
        result['pinged_at'] = finished_at
    job.pop('defer_reason', None)

    key = job['pr_key']
    prs = state.setdefault('prs', {})
    entry = prs.setdefault(key, {'pr_key': key, 'repository': job['repository'], 'number': job['number']})
    entry.update(
        last_token=token,
        last_job_status='finished',
        last_pins=copy.deepcopy(job['pins']),
        last_finished_at=finished_at,
        last_outcome=status,
        last_verdict=verdict,
        last_publication=result['publication'],
        last_live=result['live'],
        last_html_path=report,
    )
    if status == 'completed':
        entry.update(
            pinged_pins=copy.deepcopy(job['pins']),
            pinged_at=finished_at,
            last_ping=result['ping'],
            pinged_token=token,
        )
    return job


def export_digest(root, state, day):
    path = export(root, state, day)
    key = day.isoformat()
    if key in state['pending_digest_dates']:
        state['pending_digest_dates'].remove(key)
        save_state(root, state)
    return path


def finish_command(args, root, slots):
    status, reason = args.status, args.reason
    if status == 'completed' and args.cleanup_verified:
        with slots.locked(root):
            job = read_state(root)['jobs'].get(args.token)
        if job is None:
            raise ValueError('unknown reservation token')
        if not job.get('result'):
            api = authenticated_api(load_review(args.review_all, args.respond_queue))
            if pins(fetch_pr(api, job['repository'], job['number'])) != job['pins']:
                status, reason = 'blocked', 'head or base moved since review; replan the current revision'

    with slots.locked(root):
        state = read_state(root)
        job = finish(state, args.token, status, reason, args.cleanup_verified, ping_alexis=args.ping_alexis)
        day = digest_date(dt.datetime.fromisoformat(job['finished_at']))
        key = day.isoformat()
        if key not in state['pending_digest_dates']:
            state['pending_digest_dates'].append(key)
        save_state(root, state)
        path = export_digest(root, state, day)
        return {'digest': str(path), **review_result(args.token, job, job['result'])}


def render_inline_summary(root, review_all_path, token=None, run_dir=None):
    if run_dir is None:
        job = read_state(root)['jobs'].get(token)
        if not job:
            raise ValueError('unknown reservation token')
        run_dir = Path(job['run_dir'])
    else:
        run_dir = Path(run_dir).resolve()
    summary_file = run_dir / SUMMARY
    if summary_file.is_file():
        return summary_file.read_text(encoding='utf-8')
    renderer = load_renderer(review_all_path)
    report_dir = run_dir / 'report'
    prepared = report_dir / 'results.json'
    if prepared.is_file():
        data = json.loads(prepared.read_text(encoding='utf-8'))
        return renderer.render_markdown(data, report_dir)
    ledger = run_dir / 'results.json'
    if ledger.is_file():
        data = renderer.prepare(json.loads(ledger.read_text(encoding='utf-8')), run_dir, report_dir)
        return renderer.render_markdown(data, report_dir)
    raise ValueError(f'no review results found under {run_dir}')


def claim_selection(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    (selected,) = data['selected']
    if data['login'] != LOGIN or not data['searchComplete']:
        raise ValueError('claim requires a complete, single-PR queue selection')
    normalize_repo(selected['repository'])
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-all', type=Path, default=ROOT.parent / 'review-all')
    parser.add_argument('--respond-queue', type=Path, default=ROOT.parent / 'respond-queue')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--repo', help='Repository owner/name (optional filter on sweep; defaults to EnerZam/JouleMV with --number)')
    plan.add_argument('--number', type=int, help='Plan the PR named in a review-request event')
    plan.add_argument('--repos-config', type=Path, help='JSON file mapping owner/name to local checkout path')
    plan.add_argument('--t3-projects', type=Path, help='JSON file exported from t3_project_list for checkout resolution')
    plan.add_argument('--output', type=Path, required=True)
    start = commands.add_parser('claim')
    start.add_argument('--selection', type=Path, required=True)
    start.add_argument('--run-dir', type=Path, required=True)
    start.add_argument('--thread', required=True)
    start.add_argument('--host-ready', action='store_true')
    commands.add_parser('park').add_argument('--token', required=True)
    commands.add_parser('guard').add_argument('--token', required=True)
    live = commands.add_parser('claim-live')
    live.add_argument('--token', required=True)
    live.add_argument('--host-ready', action='store_true')
    release = commands.add_parser('finish')
    release.add_argument('--token', required=True)
    release.add_argument('--status', choices=('completed', 'blocked', 'failed'), required=True)
    release.add_argument('--reason')
    release.add_argument('--cleanup-verified', action='store_true')
    release.add_argument('--ping-alexis', action='store_true',
                         help='Emit an explicit Alexis decision ping on a same-head re-request when no fresh review was posted')
    wait = commands.add_parser('defer')
    wait.add_argument('--repo', help='Repository owner/name for --number')
    wait.add_argument('--number', type=int)
    wait.add_argument('--token')
    wait.add_argument('--reason', required=True)
    digest = commands.add_parser('digest').add_mutually_exclusive_group()
    digest.add_argument('--date', type=dt.date.fromisoformat)
    digest.add_argument('--retry-pending', action='store_true')
    summary_cmd = commands.add_parser('summary', help='Print inline Markdown review summary for a token or run dir')
    summary_group = summary_cmd.add_mutually_exclusive_group(required=True)
    summary_group.add_argument('--token')
    summary_group.add_argument('--run-dir', type=Path)
    commands.add_parser('migrate', help='Idempotently migrate state.json to v2 repo+number keys')
    args = parser.parse_args(argv)
    root = args.state.resolve()
    slots = None
    number = getattr(args, 'number', None)
    repository = getattr(args, 'repo', None)
    token = getattr(args, 'token', None)
    try:
        slots = load_slots(args.respond_queue)
        if args.command == 'finish':
            print(json.dumps(finish_command(args, root, slots)))
            return 0
        if args.command == 'summary':
            print(render_inline_summary(root, args.review_all, token=args.token, run_dir=args.run_dir), end='')
            return 0
        if args.command == 'migrate':
            with slots.locked(root):
                state = read_state(root)
                save_state(root, state)
            print(json.dumps({'version': state['version'], 'jobs': len(state['jobs']), 'prs': len(state['prs'])}))
            return 0
        if args.command in ('digest', 'defer', 'park'):
            with slots.locked(root):
                state = read_state(root)
                if args.command == 'digest':
                    if args.retry_pending:
                        exports, failures = [], {}
                        for key in list(state['pending_digest_dates']):
                            try:
                                exports.append(str(export_digest(root, state, dt.date.fromisoformat(key))))
                            except OSError as exc:
                                failures[key] = str(exc)
                        print(json.dumps({'exports': exports, 'failures': failures}))
                        return 2 if failures else 0
                    save_state(root, state)
                    print(export_digest(root, state, args.date or digest_date(now())))
                    return 0
                if args.command == 'park':
                    job = active_job(state, token)
                    if job['phase'] != 'static':
                        raise ValueError('only a static reservation can wait for runtime')
                    job['phase'] = 'waiting_live'
                else:
                    defer(state, 'defer', args.reason, number, token, repository=repository)
                save_state(root, state)
            return 0

        history = load_review(args.review_all, args.respond_queue)
        api = authenticated_api(history)
        if args.command == 'plan':
            checkout_map = load_checkout_map(root, repos_config=args.repos_config, t3_projects=args.t3_projects)
            with slots.locked(root):
                state = read_state(root)
            with history.locked() as directory:
                prior = history.read_state(directory)
            selection = plan_reviews(
                api, history, prior, state,
                number=number, repository=repository, checkout_map=checkout_map, root=root,
            )
            save_json(args.output, selection)
            with slots.locked(root):
                state = read_state(root)
                added = add_plan_skips(state, selection['skipped'])
                if added:
                    today = digest_date(now())
                    today_key = today.isoformat()
                    if today_key not in state['pending_digest_dates']:
                        state['pending_digest_dates'].append(today_key)
                save_state(root, state)
                if added:
                    try:
                        export_digest(root, state, today)
                    except OSError:
                        pass
            print(json.dumps({
                'selection': str(args.output),
                'count': len(selection['selected']),
                'skipped_count': len(selection['skipped']),
                'skipped': [{'key': s['key'], 'reason': s['reason']} for s in selection['skipped']],
            }))
            return 0

        if args.command in ('claim', 'claim-live') and not args.host_ready:
            raise ValueError('Mac busy, offline or availability unknown')
        if args.command == 'claim':
            selection = claim_selection(args.selection)
            number = selection['number']
            repository = selection['repository']
            recheck(api, repository, number, {key: selection[key] for key in ('head', 'base')})
            with slots.locked(root):
                state = read_state(root)
                token = claim(root, slots, state, selection, args.run_dir, args.thread)
                save_state(root, state)
            print(json.dumps({'token': token, 'repository': repository, 'pr_key': pr_key(repository, number)}))
            return 0

        with slots.locked(root):
            reservation = active_job(read_state(root), token)
        recheck(api, reservation['repository'], reservation['number'], reservation['pins'])
        with slots.locked(root):
            state = read_state(root)
            job = active_job(state, token)
            if job != reservation:
                raise ValueError('reservation changed during GitHub check; retry')
            if args.command == 'claim-live':
                if job['phase'] != 'waiting_live':
                    raise ValueError('park the static reservation before claiming runtime')
                peers = [other for key, other in state['jobs'].items() if key != token and other['status'] == 'active']
                slots.check_capacity(root, 'review-queue', 'live', job['number'], peers, repository=job['repository'])
                job['phase'] = 'live'
                job.pop('defer_reason', None)
                save_state(root, state)
            elif job['phase'] not in ('static', 'live'):
                raise ValueError('publication requires a static or live slot')
        print(json.dumps({
            'eligible': True,
            'repository': job['repository'],
            'pr_key': job['pr_key'],
            'number': job['number'],
            'phase': job['phase'],
            **job['pins'],
        }))
        return 0
    except Exception as exc:  # CLI boundary, including dependency and GitHub failures.
        if slots is not None:
            try:
                with slots.locked(root):
                    state = read_state(root)
                    defer(state, args.command, str(exc), number, token, repository=repository)
                    save_state(root, state)
            except (OSError, ValueError, TypeError):
                pass
        print(json.dumps({'status': 'deferred', 'command': args.command, 'repository': repository,
                          'number': number, 'token': token, 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
