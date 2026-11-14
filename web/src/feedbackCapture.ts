
export interface PageCapture { route: string; captured_at: string; viewport: { width: number; height: number }; theme: string; revision: string | null }
export interface FeedbackCapture { capture: PageCapture; html: string; screenshot: string }

export async function captureFeedbackPage(): Promise<FeedbackCapture> {
  const source = document.querySelector<HTMLElement>('.app-shell');
  if (!source) throw new Error('The application page is unavailable.');
  const capture: PageCapture = { route: location.hash.slice(1) || '/', captured_at: new Date().toISOString(),
    viewport: { width: innerWidth, height: innerHeight }, theme: document.documentElement.className,
    revision: document.querySelector<HTMLMetaElement>('meta[name="app-revision"]')?.content || null };
  const clone = source.cloneNode(true) as HTMLElement;
  const originals = [source, ...source.querySelectorAll<HTMLElement>('*')];
  const copies = [clone, ...clone.querySelectorAll<HTMLElement>('*')];
  originals.forEach((node, index) => {
    const copy = copies[index];
    const style = getComputedStyle(node);
    for (const property of style) copy.style.setProperty(property, style.getPropertyValue(property));
    if (node instanceof HTMLInputElement && copy instanceof HTMLInputElement) {
      copy.value = node.type === 'password' ? '' : node.value;
      copy.setAttribute('value', copy.value);
    }
    if (node instanceof HTMLTextAreaElement) copy.textContent = node.value;
    if (node.scrollTop || node.scrollLeft) {
      copy.style.overflow = 'hidden';
      const wrapper = document.createElement('div');
      while (copy.firstChild) wrapper.append(copy.firstChild);
      wrapper.style.transform = `translate(${-node.scrollLeft}px, ${-node.scrollTop}px)`;
      copy.append(wrapper);
    }
  });
  clone.querySelectorAll('[data-feedback-widget],script').forEach(node => node.remove());
  clone.querySelectorAll('*').forEach(node => {
    for (const attribute of [...node.attributes]) if (attribute.name.startsWith('on')) node.removeAttribute(attribute.name);
  });
  clone.style.width = `${innerWidth}px`; clone.style.height = `${innerHeight}px`;
  clone.style.position = 'relative'; clone.style.inset = 'auto'; clone.style.overflow = 'hidden';
  const html = '<!doctype html><html><head><meta charset="utf-8"></head><body>' + clone.outerHTML + '</body></html>';
  const holder = document.createElement('div');
  holder.style.cssText = 'position:fixed;left:-100000px;top:0;pointer-events:none';
  holder.setAttribute('aria-hidden', 'true'); holder.append(clone); document.body.append(holder);
  try {
    const { toBlob } = await import('html-to-image');
    let timer: ReturnType<typeof setTimeout> | undefined;
    const blob = await Promise.race([
      toBlob(clone, { width: capture.viewport.width, height: capture.viewport.height, pixelRatio: 1 }),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('Page capture timed out. Please retry.')), 20000); }),
    ]).finally(() => clearTimeout(timer));
    if (!blob) throw new Error('Screenshot capture failed.');
    const screenshot = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('Could not read screenshot.')); reader.readAsDataURL(blob);
    });
    return { capture, html, screenshot };
  } finally { holder.remove(); }
}
