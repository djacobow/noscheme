"""Shared names for implemented checks and permitted net waivers."""

CHECKS = (
    "do_not_connect",
    "required_connection",
    "unused_pin",
    "unconnected_net",
    "missing_driver",
    "missing_pull",
    "opposing_pull",
    "multiple_drivers",
    "power_ground_conflict",
    "incomplete_roles",
    "unchecked_net",
    "aggregate_load_boundary",
    "aggregate_current_overload",
    "incomplete_aggregate_load",
    "current_load_group",
    "current_overload",
    "incomplete_budget",
    "waiver_scope",
    "waiver_target",
    "incompatible_role",
    "tristate_capability",
    "port_role",
    "incomplete_port_role",
    "required_port_connection",
    "unused_port",
)
WAIVABLE = frozenset(CHECKS) - {
    "aggregate_load_boundary",
    "aggregate_current_overload",
    "incomplete_aggregate_load",
    "current_load_group",
    "do_not_connect",
    "required_connection",
    "unused_pin",
    "waiver_scope",
    "waiver_target",
    "required_port_connection",
    "unused_port",
}

# Passive contacts impose no electrical domain. Unknown/unchecked and forbidden
# roles are handled separately; they are never inferred to be passive.
PASSIVE = {"passive"}
DIGITAL = {"digital_input", "digital_output", "digital_bidirectional", "drive.low", "drive.high"}
ANALOG = {"analog_input", "analog_output", "analog_bidirectional"}
INPUTS = {"digital_input", "analog_input", "power_input"}
ALLOWED_ROLES = {
    "tristate": PASSIVE | DIGITAL,
    "passive": PASSIVE | DIGITAL | ANALOG | {"power_input", "power_output", "ground"},
    "digital": PASSIVE | DIGITAL,
    "bidirectional": PASSIVE | DIGITAL,
    "idle_high": PASSIVE | {"digital_input", "drive.low"},
    "idle_low": PASSIVE | {"digital_input", "drive.high"},
    "analog": PASSIVE | ANALOG,
    "power": PASSIVE | INPUTS | {"power_output"},
    "ground": PASSIVE | {"digital_input", "analog_input", "ground"},
}

# A contract describes aggregate behavior viewed from outside the block. Local
# feedback receivers may share an output port. No behavior crosses components.
PORT_ALLOWED = {
    "passive": ALLOWED_ROLES["passive"],
    "digital_input": PASSIVE | {"digital_input"},
    "digital_output": PASSIVE | {"digital_input", "digital_output"},
    "digital_bidirectional": PASSIVE | DIGITAL,
    "analog_input": PASSIVE | {"analog_input"},
    "analog_output": PASSIVE | {"analog_input", "analog_output"},
    "analog_bidirectional": PASSIVE | ANALOG,
    "power_input": PASSIVE | {"power_input"},
    "power_output": PASSIVE | {"power_input", "power_output"},
    "ground": ALLOWED_ROLES["ground"],
    "drive.low": PASSIVE | {"digital_input", "drive.low"},
    "drive.high": PASSIVE | {"digital_input", "drive.high"},
    "unchecked": ALLOWED_ROLES["passive"],
}
PORT_EVIDENCE = {
    "digital_input": {"digital_input"},
    "digital_output": {"digital_output"},
    "digital_bidirectional": {"digital_bidirectional"},
    "analog_input": {"analog_input"},
    "analog_output": {"analog_output"},
    "analog_bidirectional": {"analog_bidirectional"},
    "power_input": {"power_input"},
    "power_output": {"power_output"},
    "ground": {"ground"},
    "drive.low": {"drive.low"},
    "drive.high": {"drive.high"},
}
