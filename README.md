# vscode-memvara

Give VS Code (Copilot agent mode) a memory it can prove — hosted MCP
and the skill that says how to use it.

Add this repository as a Copilot / VS Code plugin marketplace and
install `memvara`.

The first connection opens a browser so you can click Allow. That grant
lasts 90 days. Nothing runs in the background and no API key ships in the
plugin files.

## When the browser sign-in will not finish

The skill ships `skills/memvara/scripts/memvara_auth.py`: the device-code
flow, standard library only, no `pip install`, and nothing left running
when it returns. Ask Copilot to authenticate memvara and it runs the
script, which prints a short code and a URL for you to approve and then
writes `~/.memvara/credentials.json`. It also does `logout` and `stats`.

A Copilot plugin cannot ship slash commands — the documented components
are agents, skills, hooks, MCP servers and LSP servers — so there is no
`/memvara authenticate` here. Asking in words is the interface on this
host.

The config key is `servers`, not `mcpServers`. A block copied from
Cursor will parse and do nothing.

URL: `https://app.memvara.dev/mcp`

You can also add that URL yourself under MCP: Add Server → HTTP.

Claude Code: [memvara/claude-memvara](https://github.com/memvara/claude-memvara).
A loop you wrote is `pip install memvara`.

## License

Apache-2.0.

## Teach it your vocabulary

The built-in predicates are a personal-assistant vocabulary. A store of engineering facts
matches none of them, and an unknown predicate takes the safe default twice over:
multi-valued, so nothing supersedes it, and slow-decaying, so this morning's deploy still
ranks as fresh in two years. The first half shows up on the write receipt. The second is
silent.

Server-side configuration, so it is set where the server is launched:

```bash
MEMVARA_PREDICATES=engineering        # or: engineering,./ours.toml
```

A declaration outranks a guess, so a pack corrects a store that already classified
something wrongly rather than only shaping a fresh one.

## Coming from another memory product

```python
from memvara.compat import import_mem0, import_supermemory
```

mem0 records what changed and when, so that import rebuilds supersession. Supermemory
records current state, so its documents arrive as episodes on their original timestamps
and nothing invents a history it was never told — which means plain recall answers from
claims and looks empty until you ask for `include_episodes`. The skill says this at the
point of use.
