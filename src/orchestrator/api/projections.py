"""Browser projections of durable workflow data.

Return copies: raw checkout evidence remains available to server-side assessment
and fingerprint verification, but must not travel with nested plan/step outputs.
"""
from __future__ import annotations


def public_workflow(value):
    if isinstance(value, dict):
        # Only the plan-approval model owns this private field. Workflow authors
        # may legitimately use evidence-related names in variables and outputs.
        plan = value.get('kind') == 'plan_approval'
        return {key: public_workflow(item)
                for key, item in value.items()
                if not (plan and key == 'checkout_evidence')}
    if isinstance(value, (list, tuple)):
        return [public_workflow(item) for item in value]
    return value
