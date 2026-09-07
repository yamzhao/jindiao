"""DeepSearch adapters."""

from .fallback import MockFallbackOutcome, MockFallbackService
from .interface import DeepSearchHit, DeepSearchProvider, DeepSearchQuery
from .policy import SupplementPolicy
from .provider import (
    DeepSearchCustomLocalAdapter,
    DeepSearchLifecycleError,
    ScenarioDeepSearchProvider,
    build_mock_ref,
)
from .supplement import (
    BoundedEvidenceSupplementService,
    BoundedSupplementQueryPlanner,
    EvidenceGap,
    SupplementalResearchProvider,
    SupplementDocument,
    SupplementOutcome,
    SupplementQuery,
    SupplementSearchCandidate,
)
from .tianyancha_annual_report import (
    AnnualReportSocialSecurity,
    AnnualReportSocialSecurityOutcome,
    AnnualReportSocialSecurityProvider,
    TianyanchaAnnualReportProvider,
)

__all__ = [
    "AnnualReportSocialSecurity",
    "AnnualReportSocialSecurityOutcome",
    "AnnualReportSocialSecurityProvider",
    "BoundedEvidenceSupplementService",
    "BoundedSupplementQueryPlanner",
    "DeepSearchCustomLocalAdapter",
    "DeepSearchHit",
    "DeepSearchLifecycleError",
    "DeepSearchProvider",
    "DeepSearchQuery",
    "EvidenceGap",
    "MockFallbackOutcome",
    "MockFallbackService",
    "ScenarioDeepSearchProvider",
    "SupplementDocument",
    "SupplementOutcome",
    "SupplementPolicy",
    "SupplementQuery",
    "SupplementSearchCandidate",
    "SupplementalResearchProvider",
    "TianyanchaAnnualReportProvider",
    "build_mock_ref",
]
