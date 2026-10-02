#!/usr/bin/env python3
"""Security-first merge priority and review attention; read-only and offline."""
import re

PRIORITY = ('Low', 'Medium', 'High', 'Urgent')
# Checkbox reports lead each entry with these so priority and attention read at a glance.
PRIORITY_DOT = {'Urgent': '🔴', 'High': '🟠', 'Medium': '🟡', 'Low': '⚪'}
ATTENTION_ICON = {'Critical': '🧨', 'Careful': '🔍', 'Standard': '📄', 'Light': '🪶'}
ATTENTION = ('Light', 'Standard', 'Careful', 'Critical')
LARGE_CODE_LINES = 2000
MAX_JEV_PATHS = 200
SECURITY_KINDS = ('security_fix', 'security_exposure')
SUBJECT = 'This pull request, as described by its title, description, labels, and changed paths.'


def icons(entry):
    """Priority dot then attention icon, e.g. 🔴🔍."""
    return PRIORITY_DOT[entry['priority']['level']] + ATTENTION_ICON[entry['attention']['level']]


# First match wins. Tests precede everything, so a security test stays Light;
# critical paths precede docs, so a production README still gets attention.
RULES = [
    (0, 'tests', re.compile(r'(^|/)src/test/|(^|/)__tests__/|(^|/)__fixtures__/|\.(test|spec|stories)\.[^/]+$')),
    (3, 'SQL migration', re.compile(r'^src/main/.*\.sql$|(^|/)db/migration/|flyway|^DockerfileDb$', re.I)),
    (3, 'production deploy', re.compile(r'(^|/)cicd/production/|^\.github/workflows/deploy[^/]*$')),
    (3, 'backend auth/security', re.compile(r'^src/main/java/(.*/security/|.*(Security|Jwt|Auth|Permission|Password|Credential|Encryption|Token|TenantAccess|Login)[^/]*\.java$)')),
    (0, 'skills and agent docs', re.compile(r'(^|/)\.(agents|claude|codex)/|(^|/)(AGENTS?|CLAUDE)\.md$')),
    (0, 'docs', re.compile(r'\.(md|mdx|txt|svg|png|jpe?g|gif)$|(^|/)docs/|(^|/)(LICENSE|NOTICE)$')),
    (0, 'translations', re.compile(r'(^|/)locales/')),
    (0, 'generated files', re.compile(r'(^|/)__generated__/|\.pyc$')),
    (0, 'editor and review config', re.compile(r'^\.(coderabbit\.yaml|macroscope/|idea/|vscode/|gitignore|graphifyignore)|\.iml$')),
    (2, 'frontend auth/permissions', re.compile(r'^frontend/src/.*(auth|permission|login|password|credential|session|token)', re.I)),
    (2, 'backend code', re.compile(r'^src/main/')),
    (2, 'dependencies', re.compile(r'(^|/)(pom\.xml|package(-lock)?\.json|\.npmrc)$')),
    # Non-production pipelines affect contributors, not deployed behavior.
    (1, 'CI/build pipeline', re.compile(r'^\.github/|(^|/)cicd/|(^|/)Dockerfile[^/]*$|^\.githooks/')),
    (1, 'frontend code', re.compile(r'^frontend/')),
    (1, 'scripts and tools', re.compile(r'^(scripts|tools|monitoring)/')),
]
ORDER = {label: index for index, (_, label, _) in enumerate(RULES)}
# Jev's 0.9/0.1 bands suit task ownership; for ranking, a coin-flip or better
# security reading is worth an agent check, and a lower one ranks as no.
POSSIBLE = 0.5
URGENT_LABEL = 'urgent ticket'
BUG = re.compile(r'^(fix|hotfix)(\(|!|:)|\bJMV-B-\d+', re.I)


def classify(path):
    for tier, label, pattern in RULES:
        if pattern.search(path):
            return tier, label
    return 1, 'other files'


def attention(pr):
    """Highest changed-file tier; a large runtime diff raises it one tier."""
    files = pr.get('files')
    if files is None:
        return {'level': None, 'reasons': ['changed files not collected'], 'code_lines': None, 'provisional': True}
    rated = [(classify(f['path']), f) for f in files]
    top = max((tier for (tier, _), _ in rated), default=0)
    reasons = sorted({label for (tier, label), _ in rated if tier == top}, key=ORDER.get)
    code_lines = sum((f.get('additions') or 0) + (f.get('deletions') or 0) for (tier, _), f in rated if tier)
    level = top
    if top in (1, 2) and code_lines >= LARGE_CODE_LINES:
        level += 1
        reasons.append(f'large diff, {code_lines} runtime lines')
    # GitHub caps listed files; an incomplete list can hide a higher tier.
    provisional = pr.get('changedFiles') is not None and len(files) < pr['changedFiles']
    return {'level': ATTENTION[level], 'reasons': reasons, 'code_lines': code_lines, 'provisional': provisional}


def priority_cases(pr):
    """Two Jev questions sharing one PR context: is it a security fix, and is the weakness live?"""
    files = pr.get('files')
    if files is None:
        return []
    author = pr.get('author') or {}
    ranked = sorted(files, key=lambda f: (-classify(f['path'])[0], f['path']))
    paths = [f['path'] for f in ranked[:MAX_JEV_PATHS]]
    body = (pr.get('description') or '').strip()
    event = {'id': f'pr-{pr["number"]}', 'actor': author.get('login', 'unknown'),
             'actor_type': author.get('__typename', 'Unknown'), 'at': pr.get('createdAt') or '',
             'body': pr['title'] + ('\n\n' + body if body else ''), 'url': pr['url']}
    context = {'head_sha': pr['headRefOid'], 'pr': {
        'number': pr['number'], 'base': pr['baseRefName'],
        'labels': sorted(l['name'] for l in (pr.get('labels') or {}).get('nodes', [])),
        'changed_files': pr.get('changedFiles', len(files)), 'paths_shown': len(paths), 'changed_paths': paths},
        'events': [event], 'context_complete': True}
    return [dict(context, id=f'{pr["number"]}:priority:{kind}', kind=kind, focal_id=event['id'], subject=SUBJECT)
            for kind in SECURITY_KINDS]


def priority(pr, merge_attention, judgments):
    def status(kind):
        judgment = judgments.get(f'{pr["number"]}:priority:{kind}', {})
        suggestion = judgment.get('suggestion', 'unavailable')
        if suggestion == 'uncertain':
            return 'uncertain' if judgment.get('probability_yes', 1) >= POSSIBLE else 'no'
        return suggestion
    fix, exposed = status('security_fix'), status('security_exposure')
    labels = [l['name'] for l in (pr.get('labels') or {}).get('nodes', [])]
    runtime = merge_attention['level'] not in (None, 'Light')
    notes = []
    if fix == 'yes' and exposed == 'yes':
        level, reason = 'Urgent', 'security fix, weakness live now'
    elif any(name.strip().lower() == URGENT_LABEL for name in labels):
        # The team's label is authoritative; no security reading can lower it.
        level, reason = 'Urgent', 'urgent ticket' + (', security fix' if fix == 'yes' else '')
    elif fix == 'yes':
        level, reason = 'High', 'security fix'
        if exposed != 'no':
            notes.append('verify whether the weakness is live')
    elif fix == 'uncertain':
        level, reason = 'Medium', 'possible security fix'
    elif BUG.search(pr['title']) and runtime:
        level, reason = 'Medium', 'bug fix'
    else:
        level, reason = 'Low', 'feature or maintenance' if runtime else 'docs, skills, or tests only'
    if fix == 'uncertain':
        notes.append('security effect unclear; verify')
    elif fix == 'unavailable':
        notes.append('security not assessed')
    return {'level': level, 'reason': reason, 'notes': notes, 'security_fix': fix, 'security_exposure': exposed}


def assess(snapshot, judgments):
    by_id = {j['id']: j for j in judgments}
    result = {}
    for pr in snapshot['prs']:
        merge_attention = attention(pr)
        result[pr['number']] = {'priority': priority(pr, merge_attention, by_id), 'attention': merge_attention}
    return result


def needs_verification(entry):
    p = entry['priority']
    return p['reason'].startswith('security fix') or p['security_fix'] == 'uncertain'


def summary(entry, markdown=True, level=True):
    """One line: Priority LEVEL (reason) · Attention LEVEL (reasons); level=False when a heading already names it."""
    bold = (lambda text: f'**{text}**') if markdown else (lambda text: text)
    p, a = entry['priority'], entry['attention']
    line = f'Priority {bold(p["level"])} ({p["reason"]})' if level else p['reason'][0].upper() + p['reason'][1:]
    if a['level']:
        line += f' · Attention {bold(a["level"])} ({", ".join(a["reasons"])})' + (' provisional' if a['provisional'] else '')
    else:
        line += ' · Attention unknown (changed files not collected)'
    return line + ''.join(f'; {note}' for note in p['notes']) + '.'
