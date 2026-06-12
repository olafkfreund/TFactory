/**
 * Theme constants
 * Gruvbox is the default color theme for TFactory; additional palettes are opt-in.
 */

import type { ColorThemeDefinition } from '../types/settings';

export const COLOR_THEMES: ColorThemeDefinition[] = [
  {
    id: 'gruvbox',
    name: 'Gruvbox',
    description: 'Warm retro-groove — Gruvbox light & dark · TFactory green accent',
    previewColors: { bg: '#fbf1c7', accent: '#b8bb26', darkBg: '#282828' }
  },
];
