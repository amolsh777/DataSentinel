"""Import every model so Base.metadata knows all tables."""

from app.models.dataset import (
    Baseline,
    ColumnProfile,
    Dataset,
    DatasetProfile,
    DatasetVersion,
    SchemaSnapshot,
)
from app.models.lineage import LineageAsset, LineageEdge
from app.models.monitoring import (
    DriftResult,
    Incident,
    IncidentStatusHistory,
    MonitoringRun,
    QualityCheckResult,
    ReportExport,
    ValidationRule,
)

__all__ = [
    "Baseline",
    "ColumnProfile",
    "Dataset",
    "DatasetProfile",
    "DatasetVersion",
    "DriftResult",
    "Incident",
    "IncidentStatusHistory",
    "LineageAsset",
    "LineageEdge",
    "MonitoringRun",
    "QualityCheckResult",
    "ReportExport",
    "SchemaSnapshot",
    "ValidationRule",
]
