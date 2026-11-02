"""Validated, independently launchable proposals saved with a scope checkpoint."""
from __future__ import annotations

import hashlib
import json
import re


SCOPE_PROPOSAL_INSTRUCTIONS = """When scope exceeds this workflow, propose a split before stopping.
Keep the useful subset in the requested workflow. Do not add optional features to
the required scope: editable labels, migrations and renaming existing Git resources
are separate proposals unless explicitly needed for the user's requested outcome.
Include a fenced scope-suggestions block containing a JSON array of 1 to 6 objects.
Each object must contain title, disposition (keep or separate), workflow, reason,
prompt, context, and required_inputs (an array of questions, empty when ready).
Use workflow=current for the subset that fits the requested workflow. For separate
tasks choose small-change, ui-small, backend-feature, or feature as appropriate.
Explain which rule the separate task exceeds. Include at least one separate task;
include a keep task whenever a useful subset can be done within the current rules.
Prompts must stand alone: state the outcome, boundaries, acceptance criteria,
relevant files/contracts, and validation restrictions. Context must explain known
decisions, existing changes, and dependencies. Never assume another proposal has
already run; ask for any prerequisite result as a required input. Do not invent
missing decisions. The app supplies repository, checkout, source task and attachments.
These are proposals only: do not implement even the smaller subset automatically.
Place the block before the final standalone SCOPE_ESCALATION line."""


def proposals(value, workflow):
    """Reject malformed model output as a whole; never silently launch a partial split."""
    if workflow not in {'small-change', 'ui-small'} or not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise ValueError('Expected 1 to 6 scope proposals for a small workflow')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Invalid scope proposal')
        clean = {}
        for field in ('title', 'disposition', 'workflow', 'reason', 'prompt', 'context'):
            text = item.get(field)
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError('Scope proposal is missing ' + field)
            clean[field] = text.strip()
        if clean['disposition'] not in {'keep', 'separate'}:
            raise ValueError('Invalid scope disposition')
        if clean['workflow'] == 'current':
            clean['workflow'] = workflow
        if clean['workflow'] not in {'small-change', 'ui-small', 'backend-feature', 'feature'}:
            raise ValueError('Unsupported suggested workflow')
        if clean['disposition'] == 'keep' and clean['workflow'] != workflow:
            raise ValueError('The retained subset must use the requested workflow')
        questions = item.get('required_inputs')
        if not isinstance(questions, list) or len(questions) > 12 or any(
            not isinstance(q, str) or not q.strip() or len(q) > 2000 for q in questions
        ):
            raise ValueError('Invalid required inputs')
        clean['required_inputs'] = [q.strip() for q in questions]
        clean['id'] = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:20]
        if any(old['id'] == clean['id'] for old in result):
            raise ValueError('Duplicate scope proposal')
        result.append(clean)
    if not any(item['disposition'] == 'separate' for item in result):
        raise ValueError('A scope split must identify the work that exceeds the rules')
    return result


def extract_proposals(text, workflow):
    blocks = re.findall(r'(?m)^```scope-suggestions\s*\n(.*?)\n```\s*$', text, re.S)
    if len(blocks) != 1:
        raise ValueError('The scope report did not include one structured task split')
    return proposals(json.loads(blocks[0]), workflow)


def proposal_key(task_id, run_id, suggestion_id):
    identity = json.dumps([task_id, run_id, suggestion_id])
    # Also serves as a workflow run ID, whose submission contract permits 64 characters.
    return 'scope-' + hashlib.sha256(identity.encode()).hexdigest()[:56]
