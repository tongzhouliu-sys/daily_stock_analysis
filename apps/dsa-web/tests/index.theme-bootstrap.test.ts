// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('index.html theme bootstrap', () => {
  it('preloads the stored or system theme before React mounts', () => {
    const indexHtml = readFileSync(resolve(__dirname, '..', 'index.html'), 'utf8');

    expect(indexHtml).toContain("const storageKey = 'theme'");
    expect(indexHtml).toContain("const theme = storedTheme === 'light' || storedTheme === 'dark' ? storedTheme : systemTheme;");
    expect(indexHtml).toContain("matchMedia('(prefers-color-scheme: dark)')");
    expect(indexHtml).toContain("root.classList.remove('light', 'dark');");
    expect(indexHtml).toContain('root.classList.add(theme);');
    expect(indexHtml).toContain('root.style.colorScheme = theme;');
  });
});
