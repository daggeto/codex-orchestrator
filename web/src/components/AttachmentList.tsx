import type { Attachment } from '../types';
export const attachmentContent = (taskId: string, id: string) => `/api/tasks/${encodeURIComponent(taskId)}/attachments/${encodeURIComponent(id)}/content`;
export function AttachmentList({ taskId, attachments }: { taskId: string; attachments: Attachment[] }) {
  if (!attachments.length) return null;
  return <ul className="attachment-list">{attachments.map(file => <li key={file.attachment_id}>
    {file.state === 'ready' && file.width && <a href={attachmentContent(taskId, file.attachment_id)} target="_blank" rel="noreferrer"><img loading="lazy" className="attachment-preview" src={attachmentContent(taskId, file.attachment_id)} alt={file.filename} /></a>}
    <span className="attachment-name">{file.state === 'ready' ? <a href={attachmentContent(taskId, file.attachment_id)} target="_blank" rel="noreferrer">{file.filename}</a> : file.filename}
      <small>{(file.byte_count / 1024).toFixed(1)} KiB · {file.state === 'deleted' ? 'Permanently removed during worktree cleanup. History cannot restore this file.' : file.state === 'deleting' ? 'Cleanup pending. This file is unavailable while deletion is retried.' : file.state === 'ready' ? file.media_type : file.error || file.state}</small></span>
  </li>)}</ul>;
}
