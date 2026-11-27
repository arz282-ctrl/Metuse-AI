"""Meta Account Connections: Unified Login, Encrypted Secret Store, Capability Matrix & Webhooks."""

from packages.meta_integrations.capabilities import (
    CAPABILITY_REQUIRED_SCOPES,
    PERMISSION_MATRIX,
    PERSONAL_IG_CONVERSION_STEPS,
    compute_capabilities,
    default_scopes_for_connection,
    require_meta_capability,
)
from packages.meta_integrations.client import (
    LiveMetaGraphClient,
    MetaGraphClient,
    MockMetaGraphClient,
    create_meta_graph_client,
)
from packages.meta_integrations.contracts import (
    DiscoveredMetaAsset,
    MetaCapabilitiesView,
    MetaConnectedAssetView,
    MetaConnectionView,
    MetaTokenExchangeResult,
    MetaWebhookReceipt,
    PermissionMatrixEntry,
    UnifiedLoginStartResponse,
)
from packages.meta_integrations.secret_store import EncryptedSecretStore, SecretStore
from packages.meta_integrations.service import MetaConnectionService

__all__ = [
    "CAPABILITY_REQUIRED_SCOPES",
    "PERMISSION_MATRIX",
    "PERSONAL_IG_CONVERSION_STEPS",
    "DiscoveredMetaAsset",
    "EncryptedSecretStore",
    "LiveMetaGraphClient",
    "MetaCapabilitiesView",
    "MetaConnectedAssetView",
    "MetaConnectionService",
    "MetaConnectionView",
    "MetaGraphClient",
    "MetaTokenExchangeResult",
    "MetaWebhookReceipt",
    "MockMetaGraphClient",
    "PermissionMatrixEntry",
    "SecretStore",
    "UnifiedLoginStartResponse",
    "compute_capabilities",
    "create_meta_graph_client",
    "default_scopes_for_connection",
    "require_meta_capability",
]
