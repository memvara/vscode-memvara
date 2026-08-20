# vscode-memvara

Give VS Code (Copilot agent mode) a memory it can prove — hosted MCP
and the skill that says how to use it.

Add this repository as a Copilot / VS Code plugin marketplace and
install `memvara`.

The first connection opens a browser so you can click Allow. That grant
lasts 90 days. There is no local Python process and we do not use an
API key.

The config key is `servers`, not `mcpServers`. A block copied from
Cursor will parse and do nothing.

URL: `https://app.memvara.dev/mcp`

You can also add that URL yourself under MCP: Add Server → HTTP.

Claude Code: [memvara/claude-memvara](https://github.com/memvara/claude-memvara).
A loop you wrote is `pip install memvara`.

## License

Apache-2.0.
