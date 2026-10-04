"""Shared names for implemented checks and permitted net waivers."""

CHECKS = (
    "do_not_connect",
    "required_connection",
    "unused_pin",
    "unconnected_net",
    "missing_driver",
    "multiple_drivers",
    "power_ground_conflict",
    "incomplete_roles",
    "unchecked_net",
    "current_overload",
    "incomplete_budget",
    "waiver_scope",
)
WAIVABLE = frozenset(CHECKS) - {
    "do_not_connect",
    "required_connection",
    "unused_pin",
    "waiver_scope",
}
