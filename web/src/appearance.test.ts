import { describe, expect, it } from 'vitest';
import { readTheme, themeKey } from './appearance';

describe('stored appearance', () => {
  it('reads the versioned theme preference', () => {
    expect(readTheme({ getItem: key => key === themeKey ? 'light' : null })).toBe('light');
  });
  it.each([null, '', 'dark', 'system', 'LIGHT'])('defaults %s to dark', value => {
    expect(readTheme({ getItem: () => value })).toBe('dark');
  });
  it('keeps the app usable when storage is unavailable', () => {
    expect(readTheme({ getItem: () => { throw new Error('Storage denied'); } })).toBe('dark');
  });
});
