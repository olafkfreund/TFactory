"""Diagram generation for TFactory (#133/#136).

Greenfield: TFactory had no diagram generation. This package turns structured
data (e.g. a cloud discovery inventory) into Mermaid source that renders in
GitHub, the portal, or any Mermaid viewer — emitted into ``findings/`` so a
follow-up task can act on it with evidence attached.
"""

from .mermaid import MermaidGraph, render_cloud_topology

__all__ = ["MermaidGraph", "render_cloud_topology"]
