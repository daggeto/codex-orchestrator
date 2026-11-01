import type { DurableEvent } from '../types';
import { activityLabel } from '../conversation';
export function ActivityGroup({ events }: { events: DurableEvent[] }) {
  const counts = new Map<string, number>();
  for (const event of events) { const label = activityLabel(event); counts.set(label, (counts.get(label) || 0) + 1); }
  return <details className="activity-group"><summary>{[...counts].map(([label, count]) => `${label} · ${count} events`).join(', ')}</summary>
    {events.map(event => <details key={event.id}><summary>{activityLabel(event)} <small>{event.kind}</small></summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>)}
  </details>;
}
