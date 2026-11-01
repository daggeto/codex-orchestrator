import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AttachmentList } from './AttachmentList';
import type { Attachment } from '../types';
const file: Attachment = { attachment_id: 'a', draft_id: 'd', task_id: 't', filename: '<report>.txt',
  media_type: 'application/octet-stream', byte_count: 10, width: null, height: null, state: 'ready', error: null };
describe('attachment visibility', () => {
  it('escapes names and uses task-scoped content URLs', () => {
    const html = renderToStaticMarkup(<AttachmentList taskId="t" attachments={[file]} />);
    expect(html).toContain('&lt;report&gt;.txt');
    expect(html).toContain('/api/tasks/t/attachments/a/content');
    expect(html).not.toContain('<img');
  });
  it('explains permanent deletion without a broken content link', () => {
    const html = renderToStaticMarkup(<AttachmentList taskId="t" attachments={[{ ...file, state: 'deleted' }]} />);
    expect(html).toContain('History cannot restore this file');
    expect(html).not.toContain('/content');
  });
});
