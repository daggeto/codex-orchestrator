"""Browser projections of durable workflow data.

Return copies: raw checkout evidence remains available to server-side assessment
and fingerprint verification, but must not travel with nested plan/step outputs.
"""
from __future__ import annotations


def public_workflow(value):
    if isinstance(value, dict):
        # Evidence may also appear under previous/current in assessment outputs.
        if 'tracked_patch' in value or 'untracked_hashes' in value:
            return {}
        return {key: public_workflow(item) for key, item in value.items()
                if key not in {'checkout_evidence', 'tracked_patch', 'untracked_hashes'}}
    if isinstance(value, (list, tuple)):
        return [public_workflow(item) for item in value]
    return value
