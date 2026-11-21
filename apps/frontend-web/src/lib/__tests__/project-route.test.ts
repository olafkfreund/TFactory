import { describe, expect, it } from 'vitest';
import { findProjectByRef, parseRoute, projectPath, projectSlug, projectSlugs, slugify } from '../project-route';
import type { Project } from '../../shared/types';

const project = (id: string, name: string, createdAt: string, slug?: string): Project =>
  ({ id, name, slug, createdAt: new Date(createdAt) }) as Project;

describe('project routes', () => {
  it('normalizes names and falls back to the id', () => {
    expect(slugify('My  Project_1!', 'x')).toBe('my-project-1');
    expect(slugify('Café', 'x')).toBe('cafe');
    expect(slugify('日本', 'abc123')).toBe('abc123');
  });

  it('reserves backend slugs and assigns derived collisions oldest-first', () => {
    const older = project('aaaaaaaa-1', 'Demo', '2026-01-01');
    const newer = project('bbbbbbbb-2', 'Demo', '2026-02-01');
    expect(projectSlugs([newer, older]).get(older.id)).toBe('demo');
    expect(projectSlug([newer, older], newer)).toBe('demo-bbbbbbbb');
    const reserved = project('c', 'Other', '2026-03-01', 'demo');
    expect(projectSlugs([older, reserved]).get(older.id)).toBe('demo-aaaaaaaa');
    const collision = project('d', 'Demo bbbbbbbb', '2026-03-01');
    expect(new Set(projectSlugs([older, newer, collision]).values()).size).toBe(3);
  });

  it('resolves canonical slug before id, then aliases', () => {
    const canonical = project('a', 'Alpha', '2026-01-01', 'shared');
    const idMatch = project('shared', 'Beta', '2026-01-02');
    const alias = project('c', 'Shared', '2026-01-03', 'other');
    expect(findProjectByRef([alias, idMatch, canonical], 'SHARED')).toBe(canonical);
    expect(findProjectByRef([alias, idMatch], 'shared')).toBe(idMatch);
    expect(findProjectByRef([alias], 'shared')).toBe(alias);
    expect(findProjectByRef([canonical], 'A')).toBe(canonical);
    expect(findProjectByRef([canonical], 'missing')).toBeUndefined();
  });

  it('keeps a collision alias after the older twin is removed', () => {
    const older = project('aaaaaaaa-1', 'Demo', '2026-01-01');
    const newer = project('bbbbbbbb-2', 'Demo', '2026-02-01');
    expect(findProjectByRef([older, newer], 'demo-bbbbbbbb')).toBe(newer);
    expect(findProjectByRef([newer], 'demo-bbbbbbbb')).toBe(newer);
  });

  it('parses routes, defaults unknown views, and round-trips encoded references', () => {
    expect(parseRoute('/p/alpha')).toEqual({ kind: 'project', ref: 'alpha', view: 'tfactory' });
    expect(parseRoute('/p/alpha/cloud')).toEqual({ kind: 'project', ref: 'alpha', view: 'cloud' });
    expect(parseRoute('/p/alpha/visual-reports')).toEqual({ kind: 'project', ref: 'alpha', view: 'visual-reports' });
    expect(parseRoute('/p/alpha/bogus')).toEqual({ kind: 'project', ref: 'alpha', view: 'tfactory' });
    expect(parseRoute('/p/%E0%A4%A')).toMatchObject({ kind: 'project' });
    expect(parseRoute(projectPath('a b', 'terminals'))).toEqual({ kind: 'project', ref: 'a b', view: 'terminals' });
    expect(projectPath('x', 'tfactory')).toBe('/p/x');
    expect(parseRoute('/projects/')).toEqual({ kind: 'projects' });
    expect(parseRoute('/')).toEqual({ kind: 'home' });
  });
});
