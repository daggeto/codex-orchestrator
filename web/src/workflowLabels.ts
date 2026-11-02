const labels: Record<string, string> = {
  'ux-assess': 'UX planning',
  plan: 'Technical planning',
  'plan-approval': 'Developer plan approval',
  implement: 'Implementation',
  review: 'Code review',
  revise: 'Implementation revision',
  'ux-consult': 'UX consultation',
  'architect-consult': 'Architecture consultation',
  'final-ux-gate': 'Final UX report selection',
  'ux-report': 'Final UX report (advisory)',
  done: 'Completion report',
};

export function workflowStepLabel(step: string): string {
  return labels[step] || step;
}
