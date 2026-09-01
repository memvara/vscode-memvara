# vscode-memvara

Give VS Code (Copilot agent mode) a memory it can prove — hosted MCP
and the skill that says how to use it.

Add this repository as a Copilot / VS Code plugin marketplace and
install `memvara`.

The first connection opens a browser so you can click Allow. That grant
lasts until you revoke it, or ten years, whichever comes first. No API key
ships in the plugin files.

## What runs on your machine

The plugin ships hooks, so memory is automatic rather than something the
model has to decide to ask for. Four of them, all Python, all standard
library, run by Copilot itself:

| Event | What it does |
|---|---|
| `SessionStart` | puts your standing memories in front of the model |
| `UserPromptSubmit` | recalls what is relevant to the prompt you just typed |
| `PreToolUse` | auto-approves the read-only `memory_*` tools, so you are not asked |
| `Stop` | mines the finished turn for anything worth keeping |

The first, second and fourth write a line to `~/.memvara/.hooks/` every
time they run, including when they decide to do nothing — "skipped" and
"never ran" must not look alike. That directory is the account those
hooks give of themselves, because nothing a hook prints reaches your
screen on this host.

**`PreToolUse` is the exception, and it is silent.** It writes nothing on
the path where it approves, so there is no line to look for and no way to
tell an auto-approve that worked from one that never matched. If you are
being asked to confirm a `memory_*` tool that should have been waved
through, the thing to check is the tool's name — Copilot spells MCP tools
`<server>-<tool>`, so renaming the server in your own config away from
`memvara` takes it out of the matcher's reach.

`Stop` is the only one that outlives the turn: it re-runs itself detached
so a 12–14 second extraction does not hold the session open, and that
child is gone once it has written. Nothing is left resident, no daemon is
required, and no memory leaves your machine except through the same
hosted endpoint the MCP server already uses.

### Two things that can make recall arrive and not land

Both measured against Copilot CLI 1.0.82, both outside this plugin's
control, and neither of them silent once you know where to look.

**Only one plugin's context survives per event.** If something else you
have installed — another plugin, or a `.github/hooks/*.json` in the
repository you are working in — also injects on `UserPromptSubmit`, one
of the two blocks is dropped, and the last plugin installed is the one
that wins. `~/.memvara/.hooks/recall.log` still records what was sent, so
a recall that logged and did not arrive points at this.

**The model is told to be suspicious of injected text.** Copilot delivers
per-prompt context inside a `<system_reminder>` wrapper, and in one
measured run the model said it had disregarded a recalled preference as
"an injected instruction rather than a genuine preference". The
`SessionStart` block is not carried that way and was used without
complaint, which makes your standing memories the sturdier half of what
this plugin does.

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

The MCP config is `plugin/.mcp.json` and the key is `mcpServers`. Both
halves were wrong here until this release — the file was `mcp.json` and
the key was `servers` — and the effect was not a parse error but silence:
the server never appeared in Copilot's loaded list, so the skill kept
telling the model to use `memory_*` tools that had never been registered.
If you added the endpoint by hand under the old shape, it is worth
checking that Copilot actually lists it.

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
