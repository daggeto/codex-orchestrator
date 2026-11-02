import { Button } from './components/ui/Button';
import { useEffect, useRef, useState } from 'react';
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
  const [composerRevision, setComposerRevision] = useState(0);
  const hadSubmission = useRef(!!task.submission);
  useEffect(() => {
    if (hadSubmission.current && !task.submission) setComposerRevision(value => value + 1);
    hadSubmission.current = !!task.submission;
  }, [task.submission]);
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
    <div className="home-intro"><h1>Start a task</h1></div>
    <div className="task-surface">
      <Chat resetKey={composerRevision} label="Task description" rows={3} hint="" placeholder="Describe the outcome and constraints…"
        submitLabel="Start task" messages={messages} loading={task.busy}
        disabled={!ready || !selected || !cwd || !!task.submission} error={task.submission ? '' : task.error}
        toolbar={<div className="task-toolbar">
          <ProjectPicker value={task.submission?.variables.cwd || cwd} onChange={setCwd} disabled={!!task.submission} />
          <WorkflowPicker workflows={workflows} roles={roles} value={task.submission?.workflow || workflow}
            onChange={value => { setWorkflow(value); setPublish(false); }} publish={task.submission?.variables.create_mr ?? publish}
            onPublish={setPublish} disabled={!ready || !!task.submission} />
        </div>}
        onSubmit={async text => {
          if (!selected) return;
          await task.send({ run_id: crypto.randomUUID(), workflow: selected.name,
            variables: { task_description: text, cwd,
              ...(selected.publication_policy === 'optional' ? { create_mr: publish } : {}) } });
        }}>
        {metadataError && <div><p className="error" role="alert">{metadataError}</p>
          <Button type="button" onClick={() => setRevision(value => value + 1)}>Reload workflow metadata</Button></div>}
        {task.submission && <>
          <p className="submission-note">{task.run ? 'Accepted task' : task.rejected ? 'Submission rejected' : 'Saved request · acceptance unconfirmed'}</p>
          <RunStatus submission={task.submission} run={task.run} error={task.error} rejected={task.rejected} />
          <div className="recovery-actions">
            {!task.run && !task.rejected && <Button type="button" disabled={task.busy} onClick={() => { if (task.submission) void task.send(task.submission).catch(() => {}); }}>Retry same submission</Button>}
            {canReset && <Button type="button" disabled={task.busy} onClick={task.reset}>Start another task</Button>}
          </div>
        </>}
        {(task.submission?.variables.cwd || cwd) && <details className="conversation-panel"><summary>Agent conversations</summary>
          <AgentConversation cwd={task.submission?.variables.cwd || cwd} /></details>}
      </Chat>
    </div>
  </main>;
}
