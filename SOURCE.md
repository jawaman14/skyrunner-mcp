# Extraction provenance

This repository extracts the MCP tooling from [jawaman14/skyrunner](https://github.com/jawaman14/skyrunner)
at [537a2aba279d9b8b64fbf01661ceb9170b1f8899](https://github.com/jawaman14/skyrunner/commit/537a2aba279d9b8b64fbf01661ceb9170b1f8899).
The original source history remains intact in that repository. The MIT copyright notice is preserved.

Original paths: tools/mcp/skyrunner_mcp.py, collaboration.py, run_tests.py, test_runtime.py,
test_collaboration.py, test_guards.py and tools/mcp_introspect.gd. These become the installed
src/skyrunner_mcp package and independent tests. No Skyrunner Git history was rewritten.

Extraction changes: package-relative imports, explicit target root, a packaged GDScript adapter/guide,
console entry point, isolated fixture projects and independent Windows/Linux CI. The implementation
and all public tool names remain compatible with existing clients.
