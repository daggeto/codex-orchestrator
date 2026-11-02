/** Changing list views preserves repository context, never a cursor or incompatible status. */
export function taskListUrl(repositoryId?: string | null, archived = false) {
  const query = new URLSearchParams();
  if (repositoryId) query.set('repository_id', repositoryId);
  if (archived) query.set('tab', 'archive');
  return `/tasks${query.size ? `?${query}` : ''}`;
}
