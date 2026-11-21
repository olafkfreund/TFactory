// URL <-> project mapping for /p/:slug[/:view]. Pure: no store, no router.
import type { Project } from '../shared/types';

export const PROJECT_VIEWS = [
  'tfactory', 'terminals', 'editor', 'context', 'github-issues', 'github-prs',
  'changelog', 'insights', 'worktrees', 'agent-tools', 'skills', 'cloud', 'visual-reports',
] as const;
export type ProjectView = (typeof PROJECT_VIEWS)[number];

export type AppRoute =
  | { kind: 'project'; ref: string; view: ProjectView }
  | { kind: 'projects' }
  | { kind: 'home' };

export function slugify(name: string, fallback: string): string {
  const slug = name.normalize('NFKD').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return slug || fallback;
}

const created = (project: Project) => new Date(project.createdAt).getTime() || 0;

export function projectSlugs(projects: Project[]): Map<string, string> {
  const slugs = new Map<string, string>();
  const used = new Set<string>();
  for (const project of projects) {
    if (project.slug) {
      slugs.set(project.id, project.slug);
      used.add(project.slug);
    }
  }
  const derived = projects.filter((project) => !project.slug)
    .sort((a, b) => created(a) - created(b) || a.id.localeCompare(b.id));
  for (const project of derived) {
    const base = slugify(project.name, project.id);
    const withId = `${base}-${project.id.slice(0, 8)}`;
    let slug = used.has(base) ? withId : base;
    for (let n = 2; used.has(slug); n++) slug = `${withId}-${String(n)}`;
    used.add(slug);
    slugs.set(project.id, slug);
  }
  return slugs;
}

export const projectSlug = (projects: Project[], project: Project): string =>
  projectSlugs(projects).get(project.id) ?? project.id;

export function findProjectByRef(projects: Project[], ref: string): Project | undefined {
  const normalized = ref.toLowerCase();
  const slugs = projectSlugs(projects);
  return projects.find((project) => slugs.get(project.id)?.toLowerCase() === normalized)
    ?? projects.find((project) => project.id.toLowerCase() === normalized)
    ?? projects.find((project) =>
      project.slug?.toLowerCase() === normalized
      || slugify(project.name, project.id) === normalized
      || `${slugify(project.name, project.id)}-${project.id.slice(0, 8)}`.toLowerCase() === normalized);
}

export function parseRoute(pathname: string): AppRoute {
  const match = /^\/p\/([^/]+)(?:\/([^/]+))?/.exec(pathname);
  if (match) {
    const view = PROJECT_VIEWS.find((candidate) => candidate === match[2]) ?? 'tfactory';
    let ref = match[1];
    try { ref = decodeURIComponent(ref); } catch { /* Keep malformed references for not-found. */ }
    return { kind: 'project', ref, view };
  }
  if (/^\/projects\/?$/.test(pathname)) return { kind: 'projects' };
  return { kind: 'home' };
}

export const projectPath = (slug: string, view?: ProjectView | string): string =>
  `/p/${encodeURIComponent(slug)}${view && view !== 'tfactory' ? `/${view}` : ''}`;
