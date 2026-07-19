"""The single production registry for Release 1 evidence producer IDs."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

RELEASE_CHECKPOINT_PRODUCER_ID: Final = "dan-release-checkpoint:v1"
TEST_BASELINE_PRODUCER_ID: Final = "dan-test-baseline:v2"
BATCH1_DATA_CUTOVER_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch1_data_cutover:v1"
)
BATCH2_RUNTIME_HOST_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch2_runtime_host:v1"
)
BATCH3_PERSONA_CONFIG_VOICE_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch3_persona_config_voice:v1"
)
BATCH4_PANEL_TEST_RELEASE_REPORT_PRODUCER_ID: Final = (
    "dan-release-report:batch4_panel_test_release:v1"
)
RELEASE_BUILD_GATE_PRODUCER_ID: Final = "dan-release-build-gate:v1"
RELEASE_AUDIT_PRODUCER_ID: Final = "dan-release-audit:v2"
DEPLOYMENT_RECEIPT_PRODUCER_ID: Final = "dan-deployment-receipt:v1"
ROLLBACK_REHEARSAL_PRODUCER_ID: Final = "dan-cutover-rehearsal:v1"
VOICE_ACCEPTANCE_PRODUCER_ID: Final = "dan-voice-acceptance:v2"
REVIEW_EVIDENCE_PRODUCER_ID: Final = "dan-review-evidence:v1"

BATCH_REPORT_PRODUCER_IDS: Final = MappingProxyType(
    {
        "batch1_data_cutover": BATCH1_DATA_CUTOVER_REPORT_PRODUCER_ID,
        "batch2_runtime_host": BATCH2_RUNTIME_HOST_REPORT_PRODUCER_ID,
        "batch3_persona_config_voice": BATCH3_PERSONA_CONFIG_VOICE_REPORT_PRODUCER_ID,
        "batch4_panel_test_release": BATCH4_PANEL_TEST_RELEASE_REPORT_PRODUCER_ID,
    }
)
CORE_EVIDENCE_PRODUCERS: Final = MappingProxyType(
    {
        "release_checkpoint": RELEASE_CHECKPOINT_PRODUCER_ID,
        "baseline_v2": TEST_BASELINE_PRODUCER_ID,
    }
)
RELEASE_PRODUCER_IDS: Final = MappingProxyType(
    {
        **CORE_EVIDENCE_PRODUCERS,
        **BATCH_REPORT_PRODUCER_IDS,
        "offline_clean_clone_build": RELEASE_BUILD_GATE_PRODUCER_ID,
        "active_home_release_audit": RELEASE_AUDIT_PRODUCER_ID,
        "deployment_receipt": DEPLOYMENT_RECEIPT_PRODUCER_ID,
        "rollback_rehearsal": ROLLBACK_REHEARSAL_PRODUCER_ID,
        "voice_acceptance_m5": VOICE_ACCEPTANCE_PRODUCER_ID,
        "agent_review_summary": REVIEW_EVIDENCE_PRODUCER_ID,
    }
)
