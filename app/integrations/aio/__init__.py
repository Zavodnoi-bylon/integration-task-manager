"""Read-only AIO integration used by automation workers."""

from .client import AioClient, AioClientConfig, AioPage
from .advertisers import (
    AioAdvertiser,
    AioAdvertiserClient,
    AioAdvertiserPage,
    AioTenant,
    discover_available_tenants,
)
from .models import AioConversion, AioPayloadError

__all__ = (
    "AioClient",
    "AioClientConfig",
    "AioConversion",
    "AioAdvertiser",
    "AioAdvertiserClient",
    "AioAdvertiserPage",
    "AioTenant",
    "AioPage",
    "AioPayloadError",
    "discover_available_tenants",
)
