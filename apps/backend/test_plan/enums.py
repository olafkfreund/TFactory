#!/usr/bin/env python3
"""
Enumerations for Implementation Plan
=====================================

Defines all enum types used in implementation plans: workflow types,
phase types, subtask statuses, and verification types.
"""

from enum import Enum


class WorkflowType(str, Enum):
    """Types of workflows with different phase structures."""

    FEATURE = "feature"  # Multi-service feature (phases = services)
    REFACTOR = "refactor"  # Stage-based (add new, migrate, remove old)
    INVESTIGATION = "investigation"  # Bug hunting (investigate, hypothesize, fix)
    MIGRATION = "migration"  # Data migration (prepare, test, execute, cleanup)
    SIMPLE = "simple"  # Single-service, minimal overhead
    DEVELOPMENT = "development"  # General development work
    ENHANCEMENT = "enhancement"  # Improving existing features


class PhaseType(str, Enum):
    """Types of phases within a workflow."""

    SETUP = "setup"  # Project scaffolding, environment setup
    IMPLEMENTATION = "implementation"  # Writing code
    INVESTIGATION = "investigation"  # Research, debugging, analysis
    INTEGRATION = "integration"  # Wiring services together
    CLEANUP = "cleanup"  # Removing old code, polish


class SubtaskStatus(str, Enum):
    """Status of a subtask."""

    PENDING = "pending"  # Not started
    IN_PROGRESS = "in_progress"  # Currently being worked on
    COMPLETED = "completed"  # Completed successfully (matches JSON format)
    BLOCKED = "blocked"  # Can't start (dependency not met or undefined)
    FAILED = "failed"  # Attempted but failed


class VerificationType(str, Enum):
    """How to verify a subtask is complete."""

    COMMAND = "command"  # Run a shell command
    API = "api"  # Make an API request
    BROWSER = "browser"  # Browser automation check
    COMPONENT = "component"  # Component renders correctly
    MANUAL = "manual"  # Requires human verification
    NONE = "none"  # No verification needed (investigation)


class Lane(str, Enum):
    """TFactory test lanes — added in Task 3 (#4).

    Each Subtask carries a lane tag so the Planner can emit a mixed
    plan that downstream generators dispatch on. At MVP only FUNCTIONAL
    is lit (Task 6); SAST/DAST/FUZZ/MUTATION wire up in phases 2-5.
    """

    FUNCTIONAL = "functional"  # pytest / vitest unit + integration tests
    SAST = "sast"              # Semgrep / Bandit / dep CVE / secrets scan
    DAST = "dast"              # OWASP ZAP attack against running service
    FUZZ = "fuzz"              # LLM-generated fuzz harnesses (atheris/jsfuzz)
    MUTATION = "mutation"      # mutmut / stryker mutation testing


# Backwards compatibility aliases
ChunkStatus = SubtaskStatus
