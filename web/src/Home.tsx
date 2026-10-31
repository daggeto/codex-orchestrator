import { useEffect, useState } from 'react';
import { get } from './api';
import type { ChatMessage, Role, WorkflowDefinition } from './types';
import { useTaskSubmission, terminalRun } from './useTaskSubmission';
import { Chat } from './components/Chat';
import { ProjectPicker } from './components/ProjectPicker';
import { WorkflowPicker } from './components/WorkflowPicker';
import { RunStatus } from './components/RunStatus';
import { AgentConversation } from './components/AgentConversation';

export function Home() {
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [workflow, setWorkflow] = useState('feature');
  const [cwd, setCwd] = useState('');
  const [publish, setPublish] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const task = useTaskSubmission();
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      get<{ workflows: WorkflowDefinition[] }>('/api/workflows', controller.signal),
      get<{ roles: Role[] }>('/api/roles', controller.signal),
      get<{ project_dir: string }>('/api/session', controller.signal),
    ]).then(([definitions, metadata, session]) => {
      if (controller.signal.aborted) return;
      setWorkflows(definitions.workflows); setRoles(metadata.roles);
      setWorkflow(previous => definitions.workflows.some(item => item.name === previous) ? previous : definitions.workflows[0]?.name || '');
      setCwd(previous => previous || session.project_dir); setMetadataError(''); setReady(true);
    }).catch(reason => { if (!controller.signal.aborted) { setReady(false); setMetadataError(String(reason)); } });
    return () => controller.abort();
  }, [revision]);
  const selected = workflows.find(item => item.name === workflow);
  const messages: ChatMessage[] = task.submission
    ? [{ id: task.submission.run_id, role: 'user', content: task.submission.variables.task_description }] : [];
  const canReset = terminalRun(task.run) || task.rejected;
  return <main className="task-home">
    <div className="home-intro"><p className="eyebrow">ORCHESTRATOR / TASKS</p><h1>What are we working on?</h1>
      <p className="muted">Choose a project and workflow. Your team takes it from there.</p></div>
    {metadataError && <section><p className="error" role="alert">{metadataError}</p><button onClick={() => setRevision(value => value + 1)}>Reload workflow metadata</button></section>}
    <div className="home-layout"><aside>
      <WorkflowPicker workflows={workflows} roles={roles} value={task.submission?.workflow || workflow}
        onChange={value => { setWorkflow(value); setPublish(false); }} publish={task.submission?.variables.create_mr ?? publish}
        onPublish={setPublish} disabled={!ready || !!task.submission} />
      <ProjectPicker value={task.submission?.variables.cwd || cwd} onChange={setCwd} disabled={!!task.submission} />
    </aside><div>
      <section className="task-composer"><h2>Start a task</h2>
        <Chat key={task.submission?.run_id || 'new-task'} label="Task description" placeholder="Describe the outcome, constraints, and anything the team should know…"
          submitLabel="Start task" messages={messages} loading={task.busy}
          disabled={!ready || !selected || !cwd || !!task.submission} error={task.error}
          onSubmit={async text => {
            if (!selected) return;
            await task.send({ run_id: crypto.randomUUID(), workflow: selected.name,
              variables: { task_description: text, cwd,
                ...(selected.publication_policy === 'optional' ? { create_mr: publish } : {}) } });
          }} />
      </section>
      {task.submission && <>
        <RunStatus submission={task.submission} run={task.run} error={task.error} />
        {!task.run && !task.rejected && <button disabled={task.busy} onClick={() => { if (task.submission) void task.send(task.submission).catch(() => {}); }}>Retry same submission</button>}
        {canReset && <button disabled={task.busy} onClick={task.reset}>Start another task</button>}
      </>}
      {(task.submission?.variables.cwd || cwd) && <details className="conversation-panel"><summary>Agent conversations</summary>
        <AgentConversation cwd={task.submission?.variables.cwd || cwd} /></details>}
    </div></div>
  </main>;
}
