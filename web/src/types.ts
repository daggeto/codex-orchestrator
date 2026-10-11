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
