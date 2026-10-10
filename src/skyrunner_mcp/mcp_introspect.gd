extends SceneTree
## Machine-readable facts for the Skyrunner MCP server (tools/mcp/skyrunner_mcp.py), read from the
## compiled classes rather than parsed from source: the command registry, role permissions and switches.
## godot --headless --script res://tools/mcp_introspect.gd
## Prints one line: INTROSPECT_JSON <json>.

func _initialize() -> void:
	var commands := {}
	var handlers: Dictionary = CommandDomains.handlers()
	for name in handlers:
		var handler: Callable = handlers[name]
		var owner = handler.get_object()
		var path: String = owner.resource_path if owner is Resource else ""
		var roles: Array = []
		for role in Roles.ALL:
			if Roles.allowed(role, name):
				roles.append(role)
		commands[name] = {"file": path.replace("res://", ""), "handler": handler.get_method(), "roles": roles,
			"preview": name in ActionDescriptions.SUPPORTED or name in ["accept_job", "drop_job"]}
	var switches := {}
	for key in Switches.PARITY + Switches.OPT_IN:
		switches[key] = "parity" if key in Switches.PARITY else "opt_in"
	print("INTROSPECT_JSON ", JSON.stringify({"commands": commands, "roles": Roles.ALL, "switches": switches,
		"protocol": Snapshot.PROTOCOL_VERSION, "version": ProjectSettings.get_setting("application/config/version", "")}))
	quit(0)
