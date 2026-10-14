"""Prepare a task branch and publish approved commits as a draft PR/MR."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

from orchestrator.workflows.steps import AssignStep, Step, StepResult

if TYPE_CHECKING:
    from orchestrator.workflows.engine import WorkflowContext


async def _run(cwd: str, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"{args[0]} command timed out after 120 seconds") from exc
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise
    if proc.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"{args[0]} failed")
    return stdout.decode(errors="replace").strip()


def _status(status: str, reason: str, **extra) -> StepResult:
    return StepResult(
        success=status != "blocked",
        output={"mr_status": status, "mr_reason": reason, **extra},
        error=reason if status == "blocked" else None,
    )


async def _open_requests(root: str, provider: str, remote: str, branch: str) -> list[dict]:
    if provider == "github":
        output = await _run(root, "gh", "pr", "list", "--repo", remote, "--state", "open",
                            "--head", branch, "--json", "url,baseRefName,headRefName,isCrossRepository")
        return [{"url": row["url"], "base": row["baseRefName"], "branch": row["headRefName"]}
                for row in json.loads(output) if not row["isCrossRepository"]]
    output = await _run(root, "glab", "mr", "list", "--repo", remote,
                        "--source-branch", branch, "--output", "json")
    return [{"url": row["web_url"], "base": row["target_branch"], "branch": row["source_branch"]}
            for row in json.loads(output) if row.get("state") == "opened"
            and row["source_project_id"] == row["target_project_id"]]


async def _default_branch(root: str, provider: str, remote: str) -> str:
    # Provider metadata is authoritative even when origin/HEAD is absent or stale.
    if provider == "github":
        return await _run(root, "gh", "repo", "view", remote, "--json", "defaultBranchRef",
                          "--jq", ".defaultBranchRef.name")
    parsed = urlparse(remote if "://" in remote else "ssh://" + remote.replace(":", "/", 1))
    project = parsed.path.strip("/").removesuffix(".git")
    if not parsed.hostname or not project:
        raise ValueError("Cannot identify the GitLab origin project")
    output = await _run(root, "glab", "api", f"projects/{quote(project, safe='')}",
                        "--hostname", parsed.hostname)
    base = json.loads(output)["default_branch"]
    if not base:
        raise ValueError("GitLab project has no default branch")
    return str(base)


class PrepareMergeRequestStep(Step):
    """Establish the Git baseline before any agent edits the repository."""

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        cwd = str(Path(ctx.variables.get("cwd") or Path.cwd()).expanduser().resolve())
        ctx.variables["cwd"] = cwd
        if str(ctx.variables.get("create_mr", "true")).lower() in {"false", "0", "no"}:
            return _status("skipped", "Disabled with create_mr=false")
        try:
            root = await _run(cwd, "git", "rev-parse", "--show-toplevel")
            if await _run(root, "git", "status", "--porcelain", "--untracked-files=all"):
                return _status("blocked", "Repository has existing uncommitted changes; no task branch created")
            remote_url = await _run(root, "git", "remote", "get-url", "origin")
            provider = str(ctx.variables.get("mr_provider", ""))
            if not provider:
                if re.search(r"(?:@|://)github\.com[:/]", remote_url):
                    provider = "github"
                elif re.search(r"(?:@|://)gitlab\.com[:/]", remote_url):
                    provider = "gitlab"
            cli = {"github": "gh", "gitlab": "glab"}.get(provider)
            if not cli or not shutil.which(cli):
                return _status("blocked", "Install gh (GitHub) or glab (GitLab); use mr_provider for a custom host")
            current = await _run(root, "git", "branch", "--show-current")
            if not current:
                return _status("blocked", "Detached HEAD; select the task branch before publication")
            matches = await _open_requests(root, provider, remote_url, current)
            if len(matches) > 1:
                return _status("blocked", "Multiple open PRs/MRs for this branch; select an unambiguous task branch")
            existing = matches[0] if matches else None
            requested_base = ctx.variables.get("mr_base")
            if existing and requested_base and requested_base != existing["base"]:
                return _status("blocked", "mr_base differs from the existing PR/MR target branch")
            base = str(existing["base"] if existing else
                       requested_base or await _default_branch(root, provider, remote_url))
            await _run(root, "git", "check-ref-format", f"refs/heads/{base}")
            await _run(root, "git", "fetch", "origin", f"refs/heads/{base}:refs/remotes/origin/{base}")
            baseline = await _run(root, "git", "rev-parse", "HEAD")
            ahead = await _run(root, "git", "rev-list", f"refs/remotes/origin/{base}..HEAD")
            # Existing requests must keep their source branch. Reuse feature branches
            # with committed work; the reviewer must inspect the complete PR diff.
            reuse = bool(existing) or (current != base and bool(ahead))
            branch = current if reuse else f"orch/feature-{ctx.run_id}"
            if not reuse:
                await _run(root, "git", "switch", "-c", branch)
            return _status(
                "prepared", "Existing task branch selected" if reuse else "Task branch created",
                mr_branch=branch, mr_base=base, mr_url=existing["url"] if existing else "",
                mr_provider=provider, mr_baseline=baseline, mr_repo=root,
                mr_remote_url=remote_url,
            )
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            return _status("blocked", f"Cannot prepare PR/MR: {exc}")


class CreateMergeRequestStep(Step):
    """Push only clean, approved task commits, then create a draft PR/MR."""

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        v = ctx.variables
        if v.get("mr_status") != "prepared":
            return _status(v.get("mr_status", "blocked"), v.get("mr_reason", "No task branch prepared"))
        if v.get("review_decision") != "approved":
            return _status("blocked", "Code review has not approved the implementation")
        root, branch, base = v["mr_repo"], v["mr_branch"], v["mr_base"]
        try:
            current = await _run(root, "git", "branch", "--show-current")
            if current != branch:
                return _status("blocked", "Agent changed branches; task branch was not published")
            if await _run(root, "git", "status", "--porcelain", "--untracked-files=all"):
                return _status("blocked", "Uncommitted changes remain; commit and review them before publishing")
            if await _run(root, "git", "remote", "get-url", "origin") != v["mr_remote_url"]:
                return _status("blocked", "The origin remote changed during the task")
            await _run(root, "git", "merge-base", "--is-ancestor", v["mr_baseline"], "HEAD")
            files = (await _run(root, "git", "diff", "--name-only", v["mr_baseline"], "HEAD")).splitlines()
            # Check the actual PR diff as well, including remote changes since preparation.
            await _run(root, "git", "fetch", "origin", f"refs/heads/{base}:refs/remotes/origin/{base}")
            pr_files = (await _run(root, "git", "diff", "--name-only", f"refs/remotes/origin/{base}...HEAD")).splitlines()
            if not pr_files and not v.get("mr_url"):
                return _status("skipped", "Changes are already on the target branch", mr_files=files)
            title = " ".join(str(v.get("task_description", "Feature implementation")).split())[:120]
            title = title or "Feature implementation"
            body = str(v.get("summary") or v.get("implementation") or title)
            body += "\n\nChanged files:\n" + "\n".join(f"- `{f}`" for f in pr_files)
            provider = v["mr_provider"]
            cli = "gh" if provider == "github" else "glab"
            # Explicit repository selection prevents publishing to another remote/fork.
            repo_args = ["--repo", v["mr_remote_url"]]
            matches = await _open_requests(root, provider, v["mr_remote_url"], branch)
            if len(matches) > 1 or (matches and matches[0]["base"] != base):
                return _status("blocked", "Open PR/MR target changed or is ambiguous; publication stopped")
            if v.get("mr_url") and (not matches or matches[0]["url"] != v["mr_url"]):
                return _status("blocked", "The selected PR/MR was closed or replaced during implementation")
            await _run(root, "git", "push", "--set-upstream", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
            if matches:
                return _status("existing", "Changes pushed to existing PR/MR", mr_url=matches[0]["url"], mr_files=pr_files)
            if provider == "github":
                with tempfile.TemporaryDirectory(prefix="orch-pr-") as tmp:
                    body_path = Path(tmp) / "body.md"
                    body_path.write_text(body)
                    output = await _run(root, cli, "pr", "create", *repo_args, "--draft", "--base", base,
                                        "--head", branch, "--title", title, "--body-file", str(body_path))
            else:
                output = await _run(root, cli, "mr", "create", *repo_args, "--draft", "--source-branch", branch,
                                    "--target-branch", base, "--title", title, "--description", body, "--yes")
            urls = re.findall(r"https?://[^\s]+/(?:pull/|-/merge_requests/)\d+", output)
            if not urls:
                return _status("blocked", "CLI returned no PR/MR URL; inspect the remote before retrying", mr_files=pr_files)
            return _status("created", "Draft PR/MR created", mr_url=urls[-1], mr_files=pr_files)
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            return _status("blocked", f"Cannot publish PR/MR: {exc}")


class VerifyMergeRequestStep(AssignStep):
    """Have the implementer verify CI and deployment before completing publication."""

    def __init__(self, step_id: str, **kwargs):
        super().__init__(
            step_id=step_id, role="implementer", cwd="{cwd}",
            output_key="mr_ci_report",
            message_template=(
                "Verify CI and the preview deployment for this {mr_provider} PR/MR: {mr_url}\n"
                "Repository: {mr_repo}\nBranch: {mr_branch}\n\n"
                "Use read-only provider CLI/API queries and deployment status evidence. "
                "Resolve the latest PR/MR head commit and verify that both CI checks and "
                "the deployment succeeded for that exact commit. Recheck the head before "
                "reporting success; if it changes, inspect the new commit within the same budget. "
                "Missing, skipped, or unavailable checks/deployments do not prove success. "
                "Monitor for at most 300 seconds in this turn, polling no more often than every "
                "20 seconds with bounded queries. Stop immediately on failure or inability "
                "to inspect the results. Read failed job/deployment logs and report the "
                "root cause and a concrete repair when available. Do not change files, "
                "push fixes, rerun jobs, or merge. "
                "Report the PR/MR URL, commit SHA, check/deployment statuses and links, "
                "and explain any failure, missing evidence, or pending work. "
                "End with exactly one standalone line: CI_GREEN only if both CI and deployment "
                "are confirmed successful, CI_PENDING if checks or deployment are still "
                "queued/running at the end of this turn (the workflow will continue monitoring), "
                "or CI_BLOCKED for failed or unverifiable results."
            ),
            **kwargs,
        )

    async def execute(self, ctx: WorkflowContext) -> StepResult:
        if ctx.variables.get("mr_status") not in {"created", "existing"}:
            if ctx.variables.get("mr_status") == "skipped":
                return StepResult(success=True, output={"mr_ci_status": "skipped"})
            return StepResult(success=False, output={"mr_ci_status": "blocked"},
                              error=ctx.variables.get("mr_reason", "PR/MR publication did not succeed"))
        url = ctx.variables["mr_url"]
        while True:
            try:
                # Each turn remains bounded; pending remote jobs continue in another turn.
                result = await asyncio.wait_for(super().execute(ctx), timeout=360)
            except TimeoutError:
                reason = f"Verification agent timed out for {url}; green status is unconfirmed."
                return StepResult(success=False, output={"mr_ci_status": "timeout", "mr_ci_report": reason}, error=reason)
            report = result.output.get("mr_ci_report", "").strip()
            marker = report.splitlines()[-1] if report else ""
            status = {"CI_GREEN": "green", "CI_PENDING": "pending"}.get(marker, "blocked")
            if not result.success:
                status = "blocked"
            result.output["mr_ci_status"] = status
            ctx.variables.update(result.output)
            record = await ctx.store.get_workflow_run(ctx.run_id)
            if record:
                record.state.update(current_step=self.step_id, variables=ctx.variables)
                await ctx.store.save_workflow_run(record)
            await ctx.bus.publish("workflow.mr.ci", {
                "run_id": ctx.run_id, "mr_url": url, "status": status, "report": report,
            }, source="verify-mr")
            if status == "pending":
                await asyncio.sleep(20)
                continue
            result.success = status == "green"
            if not result.success:
                result.error = result.error or f"CI/deployment {status} for {url}: {report or 'No verification report returned'}"
            return result
