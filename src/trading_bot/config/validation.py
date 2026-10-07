from trading_bot.domain.records import GateReport

from .profiles import (
    PUBLIC_INSTRUMENTS_URLS,
    ExecutionProfile,
    ResearchProfile,
    RiskProfile,
)


def validate_profiles(
    execution: ExecutionProfile, risk: RiskProfile, research: ResearchProfile
) -> GateReport:
    """G1 validates offline foundation; PASS never grants trade/account permissions."""
    fail = []
    blocked = []
    if any(profile.schema_version != 1 for profile in (execution, risk, research)):
        fail.append("UNSUPPORTED_PROFILE_VERSION")
    if risk != RiskProfile():
        fail.append("APPROVED_RISK_LIMITS_CHANGED")
    allowed_search = {"compression_window", "activity_multiplier", "retest_bars"}
    for name, _ in research.search_parameters:
        if name not in allowed_search:
            fail.append(f"SEARCH_PARAMETER_FORBIDDEN:{name}")
    if (
        research.baseline != "R0"
        or research.timeframe_seconds != 300
        or research.time_exit_boundaries != 24
        or research.variants != ("A", "B")
    ):
        fail.append("APPROVED_BASELINE_CHANGED")
    if execution.environment not in {"research", "paper", "uat", "live"}:
        blocked.append("UNKNOWN_ENVIRONMENT")
    if execution.environment == "live":
        blocked.append("P01_LIVE_UNAVAILABLE")
        if not execution.quote_profile_complete:
            blocked.append("QUOTE_PROFILE_INCOMPLETE")
        if execution.runtime_status != "COMPLETE":
            blocked.append("RUNTIME_INCOMPLETE")
        for index in range(1, 12):
            capability = f"V{index:02}"
            if not any(
                item.capability_id == capability and item.status == "PASS"
                for item in execution.evidence
            ):
                blocked.append(f"CAPABILITY_UNKNOWN:{capability}")
    if execution.credential_scope != "none":
        blocked.append("CREDENTIALS_FORBIDDEN_IN_P01")
    if execution.trade_transport != "none":
        blocked.append("TRADE_TRANSPORT_DISABLED")
    if any(url not in PUBLIC_INSTRUMENTS_URLS for url in execution.public_endpoints):
        blocked.append("PUBLIC_ENDPOINT_NOT_ALLOWED")
    if execution.environment == "uat" and any(
        url != PUBLIC_INSTRUMENTS_URLS[1] for url in execution.public_endpoints
    ):
        blocked.append("UAT_ENDPOINT_ENVIRONMENT_MISMATCH")
    if (
        execution.allowed_types != ("SPOT",)
        or execution.order_type != "LIMIT"
        or execution.time_in_force != "FILL_OR_KILL"
        or execution.retry_policy != "NEVER"
        or execution.exit_policy != "DISABLED"
    ):
        blocked.append("EXECUTION_POLICY_UNAVAILABLE_IN_P01")
    if execution.quote_currency != "synthetic:SYNTH_Q":
        blocked.append("REAL_QUOTE_UNVERIFIED")
    if execution.deadlines != ExecutionProfile().deadlines:
        fail.append("OPERATIONAL_DEADLINES_REQUIRE_MEASUREMENT_REVIEW")
    reasons = tuple(dict.fromkeys(fail + blocked))
    return GateReport(
        "G1_OFFLINE_FOUNDATION", "FAIL" if fail else "BLOCKED" if blocked else "PASS", reasons
    )
