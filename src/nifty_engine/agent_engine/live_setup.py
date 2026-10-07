"""Read-only live setup projection and receipt validation.

Only the separate owner-operated setup command installs an activation receipt.
Observer Start never commissions a broker order or changes environment/pause.
"""
from .contracts import identity

KEY = 'premium-execution-activation'


def activation(store, release, policy_hash, account_fingerprint=None):
    """Return current factual evidence, or None; a checkbox is not evidence."""
    value = store.meta(KEY, {})
    try:
        if (not isinstance(value, dict) or value.get('format') != 'trading-execution-activation-v2'
                or value.get('release') != release or value.get('policy_hash') != policy_hash
                or value.get('owner_approved') is not True or value.get('replay_verified') is not True):
            return None
        from .broker_commissioning import validated_evidence
        evidence = validated_evidence(store, value['plan_id'])
        binding = evidence['binding']
        if (binding['release'] != release or binding['policy_hash'] != policy_hash
                or binding['host'] != 'ORACLE' or binding['sdk_version'] != '1.5.0'
                or value.get('evidence_digest') != identity(evidence)
                or (account_fingerprint is not None and binding['account_fingerprint'] != account_fingerprint)):
            return None
        return evidence
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def receipt(evidence, now):
    """Called only after owner setup has rerun replay checks and live readbacks."""
    return dict(format='trading-execution-activation-v2', release=evidence['binding']['release'],
                policy_hash=evidence['binding']['policy_hash'], plan_id=evidence['plan_id'],
                evidence_digest=identity(evidence), owner_approved=True, replay_verified=True,
                installed_at=now.isoformat())


def public(store, gate):
    from .broker_commissioning import public_status
    evidence = activation(store, gate.release, gate.policy_hash, getattr(gate, 'account_fingerprint', None))
    return dict(format='trading-live-setup-v1',
                mode=gate.mode, paused=gate.pause_file.exists() or gate.pause_file.is_symlink(),
                provider_verified=evidence is not None,
                activation_installed=evidence is not None,
                commissioning=public_status(store, gate.release, gate.policy_hash),
                next_step='OWNER_START' if evidence and gate.mode == 'live' and not (
                    gate.pause_file.exists() or gate.pause_file.is_symlink()) else
                    'OWNER_LIVE_SETUP_REQUIRED', broker_writes=False)
