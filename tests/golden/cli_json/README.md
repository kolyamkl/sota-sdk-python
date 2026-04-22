# CLI `--json` golden files — parity contract between Python and TypeScript SDKs

Each `.fixture.json` is a canonical mock server response. Each corresponding
`.expected.json` (or `.ndjson`) is the expected CLI output when that fixture
drives `--json` mode.

**Contract:** Python (Plan 2) and TypeScript (Plan 3) CLIs must produce
byte-identical `--json` output for every command given the same fixture.

When adding a new `--json` command:
1. Add `<command>.fixture.json` with a canonical mock response.
2. Add `<command>.expected.json` with the expected CLI output.
3. Add a test in `test_cli_json_parity.py` (Python) and the equivalent
   TypeScript test (Plan 3).
