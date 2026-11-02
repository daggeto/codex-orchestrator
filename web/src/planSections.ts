interface PlanSection { title: string; markdown: string; preview: string; hasTldr: boolean }

function section(title: string, markdown: string): PlanSection {
  const paragraphs = markdown.trim().split(/\n\s*\n/);
  const tldr = paragraphs.find(text => /^\s*(?:\*\*)?TL;DR(?:\*\*)?:?(?:\*\*)?\s*/i.test(text));
  const excerpt = tldr || paragraphs.find(text => text.trim() && !/^\s*(?:#|```|~~~|\|)/.test(text)) || title;
  const plain = excerpt.replace(/^\s*(?:\*\*)?TL;DR(?:\*\*)?:?(?:\*\*)?\s*/i, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`]/g, '')
    .replace(/^\s*(?:[-+>] |\d+[.)] )/gm, '').replace(/\s+/g, ' ').trim();
  return { title, markdown, hasTldr: Boolean(tldr), preview: plain.length > 240 ? `${plain.slice(0, 237).trimEnd()}…` : plain };
}

// Keep the original Markdown intact; these sections are only a reading view.
export function planSections(markdown: string): PlanSection[] {
  const lines = markdown.split('\n');
  const headings: { start: number; end: number; level: number; title: string }[] = [];
  let fence: { character: string; length: number } | undefined;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      if (marker && marker[1][0] === fence.character && marker[1].length >= fence.length && !line.slice(marker[0].length).trim()) fence = undefined;
      continue;
    }
    if (marker) { fence = { character: marker[1][0], length: marker[1].length }; continue; }
    const atx = /^ {0,3}(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    const setext = line.trim() && /^ {0,3}(=+|-+)\s*$/.exec(lines[index + 1] || '');
    if (atx) headings.push({ start: index, end: index + 1, level: atx[1].length, title: atx[2] });
    else if (setext) {
      headings.push({ start: index, end: index + 2, level: setext[1][0] === '=' ? 1 : 2, title: line.trim() });
      index++;
    }
  }
  if (!headings.length) return [section('Plan overview', markdown)];
  // A single document title should not swallow all of its subsections.
  const first = headings[0];
  const hasTitle = first.level === 1 && headings.filter(heading => heading.level === 1).length === 1;
  const contentHeadings = hasTitle ? headings.slice(1) : headings;
  const level = Math.min(...contentHeadings.map(heading => heading.level));
  const boundaries = headings.filter(heading => heading === first || heading.level <= level);
  const result: PlanSection[] = [];
  const preamble = lines.slice(0, first.start).join('\n').trim();
  if (preamble) result.push(section('Overview', preamble));
  boundaries.forEach((heading, index) => {
    const body = lines.slice(heading.end, boundaries[index + 1]?.start ?? lines.length).join('\n').trim();
    if (body) result.push(section(heading.title, body));
  });
  return result.length ? result : [section('Plan overview', markdown)];
}
