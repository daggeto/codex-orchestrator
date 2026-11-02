import { createContext, useContext, useEffect, useLayoutEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type Theme = 'dark' | 'light';
export const themeKey = 'orchestrator.ui.theme.v1';
export function readTheme(storage: Pick<Storage, 'getItem'>): Theme {
  try { return storage.getItem(themeKey) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document.documentElement.classList.add('style-nova');
}
export function initializeAppearance() {
  let theme: Theme = 'dark';
  try { theme = readTheme(localStorage); } catch { /* Dark fallback. */ }
  applyTheme(theme);
}
const Appearance = createContext<{ theme: Theme; toggleTheme: () => void } | null>(null);
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return readTheme(localStorage); } catch { return 'dark'; }
  });
  useLayoutEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem(themeKey, theme); } catch { /* Appearance remains in memory. */ }
  }, [theme]);
  useEffect(() => {
    const observe = (event: StorageEvent) => {
      if (event.key === themeKey) setTheme(event.newValue === 'light' ? 'light' : 'dark');
    };
    window.addEventListener('storage', observe);
    return () => window.removeEventListener('storage', observe);
  }, []);
  return <Appearance.Provider value={{ theme, toggleTheme: () => setTheme(value => value === 'dark' ? 'light' : 'dark') }}>{children}</Appearance.Provider>;
}
export function useAppearance() {
  const value = useContext(Appearance);
  if (!value) throw new Error('AppearanceProvider is required');
  return value;
}
