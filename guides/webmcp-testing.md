# WebMCP testing (design doc — PLANNED, experimental)

> **Status: planned / not built.** This is the design doc for the WebMCP epic
> ([#332](https://github.com/olafkfreund/TFactory/issues/332)); nothing here ships
> until the child issues land, and everything is gated **default-off** behind a
> feature flag. Tracked: #333–#339.

## What WebMCP is

[WebMCP](https://www.webfuse.com/blog/what-is-webmcp-the-practical-guide-to-the-web-model-context-protocol)
is a **W3C Community-Group draft** (Feb 2026; Web ML CG, Microsoft + Google
editors) — a browser API, `navigator.modelContext.registerTool()`, that lets a
web page expose **typed, callable tools to an AI agent running in the browser**
(declarative HTML-form attributes or imperative JS). A page's registered tools
become a **new public interface** — agent-facing, with typed I/O — that needs
testing like any API.

> **Maturity caveat.** WebMCP is a CG report, **not** a formal/Track standard.
> The only working implementation today is **Chrome 146 Canary behind a flag**;
> Edge likely next, Firefox/Safari uncommitted. Adoption is thin. TFactory treats
> this as a **forward bet** — experimental, flag-gated, not on the v1 critical
> path.

## Why it's on-mission for TFactory

TFactory's moat is the **verdict pipeline** (proving a generated test is worth
keeping — mutation-kill, 3× stability, coverage-delta, CI-parity, semantic
relevance), not test generation. A page's WebMCP tools are a surface almost
nobody verifies. Bringing the existing pipeline to bear on it = *"the first test
tool that verifies (and is reachable via) agentic web surfaces."*

## Three leverage directions

### A. Generate tests *for* WebMCP surfaces — a new `webmcp` lane (the differentiator)
A 6th modality alongside `unit / browser / api / integration / mutation`,
reusing the browser lane's Playwright/Chromium harness (Chrome + the WebMCP
flag):
1. **Discover** (#334) — enumerate `navigator.modelContext`: each tool's name,
   input schema, description → a normalized manifest in `context/`.
2. **Generate** (#335) — per tool, tests that invoke it with valid / invalid /
   boundary args and assert the **structured result** *and* the **page
   side-effects** the tool claims to make.
3. **Score** (#336) — the existing verdict signals: contract/schema adherence,
   **mutation** (mutate an assertion on the return → must be KILLED), **3×
   stability**, semantic relevance.
4. **Security** (#337) — agentic tools can do real damage: confirmation-gating
   on destructive tools, argument injection, auth/permission enforcement.
   (Distinct from app SAST/DAST, which stays out of scope per the testing model.)

### B. Make the browser lane *less flaky* via WebMCP tools (#338)
When a SUT exposes WebMCP tools, generated browser tests can **call the app's
`login`/`addToCart` tool** instead of scripting brittle selectors → more
semantic, far less flaky. Complements flake-lint + 3× stability.

### C. Expose TFactory *itself* as WebMCP tools (#333 — first pilot)
The portal `registerTool()`s the actions already shipped — `ingestSpec`
(`/api/specs/ingest`, #314), `listTasks`, `getTriageReport`, `approve/merge`,
`rerunLane` — so a **browser-side agent (e.g. Claude in Chrome) drives TFactory
from the open portal tab**, no `acw_` token / MCP-server setup. The in-browser
analog of the remote-MCP control plane (#50) + WS2 ingest; lowest effort since
the underlying actions exist.

## Architecture (planned)

- New lane in the lane spine / `lang_registry` + a `frameworks/webmcp`
  descriptor (runtime = the browser/Playwright image launched with the Chrome
  WebMCP flag).
- Discovery + execution under the browser-lane runtime (`tools/runners/`).
- Portal `registerTool()` wraps the existing `tfactory-api` client methods.

## Enabling (planned flags)

- `TFACTORY_WEBMCP_LANE` (env) / `.tfactory.yml` — enable the `webmcp` lane.
  **Default off.**
- A pinned Chrome build + the WebMCP flag in the browser-lane runtime image.
- Portal tool-exposure (#333) is also default-off until WebMCP support broadens
  beyond Chrome Canary.

## References
- [What Is WebMCP? — Webfuse](https://www.webfuse.com/blog/what-is-webmcp-the-practical-guide-to-the-web-model-context-protocol)
- [WebMCP Cheat Sheet (W3C Browser AI Tool API) — Webfuse](https://www.webfuse.com/webmcp-cheat-sheet)
- [WebMCP Reality Check (May 2026) — Studio Meyer](https://studiomeyer.io/en/blog/webmcp-reality-check-may-2026)
- Epic: [#332](https://github.com/olafkfreund/TFactory/issues/332) · child issues #333–#339
