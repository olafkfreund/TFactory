"""Render a per-portal Markdown test report from crawl evidence."""

from __future__ import annotations

from pathlib import Path

from . import config
from .crawler import Step


def write_report(
    portal: config.Portal,
    login_info: dict,
    steps: list[Step],
    out_dir: Path,
    video_rel: str | None,
    timestamp: str,
) -> Path:
    shots = "screenshots"
    findings: list[str] = []
    for s in steps:
        if s.console_errors:
            findings.append(
                f"- **Console error** on _{s.label}_ ({s.kind}): `{s.console_errors[0][:160]}`"
            )
        if s.note.startswith(("click failed", "failed")):
            findings.append(
                f"- **Interaction failed** on _{s.label}_ ({s.kind}): {s.note}"
            )
    if not login_info.get("logged_in"):
        findings.insert(
            0,
            f"- **Login did not complete** — {'; '.join(login_info.get('notes', [])) or 'see screencast'}",
        )

    lines: list[str] = []
    lines.append(f"# {portal.name} — UI test report")
    lines.append("")
    lines.append(f"- **Portal:** {portal.url}")
    lines.append(f"- **Run:** {timestamp}")
    lines.append(
        f"- **Auth:** Keycloak `factory` realm — MFA presented: "
        f"**{login_info.get('mfa_presented')}**, logged in: **{login_info.get('logged_in')}**"
    )
    if video_rel:
        lines.append(f"- **Screencast:** [`{video_rel}`]({video_rel})")
    lines.append("")
    nav = sum(1 for s in steps if s.kind == "nav")
    dd = sum(1 for s in steps if s.kind == "dropdown")
    dlg = sum(1 for s in steps if s.dialog_opened)
    lines.append("## Coverage")
    lines.append("")
    lines.append("| Nav items | Dropdowns | Dialogs | Screenshots | Findings |")
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| {nav} | {dd} | {dlg} | {sum(1 for s in steps if s.screenshot)} | {len(findings)} |"
    )
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    lines.extend(
        findings
        or [
            "- None — every exercised control rendered without a console error or interaction failure."
        ]
    )
    lines.append("")
    lines.append("## Walkthrough (every menu / dropdown / dialog)")
    lines.append("")
    for s in steps:
        tag = {"nav": "🧭", "dropdown": "▾", "dialog": "🗔", "page": "📄"}.get(
            s.kind, "•"
        )
        lines.append(f"### {tag} {s.label}  ({s.kind})")
        if s.url:
            lines.append(f"`{s.url}`")
        if s.screenshot:
            lines.append(f"\n![{s.label}]({shots}/{s.screenshot})\n")
        if s.note:
            lines.append(f"> {s.note}")
        if s.console_errors:
            lines.append(f"> ⚠️ console: `{s.console_errors[0][:160]}`")
        lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"
    path.write_text("\n".join(lines))
    return path
