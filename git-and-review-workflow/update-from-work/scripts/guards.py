"""Pure fail-closed guards for the unattended workflow; performs no I/O."""
MAINTENANCE_EVENTS = {'schedule'}
DISPATCH_EVENTS = {'workflow_dispatch', 'repository_dispatch'}
RED = {'failure', 'error', 'cancelled', 'timed_out', 'action_required',
       'startup_failure', 'stale'}
OPTIONAL = {'neutral', 'skipped'}


def push_allowed(pr, original, current, contains_original):
    return (pr.get('author') == 'alchambron'
            and pr.get('repo') == 'EnerZam/JouleMV'
            and pr.get('head_repo') == 'EnerZam/JouleMV'
            and pr.get('state') == 'OPEN' and not pr.get('draft', True)
            and bool(pr.get('branch')) and pr['branch'] != 'work'
            and bool(original) and original == current and contains_original)


def excluded(event, maintenance):
    """Exclude schedules always; dispatches only when verified maintenance.

    A dispatch counts as maintenance only when its workflow file at the pinned
    SHA declares no push, pull_request or merge_group trigger. A manual run of
    a CI workflow, or a dispatch whose workflow could not be read, is retained.
    Unknown event provenance (None) is retained, so it still gates fail-closed.
    """
    return event in MAINTENANCE_EVENTS or (event in DISPATCH_EVENTS and maintenance is True)


def state(record):
    """Map one raw check run or legacy status context to green/red/pending."""
    status = record.get('status', 'completed')
    value = record.get('conclusion') or record.get('state')
    if status != 'completed' or value in {None, 'pending', 'queued', 'in_progress',
                                          'waiting', 'requested', 'expected'}:
        return 'pending'
    if value == 'success':
        return 'green'
    if value in RED:
        return 'red'
    if value in OPTIONAL and record.get('optional_path_gated') is True:
        return 'optional'
    return 'pending' if value in OPTIONAL else 'unknown'


def normalize_checks(records):
    """Filter by event, then collapse to the newest attempt per identity.

    Each record has identity, kind (check, status or combined), event (Actions
    event or None when unknown/not an Actions run), maintenance (verified
    dispatch maintenance), status/conclusion or state, and order (sortable
    newest-last attempt key). The combined commit status aggregate is not an
    individual check and never gates. Filtering precedes collapse, so a newer
    excluded scheduled run cannot replace an eligible push result. Verified
    optional path-gated neutral/skipped checks are dropped; required contexts
    must still be present for ci_state to report green.
    """
    newest = {}
    for record in records:
        if record.get('kind') == 'combined' or excluded(record.get('event'),
                                                        record.get('maintenance')):
            continue
        identity = record['identity']
        if identity not in newest or record['order'] >= newest[identity]['order']:
            newest[identity] = record
    result = {identity: state(record) for identity, record in newest.items()}
    return {identity: value for identity, value in result.items() if value != 'optional'}


def ci_state(checks, required, events=None, maintenance=frozenset()):
    """Judge normalized newest-attempt CI evidence.

    events maps check identities to their verified Actions triggering event;
    maintenance holds dispatch identities verified as maintenance (see
    excluded). Omitted/unknown provenance retains fail-closed behavior.
    Prefer normalize_checks, which filters before collapsing reruns.
    """
    if events is not None:
        checks = {name: value for name, value in checks.items()
                  if not excluded(events.get(name), name in maintenance)}
    if not checks or not set(required).issubset(checks):
        return 'unknown'
    if any(value == 'red' for value in checks.values()):
        return 'red'
    if any(value != 'green' for value in checks.values()):
        return 'pending'
    return 'green'
