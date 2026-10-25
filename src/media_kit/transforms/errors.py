"""Errors raised by the storage-independent image transformation API."""


class TransformError(Exception):
    """Base class for transform failures."""


class TransformConfigurationError(TransformError, ValueError):
    """A profile or resource limit is invalid."""


class ImageValidationError(TransformError, ValueError):
    """An input image is unsupported, inconsistent, corrupt, or over limit."""


class TransformIOError(TransformError, OSError):
    """Image output could not be safely published."""
