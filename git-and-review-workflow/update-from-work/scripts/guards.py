"""Pure fail-closed guards for the unattended workflow; performs no I/O."""
def push_allowed(pr, original, current, contains_original):
    return (pr.get('author') == 'alchambron'
            and pr.get('repo') == 'EnerZam/JouleMV'
            and pr.get('head_repo') == 'EnerZam/JouleMV'
            and pr.get('state') == 'OPEN' and not pr.get('draft', True)
            and bool(pr.get('branch')) and pr['branch'] != 'work'
            and bool(original) and original == current and contains_original)


def ci_state(checks, required):
    """Input is normalized newest-attempt evidence, not raw GitHub responses."""
    if not checks or not set(required).issubset(checks):
        return 'unknown'
    if any(value == 'red' for value in checks.values()):
        return 'red'
    if any(value != 'green' for value in checks.values()):
        return 'pending'
    return 'green'
