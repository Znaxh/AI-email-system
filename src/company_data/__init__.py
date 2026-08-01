"""Company-data package — parsers, dry-run, and versioned bundle activation."""

from src.company_data.schema import (
    MIN_USEFUL_TONE_CORPUS,
    DryRunResult,
    FieldMapping,
    PreviewResult,
)
from src.company_data.service import (
    CompanyBundle,
    activate_staged,
    dry_run_staged,
    load_active_company_bundle,
    maybe_import_legacy_data,
    preview,
    preview_staged,
    rollback,
    stage_upload,
    status,
)
from src.company_data.validate import dry_run_upload, normalize

__all__ = [
    "MIN_USEFUL_TONE_CORPUS",
    "CompanyBundle",
    "DryRunResult",
    "FieldMapping",
    "PreviewResult",
    "activate_staged",
    "dry_run_staged",
    "dry_run_upload",
    "load_active_company_bundle",
    "maybe_import_legacy_data",
    "normalize",
    "preview",
    "preview_staged",
    "rollback",
    "stage_upload",
    "status",
]
