import { useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { AttachmentController, Selection } from '../useAttachments';
import { Button } from './ui/Button';

export function LocalPreview({ file }: { file: Selection }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!file.local || !['image/png', 'image/jpeg', 'image/webp'].includes(file.local.type)) return;
    const value = URL.createObjectURL(file.local); setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [file.local]);
  return url ? <img className="attachment-preview" src={url} alt={`Selected ${file.filename}`} /> : null;
}
export function AttachmentPicker({ controller: a, disabled = false }: { controller: AttachmentController; disabled?: boolean }) {
  const picker = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const blocked = disabled || a.removing || a.recovering || a.recoveryRequired || !a.limits?.enabled;
  const drop = (event: DragEvent) => { event.preventDefault(); event.stopPropagation(); if (!blocked) void a.select(event.dataTransfer.files); };
  return <div className="attachment-picker" onDragOver={event => event.preventDefault()} onDrop={drop}>
    <input ref={picker} type="file" multiple hidden disabled={blocked} aria-label="Choose attachments" onChange={event => { if (event.target.files) void a.select(event.target.files); event.target.value = ''; }} />
    <Button type="button" ref={button} disabled={blocked} onClick={() => picker.current?.click()}>Attach files</Button>
    <small>{a.limits?.enabled ? `Drop files here or use Attach files. Up to ${a.limits.files_per_message} files, ${a.limits.max_file_bytes / 1024 / 1024} MiB each.` : 'Attachments become available after source approval and service configuration.'}</small>
    <ul className="attachment-list">{a.files.map(file => <li key={file.attachment_id}>
      <LocalPreview file={file} /><span className="attachment-name">{file.filename}<small>{(file.byte_count / 1024).toFixed(1)} KiB · {file.state}{file.localError || file.error ? ` · ${file.localError || file.error}` : ''}</small></span>
      {file.state === 'failed' && file.local && <Button disabled={disabled || a.removing || a.recovering || a.recoveryRequired} onClick={() => void a.upload(file)}>Retry {file.filename}</Button>}
      <Button disabled={disabled || a.removing || a.recovering || a.recoveryRequired || a.files.some(item => item.state === 'uploading')} onClick={async () => { await a.remove(file.attachment_id); button.current?.focus(); }}>Remove {file.filename}</Button>
    </li>)}</ul>
    {(!!a.files.length || a.recoveryRequired) && <Button disabled={disabled || a.recovering} onClick={() => void a.recover()}>{a.recoveryRequired ? 'Retry file recovery' : 'Recover uploaded files'}</Button>}
    <p role="status" aria-live="polite">{a.recovering ? 'Recovering uploads…' : a.recoveryRequired ? 'Files must be recovered before sending. Retry file recovery.' : a.files.length ? `${a.files.filter(file => file.state === 'ready').length} of ${a.files.length} files ready` : ''}</p>
    {!a.durable && <p role="alert">File selections are held only in memory. Keep this page open or reselect files after reloading.</p>}
    {a.error && <p className="error" role="alert">{a.error}</p>}
  </div>;
}
