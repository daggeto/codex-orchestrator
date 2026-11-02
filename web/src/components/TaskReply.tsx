import { useEffect, useState } from 'react';
import { clearSubmissionIdentity, post, submissionIdentity } from '../api';
import { useOperation } from '../useOperation';
import type { AttachmentSubmission, TaskMessage } from '../types';
import type { OperationAttempt } from '../operationState';
import { Chat } from './Chat';
import { Button } from './ui/Button';
export function TaskReply({ taskId, workspaceId, disabled, unavailable, messages, refresh }: { taskId: string; workspaceId: string; disabled: boolean; unavailable: boolean; messages: TaskMessage[]; refresh: () => void }) {
  const operation = useOperation<AttachmentSubmission & { message: string }>(`task:${taskId}:message`, 'message');
  useEffect(() => {
    if (operation.current) return;
    try {
      const legacy = JSON.parse(sessionStorage.getItem(`task:${taskId}:submission`) || 'null');
      if (typeof legacy?.key === 'string' && typeof legacy.fingerprint === 'string') operation.prepare(JSON.parse(legacy.fingerprint), 0, legacy.key);
    } catch { /* Invalid legacy evidence is retained for inspection. */ }
  }, [taskId, operation.current]);
  useEffect(() => {
    const attempt = operation.current;
    if (attempt && operation.unresolved && messages.some(message => 'idempotency_key' in message && message.idempotency_key === attempt.id)) { operation.settle(attempt, 'accepted'); clearSubmissionIdentity(`task:${taskId}:submission`, attempt.id); }
  }, [messages, operation.current]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function send(attempt: OperationAttempt<AttachmentSubmission & { message: string }>) {
    try {
      await post(`/api/tasks/${encodeURIComponent(taskId)}/messages`, { ...attempt.request, idempotency_key: attempt.id });
      operation.settle(attempt, 'accepted'); clearSubmissionIdentity(`task:${taskId}:submission`, attempt.id); refresh();
    } catch (reason) { const receipt = operation.settle(attempt, 'uncertain', undefined, String(reason)); if (receipt.status !== 'accepted') throw reason; }
  }
  return <>
    <Chat label="Continue conversation" rows={3} draftKey={`task:${taskId}`} messages={[]} attachmentContext={{ workspaceId, taskId }} disabled={disabled || busy} submitDisabled={operation.unresolved || unavailable}
      submitReason={operation.unresolved ? 'Check the saved message before sending another. New draft text stays editable.' : disabled || unavailable ? 'Wait for fresh task state and an available agent before sending.' : undefined}
      onSubmit={async (message, attachments) => {
        const payload = { message, ...attachments };
        await send(operation.prepare(payload, 0, submissionIdentity(`task:${taskId}:submission`, payload)));
      }} />
    {operation.unresolved && <Button disabled={disabled || busy} onClick={async () => {
      if (!operation.current?.request || busy) return;
      setBusy(true); setError('');
      try { await send(operation.current); } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
    }}>Check saved message</Button>}
    {operation.current?.status === 'accepted' && <p role="status">Message accepted. Agent execution is shown above.</p>}
    {!operation.durable && <p role="alert">Request recovery is available only while this page stays open.</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </>;
}
