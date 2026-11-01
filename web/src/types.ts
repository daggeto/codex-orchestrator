export type JsonObject = Record<string, unknown>;
export interface ActivityThread {
  thread_id: string; workspace: string | null; message_preview: string; started_at: string;
}
export interface Agent {
  agent_id: string; name: string; role: string; backend: string; status: string;
  activity?: { state: 'idle' | 'working' | 'error'; threads: ActivityThread[]; error: string | null };
}
export interface Task {
  item_id: string; title: string; status: string; priority: number;
  assigned_agent: string | null; tags: string[]; history?: JsonObject[];
  created_at: string; updated_at: string; completed_at: string | null;
}
export interface TaskDetail extends Task {
  description: string; assigned_thread: string | null; workflow_id: string | null;
  parent_item: string | null; input_data: JsonObject; output_data: JsonObject;
  history: JsonObject[];
}
export interface WorkflowRun {
  run_id: string; workflow_name: string; status: string; config: JsonObject; state: JsonObject;
  created_at: string; updated_at: string; completed_at: string | null;
}
export interface Workspace {
  worktree_id: string; repo: string; path: string; branch: string; base_branch: string;
  agent_id: string | null; work_item_id: string | null; status: string;
}
export interface MonitorEvent { topic: string; source: string; payload: JsonObject; timestamp: string }
export interface Snapshot { agents: Agent[]; tasks: Task[]; runs: WorkflowRun[]; workspaces: Workspace[] }
export type Selection = { kind: 'task' | 'workspace' | 'run' | 'agent'; id: string } | null;
export type Connection = 'connecting' | 'connected' | 'disconnected' | 'reconnecting';

export interface Project { path: string; name: string; available: boolean; created_at: string; updated_at: string }
export interface Role { name: string; backend: string; description: string; model: string | null; reasoning_effort: string | null }
export interface WorkflowDefinition {
  name: string; description: string; steps: string[]; required_roles: string[];
  publication_policy: 'none' | 'optional' | 'required';
}
export interface ChatMessage { id: string; role: 'user' | 'assistant' | 'system'; content: string }
export interface TaskSubmission {
  run_id: string; workflow: string;
  variables: { task_description: string; cwd: string; create_mr?: boolean };
}

export interface RepositoryRecord {
  repository_id: string; name: string; main_path: string; available: boolean;
}
export interface WorkspaceRecord {
  workspace_id: string; repository_id: string; kind: 'main' | 'worktree'; path: string;
  branch: string; status: string; available?: boolean;
  archive?: { stage: string; path: string | null; error: string | null };
}
export interface TaskRecord {
  task_id: string; workspace_id: string; title: string; prompt: string; kind: 'agent' | 'workflow';
  archived_at: string | null; status: string; phase: string; last_turn?: { ok: boolean } | null; settings: Record<string, unknown>; created_at: string;
}
export interface OwnedAgent {
  agent_id: string; role: string; backend: string; task_id: string | null; status: string; retired_at: string | null;
  activity?: { state: string };
}
export interface DurableEvent {
  id: number; agent_id?: string; thread_id?: string; kind: string; payload: Record<string, unknown>; turn_id: string; item_id: string | null; created_at: string;
}
export interface StepExecution {
  execution_id: string; step_id: string; attempt: number; parent_id: string | null; dependencies: string[];
  agent_id: string | null; thread_id: string | null; status: string; output: Record<string, unknown>;
}
export interface InputRequest {
  request_id: string; kind: string; prompt: string; options: unknown[];
}
export interface TaskView {
  task: TaskRecord; agents: OwnedAgent[]; workflow: { workflow_name: string } | null;
  steps: StepExecution[]; pending_input: InputRequest[];
}
