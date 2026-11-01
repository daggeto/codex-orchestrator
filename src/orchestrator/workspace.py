"""
Workspace isolation — git worktrees for concurrent agent work.

Isolation model:
- Read-only agents (architect, reviewer): share the main repo, no conflicts
- Write agents (implementer, qa): each work item gets a git worktree
- Worktree lifecycle: create on assignment, merge back on approval, cleanup on reject

This prevents race conditions when multiple agents work on the same repo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.worktree_names import task_worktree_name

logger = logging.getLogger(__name__)


@dataclass
class Worktree:
    """A git worktree for isolated agent work."""
    worktree_id: str
    repo_path: str           # original repo
    worktree_path: str       # path to the worktree
    branch: str              # branch name in the worktree
    base_branch: str         # branch it was created from
    agent_id: str | None = None
    work_item_id: str | None = None
    status: str = "active"   # active, merged, abandoned


class WorkspaceManager:
    """
    Manages git worktrees for agent isolation.

    Each write-capable agent gets a worktree per work item so they
    can make changes without interfering with each other or the main repo.
    """

    def __init__(self, worktrees_dir: Path | None = None):
        self._worktrees_dir = worktrees_dir or Path.home() / ".codex-orchestrator" / "worktrees"
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, Worktree] = {}
        self.persistence = None

    async def create_worktree(
        self,
        repo_path: str,
        agent_id: str,
        work_item_id: str | None = None,
        base_branch: str | None = None,
        branch_prefix: str = "orch",
        task_title: str | None = None,
    ) -> Worktree:
        """
        Create a git worktree for an agent to work in.

        Args:
            repo_path: Path to the git repo
            agent_id: Agent that will use this worktree
            work_item_id: Optional work item ID for naming
            base_branch: Branch to base the worktree on (default: current HEAD)
            branch_prefix: Prefix for the new branch name
            task_title: Task text used for readable branch and directory names
        """
        wt_id = uuid.uuid4().hex[:12]
        suffix = work_item_id[:8] if work_item_id else wt_id[:8]
        branch = f"{branch_prefix}/{agent_id[:8]}/{suffix}"
        name = task_worktree_name(task_title) if task_title is not None else None
        if name:
            branch = f"{branch_prefix}/{name}"
        wt_path = self._worktrees_dir / (name or wt_id)

        # Get base branch if not specified
        if not base_branch:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            base_branch = stdout.decode().strip() or "main"

        # Create the worktree with a new branch
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "-b", branch, str(wt_path), base_branch,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode()
            # If branch already exists, try without -b
            if "already exists" in error:
                proc = await asyncio.create_subprocess_exec(
                    "git", "worktree", "add", str(wt_path), branch,
                    cwd=repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"Failed to create worktree: {stderr.decode()}")
            else:
                raise RuntimeError(f"Failed to create worktree: {error}")

        wt = Worktree(
            worktree_id=wt_id,
            repo_path=repo_path,
            worktree_path=str(wt_path),
            branch=branch,
            base_branch=base_branch,
            agent_id=agent_id,
            work_item_id=work_item_id,
        )
        if self.persistence:
            await self.persistence.register(wt.worktree_path)
            record = await self.persistence.store.one('SELECT * FROM workspaces WHERE path=?', (wt.worktree_path,))
            wt.worktree_id = record['workspace_id']
        self._active[wt.worktree_id] = wt
        logger.info(
            "Created worktree %s at %s (branch=%s, base=%s)",
            wt_id, wt_path, branch, base_branch,
        )
        return wt

    async def get_diff(self, worktree_id: str) -> str:
        """Get the diff between the worktree branch and its base."""
        wt = self._active.get(worktree_id)
        if not wt:
            raise KeyError(f"Worktree {worktree_id} not found")

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", f"{wt.base_branch}...{wt.branch}", "--stat",
            cwd=wt.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    async def get_full_diff(self, worktree_id: str) -> str:
        """Get the full diff between the worktree branch and its base."""
        wt = self._active.get(worktree_id)
        if not wt:
            raise KeyError(f"Worktree {worktree_id} not found")

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", f"{wt.base_branch}...{wt.branch}",
            cwd=wt.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    async def get_log(self, worktree_id: str) -> str:
        """Get commits on the worktree branch since base."""
        wt = self._active.get(worktree_id)
        if not wt:
            raise KeyError(f"Worktree {worktree_id} not found")

        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", f"{wt.base_branch}..{wt.branch}",
            cwd=wt.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    async def merge_worktree(
        self,
        worktree_id: str,
        target_branch: str | None = None,
        strategy: str = "merge",
    ) -> str:
        """Compatibility entry point; use the same durable local-main contract."""
        import uuid
        if target_branch not in (None, 'main') or strategy != 'merge':
            raise ValueError('Only normal merges into local main are supported')
        service = getattr(self, 'merge_service', None)
        if not service:
            raise RuntimeError('Safe merging requires the persistent merge service')
        check = await service.preflight(worktree_id)
        if not check['eligible']:
            raise RuntimeError('; '.join(check['blockers']))
        from orchestrator.merges import IDENTITY
        operation, created = await service.begin(worktree_id, {
            'operation_id': uuid.uuid4().hex, **{key: check[key] for key in IDENTITY}})
        if created:
            await service.execute(operation['operation_id'])
        result = await service.store.merge_operation(operation['operation_id'])
        if result['state'] not in {'merged', 'already_integrated'}:
            raise RuntimeError(result['error_message'] or 'Merge did not complete')
        return result['resulting_sha']

    async def cleanup_worktree(self, worktree_id: str, delete_branch: bool = False):
        """Archive before removal; branches and historical records are retained."""
        wt = self._active.get(worktree_id)
        if not wt:
            raise KeyError(f"Worktree {worktree_id} not found")
        service = getattr(self, 'archive_service', None)
        if not service:
            raise RuntimeError('Safe deletion requires the persistent archive service')
        await service.orch.workspaces.register(wt.worktree_path)
        workspace = await service.store.one('SELECT * FROM workspaces WHERE path=?', (wt.worktree_path,))
        await service.archive_and_delete(workspace['workspace_id'])
        self._active.pop(worktree_id, None)

    async def cleanup_all(self):
        """Clean up all worktrees."""
        for wt_id in list(self._active):
            await self.cleanup_worktree(wt_id, delete_branch=True)

    def get_worktree(self, worktree_id: str) -> Worktree | None:
        return self._active.get(worktree_id)

    def list_worktrees(self) -> list[Worktree]:
        return list(self._active.values())

    def get_worktrees_for_agent(self, agent_id: str) -> list[Worktree]:
        return [wt for wt in self._active.values() if wt.agent_id == agent_id]

    def get_worktree_for_work_item(self, work_item_id: str) -> Worktree | None:
        for wt in self._active.values():
            if wt.work_item_id == work_item_id:
                return wt
        return None
