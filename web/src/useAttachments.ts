import { useEffect, useRef, useState } from 'react';
import { get, post, uploadAttachment } from './api';
import type { Attachment, AttachmentDraft, AttachmentLimits, AttachmentSubmission } from './types';

export interface Selection extends Attachment { local?: File; localError?: string }
export interface AttachmentContext { workspaceId: string; taskId?: string }
export function useAttachments(storageKey: string, context?: AttachmentContext) {
  const [saved] = useState<{ draftId?: string; files?: Selection[] }>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); return value && typeof value === 'object' && (!value.files || Array.isArray(value.files)) ? value : {}; } catch { return {}; }
  });
  const [files, setFiles] = useState<Selection[]>(() => (saved.files || []).map(file => file.state === 'uploading' ? { ...file, state: 'failed', localError: 'Upload interrupted. Recover uploaded files or reselect this file.' } : file));
  const [draftId, setDraftId] = useState(saved.draftId);
  const [limits, setLimits] = useState<AttachmentLimits>();
  const [error, setError] = useState('');
  const [durable, setDurable] = useState(true);
  const [recovering, setRecovering] = useState(!!saved.draftId);
  const [recoveryRequired, setRecoveryRequired] = useState(!!saved.draftId);
  const [submitted, setSubmitted] = useState(false);
  const [acceptedTaskId, setAcceptedTaskId] = useState<string>();
  const draftRef = useRef(saved.draftId);
  const creating = useRef<Promise<string> | null>(null);
  const selections = useRef(files);
  const uploadCount = useRef(0);
  const [removing, setRemoving] = useState(false);
  const replace = (update: (previous: Selection[]) => Selection[]) => {
    selections.current = update(selections.current); setFiles(selections.current);
  };
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ draftId, files: files.map(({ local: _file, ...row }) => row) })); } catch { setDurable(false); }
  }, [storageKey, files, draftId]);
  useEffect(() => {
    if (!context) return;
    const abort = new AbortController();
    void get<AttachmentLimits>('/api/attachment-limits', abort.signal).then(setLimits).catch(reason => { if (!abort.signal.aborted) setError(String(reason)); });
    return () => abort.abort();
  }, [context?.workspaceId]);
  async function recover() {
    if (!draftRef.current) return;
    setRecovering(true); setRecoveryRequired(true); setError('');
    try {
      const draft = await get<AttachmentDraft>(`/api/attachment-drafts/${encodeURIComponent(draftRef.current)}`);
      // The server may have completed an upload after the browser last saved
      // its selections. Recover those rows too, including accepted submissions.
      replace(previous => {
        const known = new Set(previous.map(file => file.attachment_id));
        const recovered: Selection[] = previous.map(file => {
          const found = draft.files.find(row => row.attachment_id === file.attachment_id);
          return found ? { ...found, local: file.local, localError: found.state === 'uploading' ? 'Upload is still running. Recover again shortly.' : undefined } : { ...file, state: 'failed', localError: 'File was not uploaded. Reselect it.' };
        });
        return [...recovered, ...draft.files.filter(file => !known.has(file.attachment_id) && file.state !== 'deleted')];
      });
      if (draft.state === 'submitted') {
        setSubmitted(true);
        setRecoveryRequired(false);
        const owners = new Set(draft.files.map(file => file.task_id).filter((id): id is string => !!id));
        if (owners.size === 1) setAcceptedTaskId([...owners][0]);
        else setError('These files were accepted, but their task could not be identified. Open task history to find the accepted request.');
        return;
      }
      if (draft.state !== 'open' || Date.parse(draft.expires_at) <= Date.now()) {
        replace(previous => previous.map(file => ({ ...file, state: 'failed', localError: 'Draft expired. Remove and reselect this file.' })));
        setRecoveryRequired(false);
        throw new Error('Upload draft expired. Remove selections and reselect files for a new message.');
      }
      setLimits(draft.limits);
      setRecoveryRequired(false);
    } catch (reason) { setError(String(reason)); }
    finally { setRecovering(false); }
  }
  useEffect(() => { if (saved.draftId) void recover(); }, []);
  async function ensureDraft() {
    if (!context) throw new Error('Choose a workspace before attaching files');
    if (draftRef.current) return draftRef.current;
    if (!creating.current) creating.current = post<AttachmentDraft>('/api/attachment-drafts', {
      workspace_id: context.workspaceId, task_id: context.taskId,
    }).then(draft => { draftRef.current = draft.draft_id; setDraftId(draft.draft_id); return draft.draft_id; }).finally(() => { creating.current = null; });
    return creating.current;
  }
  async function upload(selection: Selection) {
    if (recoveryRequired || recovering || submitted) return;
    if (!selection.local) { setError('Reselect this file from your device to retry.'); return; }
    uploadCount.current += 1;
    replace(previous => previous.map(row => row.attachment_id === selection.attachment_id ? { ...row, state: 'uploading', localError: undefined } : row));
    try {
      const did = await ensureDraft();
      const result = await uploadAttachment(did, selection.attachment_id, selection.local);
      replace(previous => previous.map(row => row.attachment_id === selection.attachment_id ? { ...result, local: selection.local } : row));
    } catch (reason) {
      replace(previous => previous.map(row => row.attachment_id === selection.attachment_id ? { ...row, state: 'failed', localError: String(reason) } : row));
    } finally { uploadCount.current -= 1; }
  }
  async function select(selected: FileList | File[]) {
    if (!limits?.enabled || recoveryRequired || recovering || submitted) return;
    const incoming = Array.from(selected);
    if (selections.current.length + incoming.length > limits.files_per_message) { setError(`Choose at most ${limits.files_per_message} files per message.`); return; }
    setError('');
    const rows: Selection[] = incoming.map(local => ({ attachment_id: crypto.randomUUID(), draft_id: '', task_id: null,
      filename: local.name, media_type: local.type, byte_count: local.size, width: null, height: null, error: null,
      state: 'uploading', local }));
    replace(previous => [...previous, ...rows]);
    for (const row of rows) {
      if (row.byte_count > limits.max_file_bytes) {
        replace(previous => previous.map(file => file.attachment_id === row.attachment_id ? { ...file, state: 'failed', localError: `File exceeds ${limits.max_file_bytes / 1024 / 1024} MiB. Choose a smaller file.` } : file));
      } else await upload(row);
    }
  }
  async function remove(id: string) {
    if (removing || uploadCount.current || recoveryRequired || recovering || submitted) return;
    setRemoving(true); setError('');
    try {
      // A failed draft may be expired. Removing it locally is safe: it cannot be
      // submitted, and server expiry owns deletion of its unbound resources.
      if (draftRef.current) {
        const response = await fetch(`/api/attachment-drafts/${encodeURIComponent(draftRef.current)}/files/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!response.ok && response.status !== 404 && response.status !== 409) throw new Error('Could not remove file. Retry.');
        if (response.status === 409) {
          const result = await response.json();
          if (String(result.error).includes('running')) throw new Error(result.error);
        }
      }
      replace(previous => previous.filter(file => file.attachment_id !== id));
      if (!selections.current.length) { draftRef.current = undefined; setDraftId(undefined); }
    } catch (reason) { setError(String(reason)); }
    finally { setRemoving(false); }
  }
  function clear() {
    replace(() => []); draftRef.current = undefined; setDraftId(undefined); setError('');
    setSubmitted(false); setAcceptedTaskId(undefined); setRecoveryRequired(false);
    try { sessionStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
  }
  const ready = !recoveryRequired && !submitted && !recovering && !removing && files.every(file => file.state === 'ready');
  const payload: AttachmentSubmission = { ...(draftId ? { draft_id: draftId } : {}), attachment_ids: files.map(file => file.attachment_id) };
  return { files, limits, error, durable, ready, recovering, recoveryRequired, removing, submitted, acceptedTaskId, select, upload, remove, recover, clear, payload };
}
export type AttachmentController = ReturnType<typeof useAttachments>;
