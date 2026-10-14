"""CLI for the orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click

from orchestrator.config import OrchestratorConfig


def _setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)-24s %(message)s",
        datefmt="%H:%M:%S",
    )


def _get_orch(config_path: str | None = None) -> "Orchestrator":
    from orchestrator.core import Orchestrator
    config = OrchestratorConfig.load(Path(config_path) if config_path else None)
    return Orchestrator(config)


@click.group()
@click.option("--config", "-c", default=None, help="Config file path")
@click.option("--log-level", "-l", default="INFO", help="Log level")
@click.pass_context
def main(ctx, config, log_level):
    """Codex Orchestrator — multi-agent coordination."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["log_level"] = log_level
    _setup_logging(log_level)


# ── Start ───────────────────────────────────────────────────────────────

@main.command()
@click.option("--api/--no-api", default=True, help="Start HTTP API server")
@click.option("--api-port", default=8420, help="API port")
@click.option("--spawn-team", "team", multiple=True, help="Auto-spawn agents (role names)")
@click.pass_context
def start(ctx, api, api_port, team):
    """Start the orchestrator (long-running daemon)."""
    asyncio.run(_start(ctx.obj, api, api_port, list(team)))


async def _start(obj: dict, api: bool, api_port: int, team: list[str]):
    from orchestrator.core import Orchestrator
    from orchestrator.api.server import run_api

    config = OrchestratorConfig.load(Path(obj["config_path"]) if obj["config_path"] else None)
    config.api_port = api_port
    orch = Orchestrator(config)

    runner = None
    try:
        await orch.start()

        if team:
            click.echo(f"Starting team: {', '.join(team)}")
            result = await orch.spawn_team(team)
            for role, aid in result.items():
                click.echo(f"  {role}: {aid}")

        if api:
            runner = await run_api(orch, config.api_host, config.api_port)
            click.echo(f"API: http://{config.api_host}:{config.api_port}")

        click.echo("Orchestrator running. Press Ctrl+C to stop.")
        await orch.wait_for_shutdown()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except (ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if runner:
            await runner.cleanup()
        await orch.stop()
        click.echo("Orchestrator stopped.")


# ── Agent Commands ──────────────────────────────────────────────────────

@main.group()
def agent():
    """Manage agents."""


@agent.command("spawn")
@click.argument("role")
@click.option("--id", "agent_id", default=None, help="Custom agent ID")
@click.pass_context
def agent_spawn(ctx, role, agent_id):
    """Spawn a new agent with the given role."""
    asyncio.run(_agent_spawn(ctx.obj, role, agent_id))


async def _agent_spawn(obj, role, agent_id):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=False)
    try:
        aid = await orch.spawn_agent(role, agent_id=agent_id)
        click.echo(f"Agent spawned: {aid} (role={role})")
    finally:
        await orch.stop()


@agent.command("list")
@click.pass_context
def agent_list(ctx):
    """List all agents."""
    asyncio.run(_agent_list(ctx.obj))


async def _agent_list(obj):
    orch = _get_orch(obj.get("config_path"))
    await orch.store.init()
    agents = await orch.store.list_agents()
    if not agents:
        click.echo("No agents.")
        return
    click.echo(f"{'ID':<18} {'Role':<16} {'Backend':<8} {'Status':<10} {'Port':<6}")
    click.echo("-" * 60)
    for a in agents:
        click.echo(f"{a.agent_id:<18} {a.role:<16} {a.backend:<8} {a.status:<10} {a.port or '-':<6}")
    await orch.store.close()


@agent.command("send")
@click.argument("agent_id")
@click.argument("message")
@click.option("--thread", "-t", default=None, help="Thread ID to resume")
@click.option("--cwd", default=None, help="Working directory")
@click.pass_context
def agent_send(ctx, agent_id, message, thread, cwd):
    """Send a message to an agent."""
    asyncio.run(_agent_send(ctx.obj, agent_id, message, thread, cwd))


async def _agent_send(obj, agent_id, message, thread_id, cwd):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=True)
    try:
        response = await orch.send_to_agent(agent_id, message, thread_id=thread_id, cwd=cwd)
        click.echo(response)
    finally:
        await orch.stop()


# ── Role Commands ───────────────────────────────────────────────────────

@main.command("roles")
@click.pass_context
def list_roles(ctx):
    """List available roles."""
    orch = _get_orch(ctx.obj.get("config_path"))
    orch._load_roles()
    if not orch.roles:
        click.echo("No roles found. Add .toml files to the roles/ directory.")
        return
    click.echo(f"{'Name':<16} {'Backend':<8} {'Model':<12} {'Description'}")
    click.echo("-" * 70)
    for name, role in orch.roles.items():
        click.echo(f"{name:<16} {role.backend:<8} {role.model or '-':<12} {role.description[:40]}")


# ── Workflow Commands ───────────────────────────────────────────────────

@main.group()
def workflow():
    """Manage and run workflows."""


@workflow.command("list")
@click.pass_context
def workflow_list(ctx):
    """List available workflows."""
    orch = _get_orch(ctx.obj.get("config_path"))
    click.echo(f"{'Name':<16} {'Steps':<6} {'Description'}")
    click.echo("-" * 60)
    for name, wf in orch.workflows.items():
        click.echo(f"{name:<16} {len(wf.steps):<6} {wf.description[:40]}")


@workflow.command("run")
@click.argument("name")
@click.option("--var", "-v", multiple=True, help="Variables as key=value")
@click.option("--cwd", default=None, help="Working directory")
@click.option("--roles-dir", default=None, type=click.Path(exists=True, file_okay=False),
              help="Role definitions directory, independent of the target repository")
@click.pass_context
def workflow_run(ctx, name, var, cwd, roles_dir):
    """Run a workflow."""
    variables = {}
    for v in var:
        k, _, val = v.partition("=")
        variables[k] = val
    if cwd:
        variables["cwd"] = cwd
    asyncio.run(_workflow_run({**ctx.obj, "roles_dir": roles_dir}, name, variables))


@workflow.command("show")
@click.argument("run_id")
@click.pass_context
def workflow_show(ctx, run_id):
    """Show a saved run's phase and complete CI report without starting agents."""
    asyncio.run(_workflow_show(ctx.obj, run_id))


async def _workflow_show(obj, run_id):
    orch = _get_orch(obj.get("config_path"))
    await orch.store.init()
    try:
        record = await orch.store.get_workflow_run(run_id)
        if not record:
            raise click.ClickException(f"Unknown workflow run: {run_id}")
        v = record.state.get("final_variables") or record.state.get("variables", {})
        click.echo(json.dumps({
            "run_id": run_id, "workflow": record.workflow_name, "status": record.status,
            "current_step": record.state.get("current_step"),
            **{key: v.get(key) for key in (
                "resumed_from", "mr_url", "mr_branch", "mr_ci_status", "mr_ci_sha",
                "ci_repair_count", "review_revision_count", "completion_error",
            )},
        }, indent=2))
        if v.get("mr_ci_report"):
            click.echo(v["mr_ci_report"])
    finally:
        await orch.store.close()


async def _workflow_run(obj, name, variables):
    orch = _get_orch(obj.get("config_path"))
    if obj.get("roles_dir"):
        orch.config.roles_dir = Path(obj["roles_dir"]).expanduser().resolve()
    await orch.start(restore=True)
    try:
        if name == "pr-completion":
            await orch.spawn_team(["implementer", "reviewer"])
            async def show_progress(event):
                if event.topic == "workflow.started":
                    click.echo(f"Run ID: {event.payload['run_id']} (save this to resume)")
                elif "mr_ci_status" in event.payload:
                    click.echo(f"CI: {event.payload['mr_ci_status']} {event.payload.get('mr_ci_sha', '')}")
            await orch.bus.subscribe("workflow.started", show_progress)
            await orch.bus.subscribe("workflow.mr.ci", show_progress)
        running = orch.registry.get_running_agents()
        if not running:
            click.echo("No running agents. Spawn a team first with: orch start --spawn-team <roles>")
            return
        result = await orch.run_workflow(name, variables=variables)
        click.echo(f"\nWorkflow '{name}' completed (run_id={result.run_id})")
        click.echo(f"Success: {result.success}")
        if result.error:
            click.echo(f"Error: {result.error}")
        for key, value in result.outputs.items():
            preview = str(value)[:200]
            click.echo(f"  {key}: {preview}")
        if name == "pr-completion" and not result.success:
            raise click.ClickException(f"PR completion failed (run_id={result.run_id}); see the error and saved CI report")
    finally:
        await orch.stop()


# ── Team Command ────────────────────────────────────────────────────────

@main.command("team")
@click.argument("roles", nargs=-1)
@click.pass_context
def spawn_team(ctx, roles):
    """Spawn a team of agents by role names."""
    asyncio.run(_spawn_team(ctx.obj, list(roles)))


async def _spawn_team(obj, roles):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=False)
    try:
        result = await orch.spawn_team(roles or None)
        click.echo("Team spawned:")
        for role, aid in result.items():
            click.echo(f"  {role}: {aid}")
        click.echo(f"\n{len(result)} agents running")
    finally:
        await orch.stop()


# ── Send to Role ────────────────────────────────────────────────────────

@main.command("send")
@click.argument("role")
@click.argument("message")
@click.option("--cwd", default=None, help="Working directory")
@click.pass_context
def send(ctx, role, message, cwd):
    """Send a message to an agent by role."""
    asyncio.run(_send(ctx.obj, role, message, cwd))


async def _send(obj, role, message, cwd):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=True)
    try:
        response = await orch.send_to_role(role, message, cwd=cwd)
        click.echo(response)
    finally:
        await orch.stop()


# ── Status ──────────────────────────────────────────────────────────────

@main.command("status")
@click.pass_context
def status(ctx):
    """Show orchestrator status."""
    asyncio.run(_status(ctx.obj))


async def _status(obj):
    orch = _get_orch(obj.get("config_path"))
    await orch.store.init()
    agents = await orch.store.list_agents()
    threads = await orch.store.list_threads()
    items = await orch.store.list_work_items()

    click.echo("=== Orchestrator Status ===")
    click.echo(f"Agents:     {len(agents)} ({sum(1 for a in agents if a.status == 'running')} running)")
    click.echo(f"Threads:    {len(threads)}")
    click.echo(f"Work items: {len(items)} ({sum(1 for i in items if i.status in ('pending', 'in_progress'))} active)")
    click.echo(f"Data dir:   {orch.config.data_dir}")

    if agents:
        click.echo("\nAgents:")
        for a in agents:
            click.echo(f"  [{a.status:>8}] {a.agent_id[:12]}  {a.role} ({a.backend})")

    await orch.store.close()


# ── Skill Commands ──────────────────────────────────────────────────────

@main.group()
def skill():
    """Manage and run skills."""


@skill.command("list")
@click.option("--role", default=None, help="Filter by role")
@click.pass_context
def skill_list(ctx, role):
    """List available skills."""
    orch = _get_orch(ctx.obj.get("config_path"))
    orch._load_skills()
    skills = orch.skills.list_for_role(role) if role else orch.skills.list_all()
    if not skills:
        click.echo("No skills found.")
        return
    click.echo(f"{'Name':<20} {'Roles':<30} {'Description'}")
    click.echo("-" * 80)
    for s in skills:
        roles_str = ", ".join(s.roles) if s.roles else "any"
        click.echo(f"{s.name:<20} {roles_str:<30} {s.description[:30]}")


@skill.command("run")
@click.argument("name")
@click.argument("role")
@click.option("--var", "-v", multiple=True, help="Variables as key=value")
@click.option("--cwd", default=None, help="Working directory")
@click.option("--no-supervise", is_flag=True, help="Disable supervision")
@click.pass_context
def skill_run(ctx, name, role, var, cwd, no_supervise):
    """Run a skill on an agent."""
    variables = {}
    for v in var:
        k, _, val = v.partition("=")
        variables[k] = val
    if cwd:
        variables["cwd"] = cwd
    asyncio.run(_skill_run(ctx.obj, name, role, variables, cwd, not no_supervise))


async def _skill_run(obj, name, role, variables, cwd, supervised):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=True)
    try:
        running = orch.registry.get_running_agents()
        if not running:
            click.echo("No running agents. Start the orchestrator first.")
            return
        response = await orch.run_skill(name, role, variables=variables, cwd=cwd, supervised=supervised)
        click.echo(response)
    finally:
        await orch.stop()


# ── Supervised Send ─────────────────────────────────────────────────────

@main.command("supervised-send")
@click.argument("role")
@click.argument("message")
@click.option("--cwd", default=None, help="Working directory")
@click.pass_context
def supervised_send_cmd(ctx, role, message, cwd):
    """Send a supervised message — ensures deliverables, not noise."""
    asyncio.run(_supervised_send(ctx.obj, role, message, cwd))


async def _supervised_send(obj, role, message, cwd):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=True)
    try:
        result = await orch.supervised_send(role, message, cwd=cwd)
        if result.accepted:
            click.echo(result.raw_response)
        else:
            click.echo("=== SUPERVISION FAILED ===")
            for issue in result.issues:
                click.echo(f"  - {issue}")
            click.echo(f"\nNoise ratio: {result.noise_ratio:.0%}")
            click.echo(f"\nRaw response:\n{result.raw_response[:500]}")
        click.echo(f"\nDeliverables: {len(result.deliverables)}")
        for d in result.deliverables:
            click.echo(f"  [{d.dtype.value}] {d.evidence[:80]} {'✓' if d.verified else ''}")
    finally:
        await orch.stop()


# ── Audit Commands ──────────────────────────────────────────────────────

@main.group()
def audit():
    """View audit trail, costs, and quality reports."""


@audit.command("turns")
@click.option("--role", default=None, help="Filter by role")
@click.option("--limit", "-n", default=20, help="Number of turns")
@click.pass_context
def audit_turns(ctx, role, limit):
    """Show recent turns with timing and tokens."""
    asyncio.run(_audit_turns(ctx.obj, role, limit))


async def _audit_turns(obj, role, limit):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=False)
    try:
        timeline = await orch.audit.get_recent_timeline(limit=limit)
        if not timeline:
            click.echo("No audit data yet. Run some agent interactions first.")
            return
        click.echo(f"{'#':<4} {'Time':<12} {'Role':<13} {'Model':<16} {'In':>7} {'Out':>7} {'Cache':>7} {'Ms':>7} {'Len':>6} {'Sup':>4} {'Message'}")
        click.echo("-" * 120)
        for t in timeline:
            ts = (t.get("timestamp") or "")[-12:-7]  # HH:MM
            sup = ""
            if t.get("supervision_accepted") is not None:
                sup = "Y" if t["supervision_accepted"] else "N"
            noise = ""
            if t.get("supervision_noise_ratio") is not None:
                noise = f"{t['supervision_noise_ratio']:.0%}"
            click.echo(
                f"{t['id']:<4} {ts:<12} {t.get('role','?'):<13} "
                f"{(t.get('model') or '?'):<16} "
                f"{t.get('input_tokens') or '-':>7} {t.get('output_tokens') or '-':>7} "
                f"{'':>7} "
                f"{t.get('duration_ms') or '-':>7} "
                f"{t.get('response_length') or 0:>6} "
                f"{sup:>4} "
                f"{(t.get('message_preview') or '')[:40]}"
            )
    finally:
        await orch.store.close()


@audit.command("costs")
@click.option("--role", default=None, help="Filter by role")
@click.pass_context
def audit_costs(ctx, role):
    """Show cost summary."""
    asyncio.run(_audit_costs(ctx.obj, role))


async def _audit_costs(obj, role):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=False)
    try:
        summary = await orch.audit.get_cost_summary(role=role)
        if not summary.get("turns"):
            click.echo("No cost data yet.")
            return
        click.echo("=== Cost Summary ===")
        click.echo(f"Turns:          {summary.get('turns', 0)}")
        click.echo(f"Input tokens:   {summary.get('total_input', 0):,}")
        click.echo(f"Output tokens:  {summary.get('total_output', 0):,}")
        click.echo(f"Cached tokens:  {summary.get('total_cached', 0):,}")
        click.echo(f"Total cost:     ${summary.get('total_cost', 0):.4f}")
        click.echo(f"Avg per turn:   ${summary.get('avg_cost_per_turn', 0):.4f}")
        click.echo(f"Max single turn:${summary.get('max_cost_turn', 0):.4f}")
    finally:
        await orch.store.close()


@audit.command("quality")
@click.option("--role", default=None, help="Filter by role")
@click.pass_context
def audit_quality(ctx, role):
    """Show quality report (supervision acceptance rates, noise)."""
    asyncio.run(_audit_quality(ctx.obj, role))


async def _audit_quality(obj, role):
    orch = _get_orch(obj.get("config_path"))
    await orch.start(restore=False)
    try:
        report = await orch.audit.get_quality_report(role=role)
        total = report.get("total_supervised") or 0
        if not total:
            click.echo("No supervised interactions yet.")
            return
        click.echo("=== Quality Report ===")
        click.echo(f"Supervised turns:   {total}")
        click.echo(f"Accepted:           {report.get('accepted', 0)} ({report.get('acceptance_rate', 0):.0%})")
        click.echo(f"Rejected:           {total - (report.get('accepted') or 0)} ({report.get('rejection_rate', 0):.0%})")
        click.echo(f"Avg noise ratio:    {report.get('avg_noise_ratio', 0):.1%}")
        click.echo(f"Avg response len:   {report.get('avg_response_length', 0):.0f} chars")
        click.echo(f"Avg duration:       {(report.get('avg_duration_ms') or 0) / 1000:.1f}s")
    finally:
        await orch.store.close()


if __name__ == "__main__":
    main()
