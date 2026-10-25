"""Typed errors raised by the public media-kit API."""


class MediaKitError(Exception):
    """Base class for expected media-kit failures."""


class ConfigurationError(MediaKitError, ValueError):
    """Configuration is incomplete, invalid, or inconsistent with a store."""


class StorageCompatibilityError(MediaKitError):
    """The on-disk store format is not supported by this package version."""


class InputValidationError(MediaKitError, ValueError):
    """An input image cannot be accepted under the configured policy."""


class AssetConflictError(MediaKitError):
    """An asset key or idempotency key conflicts with an existing request."""


class RevisionConflictError(AssetConflictError):
    """The expected asset revision differs from the current revision."""


class IdempotencyConflictError(AssetConflictError):
    """An idempotency key was reused for a different request."""


class AssetNotFoundError(MediaKitError, LookupError):
    """The requested asset does not exist."""


class StorageError(MediaKitError, OSError):
    """A managed filesystem operation failed or crossed its storage boundary."""


class ProcessingError(MediaKitError):
    """A transformation or persisted processing operation failed."""
