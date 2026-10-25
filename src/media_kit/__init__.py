"""Framework-independent managed image storage and thumbnail processing."""

from media_kit._version import ALGORITHM_VERSION, SCHEMA_VERSION, STORAGE_LAYOUT_VERSION, VERSION
from media_kit.api import MediaStore
from media_kit.config import StoreConfig, URLMapping
from media_kit.errors import (
    AssetConflictError,
    AssetNotFoundError,
    ConfigurationError,
    IdempotencyConflictError,
    InputValidationError,
    MediaKitError,
    ProcessingError,
    RevisionConflictError,
    StorageCompatibilityError,
    StorageError,
)
from media_kit.models import (
    AssetPage,
    AssetView,
    CheckReport,
    DeleteResult,
    ImageInfo,
    ProcessResult,
    Profile,
    TaskView,
    TransformConfig,
    TransformRecipe,
    TransformResult,
    VariantView,
)
from media_kit.transforms import convert_thumbnail, format_capabilities, inspect_image
from media_kit.transforms.fingerprint import fingerprint, recipe_for

__version__ = VERSION

__all__ = [
    "ALGORITHM_VERSION",
    "SCHEMA_VERSION",
    "STORAGE_LAYOUT_VERSION",
    "VERSION",
    "AssetConflictError",
    "AssetNotFoundError",
    "AssetPage",
    "AssetView",
    "CheckReport",
    "ConfigurationError",
    "DeleteResult",
    "IdempotencyConflictError",
    "ImageInfo",
    "InputValidationError",
    "MediaKitError",
    "MediaStore",
    "ProcessResult",
    "ProcessingError",
    "Profile",
    "RevisionConflictError",
    "StorageCompatibilityError",
    "StorageError",
    "StoreConfig",
    "TaskView",
    "TransformConfig",
    "TransformRecipe",
    "TransformResult",
    "URLMapping",
    "VariantView",
    "__version__",
    "convert_thumbnail",
    "fingerprint",
    "format_capabilities",
    "inspect_image",
    "recipe_for",
]
