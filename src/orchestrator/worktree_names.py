"""Readable, bounded Git identities for newly created task checkouts."""
import re
import unicodedata
import uuid


def task_worktree_name(text: str) -> str:
    words = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', words).strip('-')[:48].rstrip('-') or 'task'
    return f'{slug}-{uuid.uuid4().hex[:12]}'
