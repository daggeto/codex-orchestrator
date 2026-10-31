import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type Theme = 'dark' | 'light';
export const themeKey = 'orchestrator.ui.theme.v1';
export function readTheme(storage: Pick<Storage, 'getItem'>): Theme {
  try { return storage.getItem(themeKey) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}
const Appearance = createContext<{ theme: Theme; toggleTheme: () => void } | null>(null);
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return readTheme(localStorage); } catch { return 'dark'; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(themeKey, theme); } catch { /* Appearance remains in memory. */ }
  }, [theme]);
  return <Appearance.Provider value={{ theme, toggleTheme: () => setTheme(value => value === 'dark' ? 'light' : 'dark') }}>{children}</Appearance.Provider>;
}
export function useAppearance() {
  const value = useContext(Appearance);
  if (!value) throw new Error('AppearanceProvider is required');
  return value;
}
