"""Pure fail-closed guards for the unattended workflow; performs no I/O."""
def push_allowed(pr, original, current, contains_original):
    return (pr.get('author') == 'alchambron'
            and pr.get('repo') == 'EnerZam/JouleMV'
            and pr.get('head_repo') == 'EnerZam/JouleMV'
            and pr.get('state') == 'OPEN' and not pr.get('draft', True)
            and bool(pr.get('branch')) and pr['branch'] != 'work'
            and bool(original) and original == current and contains_original)


def ci_state(checks, required, events=None):
    """Judge normalized newest-attempt CI evidence.

    events maps check identities to their verified Actions triggering event.
    Omitted/unknown provenance retains the existing fail-closed behavior.
    Non-push evidence must never overwrite push evidence during normalization.
    """
    if events is not None:
        checks = {name: value for name, value in checks.items()
                  if events.get(name) not in {'schedule', 'workflow_dispatch',
                                              'repository_dispatch'}}
    if not checks or not set(required).issubset(checks):
        return 'unknown'
    if any(value == 'red' for value in checks.values()):
        return 'red'
    if any(value != 'green' for value in checks.values()):
        return 'pending'
    return 'green'
