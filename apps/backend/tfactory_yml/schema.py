"""
.tfactory.yml Pydantic v2 Schema
=================================

Defines all models that describe a ``.tfactory.yml`` config file placed at an
AIFactory repo root.  The schema is **parse-only** — environment variable
values are intentionally NOT resolved here.  Auth models store the *name* of
the env-var (e.g. ``token_env: STAGING_API_TOKEN``), not the secret itself,
so the file can be committed to version control and shared in PRs.

Env-var resolution happens at Executor invocation time via
``tfactory_yml.secrets.resolve_env_var()``.

Decision 10 (v0.2 design spec): target addressing via single ``.tfactory.yml``
with a ``targets:`` array; subtasks reference by name.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _validate_env_var_name(name: str, field_name: str) -> str:
    """Raise ValueError if *name* is not a valid env-var identifier."""
    if not _ENV_VAR_RE.match(name):
        raise ValueError(
            f"'{field_name}' must be an uppercase env-var name matching "
            f"[A-Z_][A-Z0-9_]* (got {name!r}).  Store the variable NAME, "
            "not the secret value."
        )
    return name


# ---------------------------------------------------------------------------
# Auth models (discriminated union on 'type')
# ---------------------------------------------------------------------------


class BearerAuth(BaseModel):
    """HTTP Bearer token auth.  The token value is read from *token_env* at
    runtime; only the env-var NAME is stored here."""

    type: Literal["bearer"]
    token_env: str  # env-var name, e.g. STAGING_API_TOKEN

    @field_validator("token_env")
    @classmethod
    def _check_token_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "token_env")


class BasicAuth(BaseModel):
    """HTTP Basic auth.  Credentials are read from env vars at runtime."""

    type: Literal["basic"]
    username_env: str
    password_env: str

    @field_validator("username_env")
    @classmethod
    def _check_username_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "username_env")

    @field_validator("password_env")
    @classmethod
    def _check_password_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "password_env")


class OAuth2ClientCredentialsAuth(BaseModel):
    """OAuth 2.0 client-credentials flow."""

    type: Literal["oauth2_client_credentials"]
    token_url: AnyHttpUrl
    client_id_env: str
    client_secret_env: str
    scopes: list[str] = []

    @field_validator("client_id_env")
    @classmethod
    def _check_client_id_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "client_id_env")

    @field_validator("client_secret_env")
    @classmethod
    def _check_client_secret_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "client_secret_env")


class ServiceAccountAuth(BaseModel):
    """Kubernetes ServiceAccount token file auth."""

    type: Literal["serviceaccount"]
    token_file: str  # path to the mounted token file


class MtlsAuth(BaseModel):
    """Mutual TLS (mTLS) client certificate auth."""

    type: Literal["mtls"]
    client_cert: str  # path to PEM client cert
    client_key: str  # path to PEM private key
    ca_cert: str | None = None  # optional CA bundle path


class NoneAuth(BaseModel):
    """Explicit declaration of no authentication required."""

    type: Literal["none"]


# Union type for the ``auth:`` field (discriminated on ``type``).
AuthSpec = Annotated[
    BearerAuth
    | BasicAuth
    | OAuth2ClientCredentialsAuth
    | ServiceAccountAuth
    | MtlsAuth
    | NoneAuth,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# HealthCheck + WaitFor
# ---------------------------------------------------------------------------


class HealthCheck(BaseModel):
    """Liveness probe for HTTP-type targets."""

    path: str = "/healthz"
    expect_status: int = 200
    timeout_seconds: int = 10


class WaitFor(BaseModel):
    """Poll a URL until it responds (used with docker_compose targets)."""

    url: str
    timeout_seconds: int = 60
    expect_status: int = 200


# ---------------------------------------------------------------------------
# Target models (discriminated union on 'type')
# ---------------------------------------------------------------------------


class HttpTarget(BaseModel):
    """An HTTP/HTTPS service endpoint (browser app or REST API).

    Used for both Browser-lane targets (the app under test) and API-lane
    targets (REST/GraphQL APIs).  The ``base_url`` is the root URL; the
    Executor appends test-specific paths.

    Examples::

        # Browser lane
        - name: web
          type: http
          base_url: https://staging.example.com
          auth:
            type: bearer
            token_env: STAGING_API_TOKEN
          health_check:
            path: /healthz
            expect_status: 200

        # API lane (with OpenAPI spec for Gen-Functional context)
        - name: api
          type: http
          base_url: https://api.staging.example.com
          openapi_spec: openapi.yaml
          auth:
            type: oauth2_client_credentials
            token_url: https://auth.example.com/token
            client_id_env: API_CLIENT_ID
            client_secret_env: API_CLIENT_SECRET
    """

    type: Literal["http"]
    name: str
    base_url: AnyHttpUrl
    auth: AuthSpec | None = None
    health_check: HealthCheck | None = None
    # Optional hints consumed by the Planner / Gen-Functional
    openapi_spec: str | None = None  # path to OpenAPI spec for API lane
    selectors_hint: str | None = None  # e.g. "data_testid" or "role_based"


class KubernetesTarget(BaseModel):
    """A service accessed via a Kubernetes cluster context.

    The Executor port-forwards to the named service to make it reachable
    locally.  Only ``serviceaccount`` or ``mtls`` auth is permitted — bearer
    tokens and basic auth are not appropriate for in-cluster access.

    Examples::

        - name: cluster
          type: kubernetes
          context: prod-readonly
          namespace: example-app
          auth:
            type: serviceaccount
            token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
    """

    type: Literal["kubernetes"]
    name: str
    context: str
    namespace: str
    # Only ServiceAccount or mTLS auth makes sense for in-cluster access.
    # BearerAuth, BasicAuth, etc. would be misleading here — reject them.
    auth: Annotated[
        ServiceAccountAuth | MtlsAuth,
        Field(discriminator="type"),
    ]
    service: str | None = None
    port: int | None = None
    port_forward: bool = False


class DockerComposeTarget(BaseModel):
    """A set of services spun up via docker-compose for local testing.

    Decision 5 (v0.2): docker-compose is the default runtime for Browser-lane
    tests when a ``base_url`` override is not provided.

    Examples::

        - name: web
          type: docker_compose
          compose_file: docker-compose.test.yml
          services: [app, db, redis]
          wait_for:
            - url: http://localhost:3000/ready
              timeout_seconds: 60
    """

    type: Literal["docker_compose"]
    name: str
    compose_file: str  # path relative to repo root
    services: list[str]  # must contain at least one service name
    wait_for: list[WaitFor] = []

    @field_validator("services")
    @classmethod
    def _require_at_least_one_service(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "docker_compose target 'services' must list at least one "
                "service name (got an empty list)"
            )
        return v


class FeatureFlagTarget(BaseModel):
    """A feature-flag / gate overlay.

    Used for testing rollouts, dark launches, and A/B variants.  The
    Planner injects the flag state so Gen-Functional can write tests that
    assert the correct behaviour for each variant.

    Supported services: ``growthbook``, ``launchdarkly``, ``split``,
    ``unleash``.

    Examples::

        - name: billing-flag
          type: feature_flag
          flag_key: new-billing-flow
          service: launchdarkly
          auth:
            type: bearer
            token_env: LD_SDK_KEY
    """

    type: Literal["feature_flag"]
    name: str
    flag_key: str
    service: Literal["growthbook", "launchdarkly", "split", "unleash"]
    auth: AuthSpec | None = None


# Union of all concrete target types (discriminated on ``type``).
TargetSpec = Annotated[
    HttpTarget | KubernetesTarget | DockerComposeTarget | FeatureFlagTarget,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# TestData
# ---------------------------------------------------------------------------


class TestData(BaseModel):
    """Commands for seeding / resetting the test database."""

    fixtures_dir: str | None = None
    seed_command: str | None = None
    reset_command: str | None = None


# ---------------------------------------------------------------------------
# EvidencePolicy (Task 16 placeholder — accept freeform sub-keys)
# ---------------------------------------------------------------------------


class EvidencePolicy(BaseModel):
    """Evidence-capture policy (screenshots, video, HAR).

    Fleshed out in Task 16 / #22.  For v0.2 we accept any sub-key so that
    forward-compatible configs don't cause parse errors.
    """

    model_config = {"extra": "allow"}

    # Typed fields added in Task 16:
    # screenshots: bool = True
    # video: Literal["on", "off", "retain-on-failure"] = "retain-on-failure"
    # trace: Literal["on", "off", "retain-on-failure"] = "retain-on-failure"
    # network_har: bool = False
    # retention_days_pass: int = 7
    # retention_days_fail: int | None = None  # keep indefinitely


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class TFactoryConfig(BaseModel):
    """Parsed representation of a ``.tfactory.yml`` file.

    Example::

        version: 1
        targets:
          - name: api
            type: http
            base_url: https://api.staging.example.com
            auth:
              type: bearer
              token_env: STAGING_API_TOKEN
        test_data:
          seed_command: ./scripts/seed-test-db.sh
    """

    version: Literal[1]
    targets: list[TargetSpec]
    default_target: str | None = None
    test_data: TestData | None = None
    evidence_policy: EvidencePolicy | None = None
    # Optional path overrides (consumed by Planner / Gen-Functional)
    test_paths: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_default_target(self) -> TFactoryConfig:
        """Ensure default_target (if set) refers to a declared target name."""
        if self.default_target is not None:
            names = {t.name for t in self.targets}
            if self.default_target not in names:
                raise ValueError(
                    f"default_target {self.default_target!r} does not match "
                    f"any declared target name; known: {sorted(names)}"
                )
        return self

    # -------------------------------------------------------------------------
    # Convenience helpers (consumed by Planner, Gen-Functional, Executor)
    # -------------------------------------------------------------------------

    def target_names(self) -> list[str]:
        """Return sorted list of all declared target names."""
        return sorted(t.name for t in self.targets)

    def lookup_target(self, name: str) -> TargetSpec | None:
        """Return the target with *name*, or ``None`` if not found.

        Parameters
        ----------
        name:
            The ``name`` field of the target to find.

        Returns
        -------
        TargetSpec | None
            The matching target, or ``None``.
        """
        for target in self.targets:
            if target.name == name:
                return target
        return None

    def get_target(self, name: str) -> TargetSpec:
        """Return the target with *name*, raising ``KeyError`` if not found.

        Parameters
        ----------
        name:
            The ``name`` field of the target to find.

        Raises
        ------
        KeyError
            If no target with *name* is declared.
        """
        target = self.lookup_target(name)
        if target is None:
            raise KeyError(
                f"Target {name!r} not found in .tfactory.yml. "
                f"Declared targets: {self.target_names()}"
            )
        return target
