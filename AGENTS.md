# Repository instructions

Keep answers focused and easy to scan. Start with the main point and explain only
what is necessary. Prefer short, natural paragraphs. Public-facing text should
be clear, specific, conversational, and use normal punctuation without em dashes.

## Project commands

- Use `make help` to discover project commands. Use `make <command> help` for
  detailed explanations and examples for that command.
- `scripts/commands.py` is the single command catalog. The Makefile generates its
  runnable targets and both help views from this catalog. Do not add separately
  maintained target lists or help descriptions.
- Whenever you add, rename, remove, or change a CLI command in
  `src/orchestrator/cli.py`, an npm script in `web/package.json`, or a project
  setup/check command, update the catalog in the same change. Keep the wrapped
  arguments, one-sentence summary, detailed behavior/options, and examples current.
- When workflow or role behavior changes, update affected catalog descriptions
  and examples too. Document lifecycle and publication effects accurately.
- Prefer Make targets in new project documentation. Pass command arguments with
  `ARGS='...'` and global CLI options with `ORCH_ARGS='...'`. Keep messages quoted
  inside ARGS. The runner passes argv directly and does not evaluate shell code.
- Help must work before dependencies are installed. A `help` goal must never
  start agents, install packages, run checks, or perform the selected action.

## Debugging efficiency

- For local application hangs, start with application logs, process or container
  health, database activity and locks, then the route's direct dependency chain.
- Use browser automation for reproduction and final verification unless the
  issue is clearly client-side or the user requests browser interaction.
- Prefer a fresh test tab when a browser tab is unresponsive.
- Stop gathering evidence once an authoritative signal supports the root cause.
  Keep tool output narrow and avoid duplicate verification and broad searches.

## Testing boundaries

- Do not run testing loops or repeatedly rerun failing suites during implementation.
- If a plan requires manual testing, stop after implementation and explain the
  manual validation steps for the user to run.
- Run tests, extended verification, or iterative test-fixing only when the user
  explicitly asks for it.
