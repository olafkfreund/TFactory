---
layout: default
title: Portal Gallery
permalink: /gallery/
nav_order: 5.6
---

# Portal gallery

A walk through the live TFactory portal at
[tfactory.freundcloud.org.uk](https://tfactory.freundcloud.org.uk), captured
against real verify runs. These are authenticated screenshots of the running
system — not mock-ups. Where a tab is empty or a run did not reach a level, the
UI says so plainly; we kept those shots rather than staging green ones.

## The front door

<figure class="reveal" markdown="1">

![TFactory login — API-token or SSO]({{ '/static/img/gallery/01-login.png' | relative_url }})

<figcaption>The login screen. Authenticate with an API token (or SSO); the SPA
validates the token against the API before rendering the app.</figcaption>

</figure>

## The pipeline board

<figure class="reveal" markdown="1">

![The TFactory pipeline board — Plan, Generate, Execute, Report columns with live tasks]({{ '/static/img/gallery/02-home-projects.png' | relative_url }})

<figcaption>The four-stage pipeline (Plan, Generate, Execute, Report) above
columns of real tasks — including a triaged Report-column task and a failed run.
This is the live state, in-pod verify path.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![A task in the planning stage with lane chips and planner warnings]({{ '/static/img/gallery/03-pipeline-board.png' | relative_url }})

<figcaption>A task open on its Status tab — lane chips (unit · browser · api ·
integration · mutation), subtask counts, and any planner warnings.</figcaption>

</figure>

## A finished task, tab by tab

<figure class="reveal" markdown="1">

![Task detail — overview]({{ '/static/img/gallery/05-task-detail.png' | relative_url }})

<figcaption>Task detail opens on Status. The tab strip — Status · Lanes ·
Verdicts · Report · Acceptance · Logs · Evidence — is enabled per tab as the run
produces the data behind it.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Verdicts tab — real reject verdicts with the five-signal breakdown]({{ '/static/img/gallery/08-task-verdicts.png' | relative_url }})

<figcaption>Verdicts. Each generated test gets a verdict (accept / flag / reject)
backed by five signals: coverage, stability, mutation, lint-promotion, and
semantic relevance.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Report tab — VAL ladder above the triage buckets]({{ '/static/img/gallery/09-task-report.png' | relative_url }})

<figcaption>The Report tab leads with the Verification Assurance Level — exactly
how far the run was verified — above the Triager's dedup / commit / flag / reject
buckets.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Acceptance tab — per-criterion fidelity, honest UNVERIFIED labels]({{ '/static/img/gallery/10-task-acceptance.png' | relative_url }})

<figcaption>Acceptance-criteria fidelity. Each criterion is graded against a test
that actually ran; criteria with no passing test are labelled UNVERIFIED rather
than rounded up.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Logs tab — phase-by-phase agent logs]({{ '/static/img/gallery/11-task-logs.png' | relative_url }})

<figcaption>Logs. The phase-by-phase record of the five agents running the
pipeline.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Evidence tab — honest about an absent browser lane]({{ '/static/img/gallery/12-task-evidence.png' | relative_url }})

<figcaption>Evidence. For browser-lane runs this is a gallery of screenshots and
recordings; for a task with no browser lane it says so plainly.</figcaption>

</figure>

## A failed run, shown honestly

<figure class="reveal" markdown="1">

![A failed task — replan budget exhausted, with planner warnings]({{ '/static/img/gallery/13-failed-task.png' | relative_url }})

<figcaption>A failed run on the in-pod path: the planner exhausted its replan
budget and the run is marked <code>failed</code> rather than looping forever. The
warnings explain why.</figcaption>

</figure>

## Inside a project

<figure class="reveal" markdown="1">

![The MCP server configuration and agent roster]({{ '/static/img/gallery/19-mcp.png' | relative_url }})

<figcaption>MCP. Which MCP servers are available to the agents, plus the agent
roster (Spec Gatherer, Researcher, Writer, Critic, …) and their models.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The Files browser]({{ '/static/img/gallery/18-files.png' | relative_url }})

<figcaption>Files. Browse the task workspace — generated tests, contract,
findings.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![Visual Reports]({{ '/static/img/gallery/21-visual-reports.png' | relative_url }})

<figcaption>Visual Reports — visual-inspection baselines and diffs.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![The GitHub PRs view]({{ '/static/img/gallery/22-github-prs.png' | relative_url }})

<figcaption>GitHub PRs. The PR endgame, surfaced inside the project once a GitHub
token and repo are configured.</figcaption>

</figure>

<figure class="reveal" markdown="1">

![Test Plans — GitHub-connected view]({{ '/static/img/gallery/20-test-plans.png' | relative_url }})

<figcaption>Test Plans. Connects to GitHub to organise plans against issues.</figcaption>

</figure>

---

These screenshots were captured by `scripts/capture-portal-gallery.ts` driving
the live portal with a real form login (Playwright + Chromium) and saved to
`docs/static/img/gallery/`.
