"""
tfactory_yml — .tfactory.yml schema, parser, and secret helpers
================================================================

Public API::

    from tfactory_yml import (
        TFactoryConfig,
        Target,
        TargetSpec,
        Auth,
        AuthSpec,
        HealthCheck,
        WaitFor,
        TestData,
        EvidencePolicy,
        HttpTarget,
        KubernetesTarget,
        DockerComposeTarget,
        FeatureFlagTarget,
        BearerAuth,
        BasicAuth,
        OAuth2ClientCredentialsAuth,
        ServiceAccountAuth,
        MtlsAuth,
        NoneAuth,
        load_tfactory_yml,
        TFactoryYmlError,
        resolve_env_var,
        MissingSecretError,
    )
"""

from .exceptions import TFactoryYmlError
from .parser import _has_env_var_references, load_tfactory_yml, load_tfactory_yml_text
from .schema import (
    CONNECTOR_PLATFORMS,
    AuthSpec,
    BasicAuth,
    BearerAuth,
    ConnectorTarget,
    DockerComposeTarget,
    EvidencePolicy,
    FeatureFlagTarget,
    HealthCheck,
    HttpTarget,
    KubernetesTarget,
    MtlsAuth,
    NoneAuth,
    OAuth2ClientCredentialsAuth,
    ServiceAccountAuth,
    TargetSpec,
    TestData,
    TFactoryConfig,
    WaitFor,
    connector_browser_guidance,
    connector_platform_info,
)
from .secrets import MissingSecretError, resolve_auth_env_vars, resolve_env_var

# Legacy aliases used in the task spec
Target = TargetSpec
Auth = AuthSpec

__all__ = [
    # Config root
    "TFactoryConfig",
    # Target types
    "TargetSpec",
    "Target",  # alias
    "HttpTarget",
    "KubernetesTarget",
    "DockerComposeTarget",
    "FeatureFlagTarget",
    "ConnectorTarget",
    "CONNECTOR_PLATFORMS",
    "connector_platform_info",
    "connector_browser_guidance",
    # Auth types
    "AuthSpec",
    "Auth",  # alias
    "BearerAuth",
    "BasicAuth",
    "OAuth2ClientCredentialsAuth",
    "ServiceAccountAuth",
    "MtlsAuth",
    "NoneAuth",
    # Supporting models
    "HealthCheck",
    "WaitFor",
    "TestData",
    "EvidencePolicy",
    # Parser
    "load_tfactory_yml",
    "load_tfactory_yml_text",
    "_has_env_var_references",
    "TFactoryYmlError",
    # Secrets
    "resolve_env_var",
    "resolve_auth_env_vars",
    "MissingSecretError",
]
