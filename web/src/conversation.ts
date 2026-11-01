import type { DurableEvent } from './types';
export interface ConversationEntry {
  key: string; kind: 'user' | 'assistant' | 'error' | 'activity'; text: string;
  events: DurableEvent[]; attachmentIds?: string[];
}
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};
const readable = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(readable).filter(Boolean).join('\n');
  const record = object(value);
  return record.message ? readable(record.message) : record.text ? readable(record.text) : value ? JSON.stringify(value) : '';
};
const scope = (event: DurableEvent) => JSON.stringify([event.agent_id, event.thread_id, event.turn_id]);
export function activityLabel(event: DurableEvent): string {
  const item = object(event.payload.item);
  const type = String(item.type || event.kind).toLowerCase();
  if (type.includes('command') || type.includes('tool')) return 'Tool actions';
  if (type.includes('file') || type.includes('patch')) return 'File changes';
  if (type.includes('reason')) return 'Thinking';
  if (type.includes('turn')) return 'Turn updates';
  return 'Activity';
}
// Rebuild from persisted ordered records so pagination and replay use identical rules.
export function reduceConversation(records: DurableEvent[]): ConversationEntry[] {
  const entries: ConversationEntry[] = [];
  const seen = new Set<number>();
  const messages = new Map<string, ConversationEntry>();
  const completed = new Set<string>();
  let lastDelta: { scope: string; entry: ConversationEntry } | undefined;
  for (const event of records) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    const payload = event.payload;
    const item = object(payload.item);
    const turn = object(payload.turn);
    const identity = event.item_id || item.id || payload.itemId;
    const key = identity ? `${scope(event)}:${identity}` : undefined;
    const delta = event.kind === 'item/agentMessage/delta';
    const finalMessage = item.type === 'agentMessage' && !event.kind.endsWith('/started');
    const failed = event.kind.toLowerCase().includes('error') || !!payload.error || !!item.error || !!turn.error || payload.is_error === true || item.is_error === true || ['failed', 'error'].includes(String(turn.status)) || ['failed', 'error'].includes(String(item.status || payload.status)) || (typeof item.exitCode === 'number' && item.exitCode !== 0) || (typeof payload.exitCode === 'number' && payload.exitCode !== 0);
    if (failed) {
      entries.push({ key: String(event.id), kind: 'error', text: readable(payload.message || payload.error || item.error || item.aggregatedOutput || item.content || payload.result || turn.error || 'Action failed. Expand details for the recorded result.'), events: [event] });
      lastDelta = undefined;
    } else if (event.kind === 'user') {
      entries.push({ key: String(event.id), kind: 'user', text: String(payload.text || ''), attachmentIds: Array.isArray(payload.attachment_ids) ? payload.attachment_ids.filter((id): id is string => typeof id === 'string') : [], events: [event] });
      lastDelta = undefined;
    } else if (delta || finalMessage) {
      let entry = key ? messages.get(key) : delta && lastDelta?.scope === scope(event) ? lastDelta.entry : undefined;
      const text = String(delta ? payload.delta || payload.textDelta || '' : item.text || '');
      if (!entry) {
        entry = { key: String(event.id), kind: 'assistant', text: '', events: [] };
        entries.push(entry);
        if (key) messages.set(key, entry);
      }
      if (finalMessage) { entry.text = text; if (key) completed.add(key); }
      else if (!key || !completed.has(key)) entry.text += text;
      entry.events.push(event);
      lastDelta = delta && !key ? { scope: scope(event), entry } : undefined;
    } else {
      lastDelta = undefined;
      const previous = entries[entries.length - 1];
      if (previous?.kind === 'activity' && scope(previous.events[0]) === scope(event)) previous.events.push(event);
      else entries.push({ key: String(event.id), kind: 'activity', text: '', events: [event] });
    }
  }
  return entries;
}
