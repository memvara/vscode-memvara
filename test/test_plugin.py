
"""Gates for the VS Code / Copilot plugin.

Every file the client will read is asserted here.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import ssl
import subprocess
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
SKILL = PLUGIN / "skills" / "memvara"
HOSTED = "https://app.memvara.dev/mcp"
REPO_NAME = "memvara/vscode-memvara"


def _json(path: pathlib.Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class LibraryUnreachable(Exception):
    """Neither a local checkout nor GitHub could answer. Raised, never swallowed.

    A drift check that quietly passes when it cannot look is the same as no drift check.
    This repository has already been caught by exactly that shape: `skill-sync.yml` failed
    on every scheduled run for days while nothing here went red, because the vendored copy
    and `skill.lock` stayed consistent with each other and the only thing that would have
    noticed was a scheduled job nobody read.
    """


def _trust() -> "ssl.SSLContext":
    """A context that trusts the same roots `curl` does.

    python.org's macOS build ignores the system trust store, so an unqualified `urlopen`
    raises CERTIFICATE_VERIFY_FAILED against a certificate `curl` accepts. Without this the
    drift check below does not fail on a Mac -- it *skips*, reporting the library as
    unreachable when the library is fine, which is the quiet half of the failure it was
    written to catch.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "memvara-tests"})
    with urllib.request.urlopen(request, timeout=30, context=_trust()) as resp:
        return bytes(resp.read())


def _library_bytes(sha: str, path: str) -> bytes:
    root = os.environ.get("MEMVARA_LIBRARY")
    if root:
        try:
            return subprocess.check_output(
                ["git", "-C", root, "show", f"{sha}:{path}"],
                stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            # The checkout has the sha `skill.lock` names and nothing else: CI clones the
            # library AT that sha, shallow, so the library's current HEAD is simply not an
            # object here. Falling back to the network rather than failing is what lets the
            # drift check below run on CI at all -- and it only matters when the lock is
            # stale, which is precisely when the check has something to say.
            pass
    return _fetch(f"https://raw.githubusercontent.com/memvara/memvara/{sha}/{path}")


def _library_head() -> str:
    """The library default branch's current sha, or raise `LibraryUnreachable`."""
    root = os.environ.get("MEMVARA_LIBRARY")
    if root:
        for ref in ("origin/main", "main"):
            try:
                return subprocess.check_output(
                    ["git", "-C", root, "rev-parse", ref],
                    stderr=subprocess.DEVNULL).decode().strip()
            except subprocess.CalledProcessError:
                continue
    try:
        body = _fetch("https://api.github.com/repos/memvara/memvara/commits/main")
        return str(json.loads(body)["sha"])
    except Exception as exc:
        raise LibraryUnreachable(str(exc)) from exc


def _library_skill_files(sha: str) -> "set[str]":
    """Every path under the packaged skill at `sha`, relative to it."""
    root = os.environ.get("MEMVARA_LIBRARY")
    prefix = "memvara/skills/memvara/"
    if root:
        try:
            out = subprocess.check_output(
                ["git", "-C", root, "ls-tree", "-r", "--name-only", sha,
                 "memvara/skills/memvara"], stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError:
            # Not an object in this checkout -- see `_library_bytes`. Ask GitHub instead
            # of reporting the library unreachable, which would SKIP the check on the one
            # run that needed it.
            out = None
        if out is not None:
            return {line[len(prefix):] for line in out.splitlines()
                    if line.startswith(prefix)}
    try:
        tree = json.loads(_fetch(
            f"https://api.github.com/repos/memvara/memvara/git/trees/{sha}?recursive=1"))
    except Exception as exc:
        raise LibraryUnreachable(str(exc)) from exc
    return {entry["path"][len(prefix):] for entry in tree.get("tree", [])
            if entry.get("type") == "blob" and entry["path"].startswith(prefix)}


def _library_files(sha: str, path: str) -> "set[str]":
    """Every path under `path` at `sha`, relative to `path`. The hook twin of
    `_library_skill_files`, which hardcodes the packaged-skill prefix."""
    root = os.environ.get("MEMVARA_LIBRARY")
    prefix = f"{path}/"
    if root:
        try:
            out = subprocess.check_output(
                ["git", "-C", root, "ls-tree", "-r", "--name-only", sha, path],
                stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError:
            out = None
        if out is not None:
            return {line[len(prefix):] for line in out.splitlines()
                    if line.startswith(prefix)}
    try:
        tree = json.loads(_fetch(
            f"https://api.github.com/repos/memvara/memvara/git/trees/{sha}?recursive=1"))
    except Exception as exc:
        raise LibraryUnreachable(str(exc)) from exc
    return {entry["path"][len(prefix):] for entry in tree.get("tree", [])
            if entry.get("type") == "blob" and entry["path"].startswith(prefix)}


def _lock(name: str = "skill.lock") -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


#: The vendored hook tree, the library path it comes from, and the one file in it that is
#: NOT vendored: `hooks.json` is generated from `hosts/copilot.py` and has its own guard.
HOOKS = PLUGIN / "hooks"
LIBRARY_HOOKS_PATH = "plugin/hooks"
GENERATED_REGISTRATION = "hooks.json"
EVIDENCE = ROOT / "test" / "evidence" / "copilot"

#: Hook scripts are executable content Copilot runs on every prompt, so the allowlist
#: names them one by one. A file under `hooks/` that nobody listed is the thing to catch.
ALLOWED_HOOK_FILES = {
    "hooks.json",
    "run.py", "recall.py", "capture.py", "session_start.py", "approve.py", "daemon.py",
    "core/__init__.py", "core/host.py", "core/envelope.py",
    # `hosts/claude.py`, `hosts/codex.py`, `hosts/cursor.py` and `hosts/opencode.py` are
    # other clients' records: inert here, since `run.py --host copilot` imports only the
    # record it is given. Present because the tree is copied whole with zero transforms,
    # and named rather than wildcarded so a file nobody read cannot ship from this plugin.
    "hosts/__init__.py", "hosts/claude.py", "hosts/codex.py", "hosts/copilot.py",
    "hosts/cursor.py", "hosts/opencode.py",
    "js/shim.mjs", "js/opencode.mjs",
    "lib/__init__.py", "lib/extract.py", "lib/fast.py", "lib/hosted.py", "lib/ipc.py",
    "lib/open.py", "lib/standing.py", "lib/transcript.py", "lib/usage.py", "lib/write.py",
    "tools/__init__.py", "tools/generate.py",
}


class Hooks(unittest.TestCase):
    """The tree Copilot runs on every prompt, vendored byte for byte with ZERO transforms.

    Stricter than `skill.lock`, which sanctions exactly one line. Two comparisons because
    they catch different failures: against the sha the lock names, and against the
    library's current default branch. The first alone is satisfied forever by a lock and a
    copy frozen together, which is how the vendored skill in this family once shipped five
    commits behind for four days with every test green.
    """

    def _ours(self) -> "set[str]":
        return {path.relative_to(HOOKS).as_posix() for path in HOOKS.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts}

    def _vendored(self) -> "set[str]":
        # `hooks.json` is generated from the host record, not vendored: seven repositories
        # share this tree and each registers a different client, so a canonical copy would
        # be one repository's manifest shipped to all of them.
        return self._ours() - {GENERATED_REGISTRATION}

    def _record(self):
        sys.path.insert(0, str(HOOKS))
        try:
            import hosts.copilot as record  # noqa: PLC0415

            return record.HOST
        finally:
            sys.path.remove(str(HOOKS))

    def test_the_vendored_hook_bytes_match_the_library_at_the_pinned_sha(self) -> None:
        lock = _lock("hooks.lock")
        self.assertEqual(lock["repo"], "memvara/memvara")
        self.assertEqual(lock["path"], LIBRARY_HOOKS_PATH)
        self.assertEqual(lock["host"], "copilot")
        sha = lock["sha"]
        self.assertEqual(len(sha), 40, f"hooks.lock sha is not a full sha: {sha!r}")
        ours = self._vendored()
        self.assertTrue(ours, "no vendored hook files found - this guard would pass on "
                              "an empty tree, which is the shape it exists to stop")
        try:
            upstream = _library_files(sha, LIBRARY_HOOKS_PATH)
        except LibraryUnreachable as exc:
            raise unittest.SkipTest(
                f"library unreachable, vendored bytes NOT checked: {exc}") from exc
        self.assertEqual(ours, upstream,
                         f"the vendored hook file set differs from the library@{sha[:7]}")
        drifted = [rel for rel in sorted(ours)
                   if (HOOKS / rel).read_bytes()
                   != _library_bytes(sha, f"{LIBRARY_HOOKS_PATH}/{rel}")]
        self.assertEqual(drifted, [], f"vendored hooks drifted from {sha[:7]}: {drifted}")

    def test_the_vendored_hooks_are_not_behind_the_library(self) -> None:
        """Skips loudly rather than passing when the library cannot be reached: a check
        that passes because it could not look is the failure one level up."""
        try:
            head = _library_head()
            upstream = _library_files(head, LIBRARY_HOOKS_PATH)
        except LibraryUnreachable as exc:
            raise unittest.SkipTest(
                f"library unreachable, hook drift NOT checked: {exc}") from exc
        self.assertTrue(upstream, "the library reported an empty hook tree")
        self.assertEqual(self._vendored(), upstream,
                         f"the vendored hook file set differs from the library at "
                         f"{head[:7]} - re-vendor and update hooks.lock")

    def test_the_hook_file_set_is_named_here_one_by_one(self) -> None:
        extra = self._ours() - ALLOWED_HOOK_FILES
        self.assertFalse(extra, f"unlisted hook files: {sorted(extra)} - add them to "
                                "ALLOWED_HOOK_FILES deliberately, having read them")

    def test_the_allowlist_names_nothing_that_is_no_longer_in_the_tree(self) -> None:
        """A file deleted upstream leaves its entry behind, the entry covers nothing, and
        a list that has stopped covering a file looks exactly like one that covers
        everything."""
        missing = ALLOWED_HOOK_FILES - self._ours()
        self.assertFalse(missing, f"allowlist names files that are gone: {sorted(missing)}")

    def test_the_registration_sits_where_this_client_actually_looks(self) -> None:
        """The one guard without which this whole port silently does nothing.

        Copilot recognises a plugin manifest at `.plugin/plugin.json`, `plugin.json`,
        `.github/plugin/plugin.json` or `.claude-plugin/plugin.json` - the installer
        prints exactly that list when it cannot find one. This plugin's manifest is at
        `plugin/.github/plugin.json`, which is on no such list, so it is NOT READ: measured
        on 1.0.82, a `hooks` key there produced no receipt and a `skills` key naming a
        non-default directory left the skill unlisted.

        So the registration is found by CONVENTION, which means `hooks.json` at the plugin
        root. Move it, or add a `hooks` key to a manifest the client would read, and the
        hooks stop firing while every other test here stays green.
        """
        self.assertTrue((HOOKS / "hooks.json").is_file(),
                        "hooks.json is not at plugin/hooks/hooks.json")
        manifest = _json(PLUGIN / ".github" / "plugin.json")
        self.assertNotIn(
            "hooks", manifest,
            "the manifest declares a hooks key, and at this path the client never reads "
            "it - and if the manifest is ever moved to a path it DOES read, that key "
            "replaces the convention rather than adding to it, so the two must be "
            "decided together")

    def test_every_hook_this_plugin_declares_points_at_a_file_that_exists(self) -> None:
        """Replaces the old `test_no_hooks`, which asserted `plugin/hooks` did not exist.

        That was a guard a deletion satisfies. This one is positive: the command set must
        be NON-EMPTY, every entry must be a command, and every path it names must resolve
        once the plugin-root variable is expanded - so a gutted `{"hooks": {}}` fails
        exactly as loudly as a broken path.
        """
        body = _json(HOOKS / "hooks.json")["hooks"]
        self.assertTrue(body, "hooks.json registers no events at all")
        seen = 0
        for event, entries in body.items():
            for entry in entries:
                for command in entry["hooks"]:
                    self.assertEqual(command["type"], "command", event)
                    text = command["command"]
                    self.assertIn("run.py", text, event)
                    # `${PLUGIN_ROOT:-${COPILOT_PLUGIN_ROOT}}` -> the tree on disk here.
                    # Everything after the LAST closing brace: the expansion is nested,
                    # so a non-greedy `\$\{[^}]*\}` stops at the inner `}` and leaves a
                    # stray one on the front of the path.
                    quoted = text.split('"')[1]
                    rel = quoted[quoted.rindex("}") + 1:].lstrip("/")
                    self.assertTrue((PLUGIN / rel).is_file(),
                                    f"{event} names {rel}, which is not a file here")
                    seen += 1
        self.assertGreaterEqual(seen, 4, "fewer commands registered than hooks shipped")

    def test_the_events_registered_are_the_ones_this_host_was_seen_to_fire(self) -> None:
        """Set membership against a receipt, not against documentation.

        Documentation says what an event is CALLED. `verified.json` records what actually
        fired, captured from the client by a probe installed the way a user installs one.
        Only both together close the gap that makes a wrong event name completely silent.
        """
        fired = set(_json(EVIDENCE / "verified.json")["events_fired"])
        registered = set(_json(HOOKS / "hooks.json")["hooks"])
        self.assertTrue(registered, "hooks.json registers no events")
        self.assertLessEqual(
            registered, fired,
            f"registered but never seen to fire: {sorted(registered - fired)} - an event "
            "name this client does not fire is a hook that installs and never runs")

    def test_the_event_payload_this_host_sends_is_the_one_the_hook_reads(self) -> None:
        """Compares the hook against payloads captured FROM the client, not against our
        assumption about it.

        A renamed stdin key is silent here: the dedup file is keyed on session, so a miss
        re-injects every memory on every turn while looking perfectly healthy. The casing
        is the live hazard - Copilot fires `userPromptSubmitted` as readily as
        `UserPromptSubmit` and sends `sessionId` instead of `session_id` when it does, so
        a registration that drifted to camelCase would read nothing at all.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from core import envelope  # noqa: PLC0415
            import hosts.copilot as record  # noqa: PLC0415
        finally:
            sys.path.remove(str(HOOKS))
        host = record.HOST
        for event, hook, wanted in (
                ("SessionStart", "session_start", ("session", "cwd")),
                ("UserPromptSubmit", "recall", ("session", "cwd", "prompt")),
                ("PreToolUse", "approve", ("session", "tool_name")),
                ("Stop", "capture", ("session", "cwd", "transcript_path"))):
            with self.subTest(event=event):
                raw = (EVIDENCE / f"{event}.stdin.json").read_bytes()
                ev = envelope.read_event(host, hook, raw)
                for field in wanted:
                    self.assertTrue(getattr(ev, field),
                                    f"{event} carries {field} and the record does not "
                                    f"read it")

    def test_the_reply_is_flat_because_the_nested_shape_delivers_nothing_here(self) -> None:
        """The measurement that decides whether recall reaches the model at all.

        Isolated on 1.0.82 by emitting each shape alone: `hookSpecificOutput.
        additionalContext` returned `NO CANARY`, a top-level `additionalContext` was read
        back verbatim. Shipping Claude Code's envelope unchanged produces a plugin that
        installs, runs, logs success and recalls nothing.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from core import envelope  # noqa: PLC0415
            from core.host import Reply  # noqa: PLC0415
            import hosts.copilot as record  # noqa: PLC0415
        finally:
            sys.path.remove(str(HOOKS))
        rendered = json.loads(envelope.render(
            record.HOST, Reply("recall", context="a memory")))
        self.assertEqual(rendered, {"additionalContext": "a memory"},
                         "the reply is not the flat shape this client reads")
        self.assertNotIn("hookSpecificOutput", rendered)

    def test_the_approve_matcher_matches_the_way_this_client_anchors_it(self) -> None:
        """Copilot compiles a matcher as `^(?:PATTERN)$` and names MCP tools
        `<server>-<tool>` - measured against a local stdio server, where `memory_recall`
        from a server configured as `memvara` reached the hook as `memvara-memory_recall`.

        Both halves matter and neither is Claude Code's. An unanchored `memvara`, which is
        exactly what the sibling Cursor record uses, matches NOTHING here; and a separator
        of `__` leaves the leaf as the whole string, so `_tool_leaf` never finds a
        read-only tool name and nothing is ever auto-approved.
        """
        host = self._record()
        name = "memvara-memory_recall"
        self.assertTrue(re.fullmatch(host.approve.matcher, name),
                        f"{host.approve.matcher!r} does not match {name!r} when anchored "
                        "the way this client anchors it")
        leaf = name
        for sep in host.approve.separators:
            if sep in leaf:
                leaf = leaf.rsplit(sep, 1)[-1]
                break
        self.assertEqual(leaf, "memory_recall",
                         "the separators do not reduce this client's tool name to the "
                         "bare tool, so approve.py can never recognise a read-only one")

    def test_capture_is_not_registered_async_on_this_client(self) -> None:
        """`async: true` is accepted here and is NOT honoured: measured, a hook declared
        async that slept six seconds delayed the client's exit by six seconds. Capture is
        declared synchronous and `run.py` forks it with its pipes closed, which is what
        actually releases the turn. An `async` key reappearing here would be decoration
        over a turn that still hangs for the whole extraction.
        """
        stop = _json(HOOKS / "hooks.json")["hooks"]["Stop"][0]["hooks"][0]
        self.assertNotIn("async", stop,
                         "Stop is registered async, and this client does not honour the "
                         "flag - the turn would still wait for the whole extraction")

    def test_no_context_limit_is_declared_because_this_client_imposes_none(self) -> None:
        """The opposite of the Codex guard, and measured rather than inherited.

        Codex truncates `additionalContext` middle-out above a default and needs its limit
        raised. Copilot passed a 16,384-byte block whole - head, middle and tail nonces all
        arrived - so there is nothing to raise. `additionalContextLimit` is Codex's key;
        emitting it here would address a setting this client has no use for, and a key
        nobody reads is a key that goes stale unnoticed.
        """
        for entries in _json(HOOKS / "hooks.json")["hooks"].values():
            for entry in entries:
                for command in entry["hooks"]:
                    self.assertNotIn("additionalContextLimit", command)

    def test_this_repository_ships_the_record_its_lock_binds(self) -> None:
        self.assertEqual(_lock("hooks.lock")["host"], "copilot")
        self.assertTrue((HOOKS / "hosts" / "copilot.py").is_file())

    def test_the_registration_is_what_the_record_generates(self) -> None:
        """`hooks.json` is the one file here that is built rather than copied.

        Built IN PROCESS and compared to the committed bytes. A sibling repository's first
        version of this guard ran `generate.py` as a subprocess, which REWRITES hooks.json,
        and then diffed the file against git - so the regeneration erased the edit before
        the comparison and a hand-edited manifest passed. It could not fail, which a
        sabotage found and reading could not.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from tools.generate import registration  # noqa: PLC0415
            import hosts.copilot as record  # noqa: PLC0415
        finally:
            sys.path.remove(str(HOOKS))
        self.assertEqual(
            (HOOKS / "hooks.json").read_bytes(), registration(record.HOST),
            "the committed hooks.json is not what hosts/copilot.py generates - it was "
            "hand-edited, or the record changed without regenerating")

    def test_capture_mines_with_this_host_s_own_model(self) -> None:
        """The README promises "your own model", so the first rung must be this host's CLI.

        The absence of `--model` is the subtler half: naming one there overrides the model
        this user configured and authenticated, from inside a hook they never read, and
        could name one their account cannot reach.
        """
        argv = self._record().extractor.argv
        self.assertEqual(
            argv[0], "copilot",
            f"the first rung of the extractor chain is {argv[0]!r}, not this host's own "
            "CLI, so the README's promise that capture uses your own model is false")
        self.assertNotIn(
            "--model", argv,
            "the extractor pins a model, which overrides the one this user configured "
            "and may name one their account cannot reach")

    def test_the_extractor_cannot_run_tools_with_the_text_it_is_handed(self) -> None:
        """`copilot -p` executes tools WITHOUT `--allow-all-tools` - measured, a probe told
        to run a shell command ran it. What the extractor is handed is a mined turn:
        arbitrary text, including anything the user pasted into their session. So the argv
        must grant no tools, and this asserts the guard is still on it.

        Stated positively, against the mechanism measured to work. `--available-tools=`
        with an empty value did NOT restrict, `--excluded-tools=bash` left
        `read_bash`/`list_bash` behind, and `--deny-tool=shell` did not stop `bash`; an
        allowlist naming a tool that does not exist is the one form that granted nothing.
        """
        argv = self._record().extractor.argv
        allow = [a for a in argv if a.startswith("--available-tools")]
        self.assertEqual(len(allow), 1,
                         "the extractor argv does not restrict the tools available to "
                         "the model it hands mined turn text to")
        self.assertNotEqual(allow[0], "--available-tools=",
                            "an empty allowlist does not restrict on this client - "
                            "measured, the probe still ran bash")
        self.assertEqual(argv[-1], "-p",
                         "`-p` must be last: lib/extract.py appends the prompt as the "
                         "final argument and `-p` takes it as its value")

    def test_the_transcript_reader_mines_a_real_session_from_this_client(self) -> None:
        """The code half of this port, against a transcript captured FROM the client.

        Copilot's session log is a third shape -- `{"type": "user.message", "data": {...}}`
        -- so `lib/transcript.py` carries a reader for it. Read with the Claude reader
        every line formats to `[]`, and the symptom is not an error: capture runs on every
        turn, mines an empty string, and logs `no turn to mine` forever while every other
        gate here stays green. That is the failure this fixture exists to catch, and only
        a real transcript catches it -- a hand-written one would be written from the same
        assumption the reader is.

        The fixture is one real session that typed a prompt, created a file, read it back
        and edited it, with a recall block injected into it. Every assertion below is a
        different way this reader has already been got wrong on some host in this family.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from core import host as bind  # noqa: PLC0415
            import hosts.copilot as record  # noqa: PLC0415

            bind.use(record.HOST)
            from lib import transcript  # noqa: PLC0415
        finally:
            sys.path.remove(str(HOOKS))

        # `lib.transcript` caches the bound host in a module-level `_HOST` at IMPORT time,
        # so `use()` above only takes effect for whichever test imports it first. Asserted
        # rather than assumed: a future test that imports it under a different host would
        # otherwise make this one fail with "the reader mined an empty turn", which names
        # the reader and not the import order that actually caused it.
        self.assertIs(transcript._HOST, record.HOST,
                      "lib.transcript was already imported under another host, so this "
                      "test is exercising that host's reader rather than Copilot's")

        raw = (EVIDENCE / "transcript.jsonl").read_bytes()
        turn, injected = transcript.last_turn_with_injections(raw)

        self.assertTrue(turn, "the reader mined an empty turn from a real session")
        self.assertIn("User: ", turn,
                      "no typed prompt was recovered, so capture has no turn boundary")
        self.assertIn("Claude used create", turn,
                      "what the turn DID is missing -- a fact grounded in the action "
                      "rather than stated in the reply would be lost on this host alone")
        self.assertIn("Claude used edit", turn)
        self.assertNotIn(
            "Claude used view", turn,
            "`view` is a read and is excluded exactly as Claude Code's `Read` is; "
            "including it puts every file the agent opened into the turn as if it "
            "were work")
        self.assertTrue(
            injected,
            "the echo filter saw nothing, so a memory the model was SHOWN and then "
            "restated would be mined back in and stored again -- every session, with a "
            "successful receipt each time")

    def test_the_echo_filter_sees_the_standing_block_and_not_only_the_prompt(self) -> None:
        """The narrower half of the guard above, because it was got wrong here first.

        The reader originally took injections from the transformed prompt alone. Copilot's
        `SessionStart` block never enters the transcript as a message at all, so standing
        memories were unprotected: the model restates one it was shown at session start,
        capture mines the reply, the filter has nothing to match, and the fact is written
        again. Copilot records every hook's own output as a `hook.end` entry, which covers
        BOTH events, and that is what the filter reads now.

        Asserted through the `hook.end` entry specifically, so that if a later client stops
        logging hook output this goes red rather than quietly losing half its coverage.
        """
        sys.path.insert(0, str(HOOKS))
        try:
            from core import host as bind  # noqa: PLC0415
            import hosts.copilot as record  # noqa: PLC0415

            bind.use(record.HOST)
            from lib import transcript  # noqa: PLC0415
        finally:
            sys.path.remove(str(HOOKS))

        # See the sibling test above: the bound host is cached at import time, so this
        # says so by name rather than failing as an empty result.
        self.assertIs(transcript._HOST, record.HOST,
                      "lib.transcript was already imported under another host")

        # Built from the library's own marker constant rather than retyped. The markers
        # contain an em dash; a hyphen typed here instead matches nothing, the filter
        # returns empty, and the test fails for a reason that has nothing to do with the
        # behaviour under test. Reading the constant also means a marker changed upstream
        # cannot leave this guard passing against a string nothing writes any more.
        marker = next(m for m in transcript.RECALL_MARKERS if "already known" in m)
        entry = {"type": "hook.end",
                 "data": {"hookType": "sessionStart", "success": True,
                          "output": {"additionalContext":
                                     f"{marker}:\n- user prefers the samply profiler"}}}
        self.assertEqual(transcript._entry_injected(entry),
                         ["user prefers the samply profiler"],
                         "a standing block logged by the client is invisible to the echo "
                         "filter, so the model restating it re-stores it as a new fact")

    def test_a_hook_never_fails_a_turn_whatever_the_environment(self) -> None:
        """No home directory, no store, no credentials: exit 0 and stay quiet."""
        env = dict(os.environ, HOME="/nonexistent", MEMVARA_HOME="/nonexistent",
                   # WITHOUT this, `capture` on this host forks and returns before the
                   # body is even imported -- `detach_capture=True` -- so the subtest
                   # would check the exit code of the forking wrapper while the code that
                   # opens a store, reads a transcript and writes claims ran unobserved
                   # in a detached child whose stdout goes to /dev/null.
                   MEMVARA_HOOK_DETACHED="1")
        for hook in ("session_start", "recall", "capture", "approve"):
            with self.subTest(hook=hook):
                proc = subprocess.run(
                    [sys.executable, str(HOOKS / "run.py"), hook, "--host", "copilot"],
                    input="{}", capture_output=True, text=True, env=env, timeout=120)
                self.assertEqual(proc.returncode, 0,
                                 f"{hook} exited {proc.returncode}: {proc.stderr[:300]}")
                if proc.stdout.strip():
                    json.loads(proc.stdout)


class SkillTree(unittest.TestCase):
    def test_skill_has_front_matter_and_references(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.splitlines()[0] == "---")
        named = set(re.findall(r"references/([a-z0-9-]+\.md)", text))
        self.assertTrue(named)
        for name in named:
            self.assertTrue((SKILL / "references" / name).is_file(), name)

    def test_matches_library_at_lock_sha(self) -> None:
        lock = _lock()
        self.assertEqual(lock["repo"], "memvara/memvara")
        sha = lock["sha"]
        self.assertEqual(len(sha), 40)
        for rel in ("SKILL.md", "references/hosted-mcp.md"):
            expected = _library_bytes(sha, f"memvara/skills/memvara/{rel}")
            self.assertEqual((SKILL / rel).read_bytes(), expected, rel)

    def test_the_vendored_skill_is_not_behind_the_library(self) -> None:
        """The whole tree, against the library's CURRENT default branch.

        `test_matches_library_at_lock_sha` cannot catch a stale sync and is not supposed
        to: it compares the copy against the sha the copy itself names, so a lock and a
        tree frozen together agree with each other forever. That is exactly how this repo
        shipped a skill five commits behind -- `skill-sync.yml` dying every night on a
        permission the organization pins, nothing here going red, and the agreement
        between the two stale files being the thing that hid it.

        Two deliberate choices about noise. It compares BYTES rather than shas, so the
        library moving does not fail this repository -- only the library's *skill* moving
        does, which is rare. And it compares the file SET as well, because a new reference
        file upstream is drift that a per-file comparison of the files we already have
        would never see.

        When the library cannot be reached this SKIPS rather than passes. A skip is
        visible in the run output; a pass is not, and a check that silently succeeds when
        it could not look is the failure it exists to prevent, one level up.
        """
        try:
            head = _library_head()
            upstream = _library_skill_files(head)
        except LibraryUnreachable as exc:
            raise unittest.SkipTest(
                f"library unreachable, drift NOT checked: {exc}") from exc

        self.assertTrue(upstream, "the library reported an empty skill tree")
        ours = {str(path.relative_to(SKILL))
                for path in SKILL.rglob("*") if path.is_file()}
        self.assertEqual(
            ours, upstream,
            f"the vendored skill's file set differs from the library at {head[:7]} — "
            "run scripts/sync_plugin_repos.py from the library and update skill.lock")

        drifted = []
        for rel in sorted(upstream):
            expected = _library_bytes(head, f"memvara/skills/memvara/{rel}")
            if (SKILL / rel).read_bytes() != expected:
                drifted.append(rel)
        self.assertEqual(
            drifted, [],
            f"vendored skill is behind memvara/memvara@{head[:7]}: {drifted} — "
            "sync it")


class License(unittest.TestCase):
    def test_apache(self) -> None:
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)


class SharedInstructions(unittest.TestCase):
    """CLAUDE.md is shared across every plugin repo, and nothing used to carry it.

    It was hand-copied and it drifted: eleven of fourteen sections were byte-identical
    across all seven repositories while a section written in one of them reached none of
    the others. The canonical is `plugin-claude.md` in the library; `skill-sync.yml`
    composes this file from it and preserves the `local:` block, because two sections
    legitimately differ per repo — a repository's own runtime facts, and hook rules that
    only one plugin needs.

    Without this guard the sync would be a tidier way to drift rather than an end to it,
    which is the objection the section it carries makes about hand-maintained copies.
    """

    BEGIN = "<!-- local: begin"
    END = "<!-- local: end -->"

    def _text(self) -> str:
        return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    def test_the_local_block_is_delimited_exactly_once(self) -> None:
        """Two of either marker and the splice takes the wrong span; none and the composer
        refuses rather than replacing this repository's sections with a placeholder.
        """
        text = self._text()
        self.assertEqual(text.count(self.BEGIN), 1)
        self.assertEqual(text.count(self.END), 1)
        self.assertLess(text.index(self.BEGIN), text.index(self.END))

    def test_the_shared_half_matches_the_library(self) -> None:
        """Compared against the LIBRARY, never against this file's own halves.

        A check that read both halves of one file would prove it internally consistent and
        nothing else — exactly how a vendored skill sat five commits behind while its own
        drift test passed.
        """
        lock = _lock()
        try:
            canonical = _library_bytes(lock["sha"], "plugin-claude.md").decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(
                f"library has no plugin-claude.md at {lock['sha'][:7]}: {exc}") from exc
        text = self._text()
        head, rest = text.split(self.BEGIN, 1)
        _, tail = rest.split(self.END, 1)
        want_head, want_tail = canonical.split("@@LOCAL@@\n", 1)
        self.assertEqual(head, want_head,
                         "text above the local block drifted — edit plugin-claude.md in "
                         "memvara/memvara, not the copy here")
        self.assertEqual(tail.lstrip("\n"), want_tail.lstrip("\n"),
                         "text below the local block drifted from plugin-claude.md")

    def test_the_local_block_holds_what_only_this_repo_knows(self) -> None:
        """Not decorative: it carries the two sections that differ per repo. A sync that
        flattened it would lose them silently — the file would still read as a complete
        CLAUDE.md, just one belonging to a different repository.
        """
        local = self._text().split(self.BEGIN, 1)[1].split(self.END, 1)[0]
        self.assertIn("Runtime facts that cost hours to find", local)
        self.assertIn("If this repo ships hooks", local)


class Hygiene(unittest.TestCase):
    def test_no_npx_in_json(self) -> None:
        """No JSON *this repo ships* may reach for npx.

        `_library` is skipped because it is not ours: CI checks the library out there, at
        `skill.lock`'s sha, so the drift test can run offline. The moment that lock moves
        to a sha where the library has an npm package, an unfiltered scan reads
        `_library/npm/memvara/package.json` -- whose description legitimately begins "npx
        memvara" -- and fails a sync PR for a string in another repository. That is not
        hypothetical: it happened in claude-memvara on 2026-08-25, and this lock bump is
        the one that would have done it here.

        The scan stays repo-wide rather than narrowing to `plugin/`: the rule is about
        anything shipped from here, and an allowlist of directories stops covering the
        next one added.
        """
        for path in ROOT.rglob("*.json"):
            if {"node_modules", "_library"} & set(path.parts):
                continue
            self.assertNotIn("npx", path.read_text(encoding="utf-8"), path)

    def test_no_app_manifest_and_no_commands(self) -> None:
        """`plugin/hooks` used to be asserted ABSENT here. It ships now, and its guards
        are the `Hooks` class above -- stated positively, so a deletion fails them."""
        self.assertFalse((PLUGIN / ".app.json").exists())
        self.assertFalse((PLUGIN / "commands").exists())

    def test_github_org(self) -> None:
        env = os.environ.get("GITHUB_REPOSITORY")
        if env:
            self.assertEqual(env, REPO_NAME)

class VscodeManifest(unittest.TestCase):
    def test_manifest(self) -> None:
        body = _json(PLUGIN / ".github" / "plugin.json")
        self.assertEqual(body["name"], "memvara")
        self.assertEqual(body["repository"], f"https://github.com/{REPO_NAME}")

    def test_marketplace(self) -> None:
        body = _json(ROOT / ".github" / "plugin" / "marketplace.json")
        self.assertEqual(body["plugins"][0]["source"], "./plugin")

    def test_the_mcp_config_is_where_this_client_looks_and_shaped_the_way_it_reads(self) -> None:
        """This replaces `test_mcp_uses_servers_not_mcpServers`, which asserted the exact
        opposite and was green the whole time the server never loaded.

        That guard read the file this repository ships and checked it against a claim this
        repository also made. Both agreed, both were wrong, and nothing compared either to
        the client -- the defect shape CLAUDE.md describes at length. Measured on Copilot
        CLI 1.0.82 with four plugins installed side by side and one session listing what
        loaded:

        * `.mcp.json` with an `mcpServers` key -- LOADED
        * `.github/mcp.json` with an `mcpServers` key -- LOADED
        * `.mcp.json` with a `servers` key -- not loaded
        * `mcp.json` with a `servers` key, which is what shipped -- not loaded

        So the plugin advertised a skill telling the model to use `memory_*` tools that
        were never registered on this host. Both halves of the fix are asserted, because
        either one alone still leaves the server absent.
        """
        self.assertFalse((PLUGIN / "mcp.json").exists(),
                         "plugin/mcp.json is not a path this client reads; the config "
                         "must be .mcp.json")
        body = _json(PLUGIN / ".mcp.json")
        self.assertIn("mcpServers", body,
                      "a `servers` key is not read by this client -- measured, the server "
                      "simply does not appear in session.mcp_servers_loaded")
        self.assertNotIn("servers", body)
        server = body["mcpServers"]["memvara"]
        self.assertEqual(server["url"], HOSTED)
        self.assertEqual(server["type"], "http")
        self.assertNotIn("command", server)

    def test_readme(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        # `mcpServers`, spelled out: the old assertion was on the substring "servers",
        # which the wrong key satisfied too and so could never have caught the config
        # this repository actually shipped.
        self.assertIn("mcpServers", text)
        self.assertIn(HOSTED, text)
        self.assertNotIn("npx ", text)
        self.assertNotIn("chatgpt", text.lower())

    def test_plugin_tree(self) -> None:
        allowed = {
            pathlib.Path(".github") / "plugin.json",
            pathlib.Path(".mcp.json"),
        }
        for path in SKILL.rglob("*"):
            if path.is_file():
                allowed.add(path.relative_to(PLUGIN))
        # The hook tree is allowlisted file by file in `ALLOWED_HOOK_FILES`, which the
        # `Hooks` class checks in both directions. Widening it here to `hooks/**` would
        # make that list decorative.
        for rel in ALLOWED_HOOK_FILES:
            allowed.add(pathlib.Path("hooks") / rel)
        # `__pycache__` is gitignored and never shipped, but `rglob` sees it the moment
        # anything here imports the hook tree -- and this suite does, twice. Filtered the
        # same way `Hooks._ours()` filters it, rather than left to make the run's outcome
        # depend on whether the tests had been run before.
        found = {p.relative_to(PLUGIN) for p in PLUGIN.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts}
        self.assertFalse(found - allowed, found - allowed)


class Version(unittest.TestCase):
    """Every version this repository states must be the same one, and none may hide.

    Five skill syncs shipped under 0.1.0. The vendored skill is the whole of what a client
    receives here, it changed five times, and the string a client compares never moved.
    `claude-memvara` was caught by the identical shape at larger scale -- twenty-one
    commits on main behind an unchanged version, `/plugin update` answering "already at
    the latest version" for every one of them.

    Three deliberate choices, each of them paid for by a sabotage run.

    Files are found by walking the tree, not by reading a list, so a manifest nobody
    remembered cannot go unchecked. `DECLARED` is then the completeness half -- it names
    the manifests that MUST carry a version, and it is compared against the walk in both
    directions, which is what keeps a hand-written list from quietly narrowing coverage.

    The file set comes from `git ls-files`, not from the filesystem. Two sweeps of the
    tree were tried first and both were wrong in a way a passing run could not show: one
    ignored directories by absolute path, which excluded the entire repository whenever the
    checkout was a worktree (those live under `.claude/worktrees/`, so `.claude` was in the
    parts of every path); the next was caught by CI dragging in six manifests from the
    library checkout under `_library/`. Git already knows which files this repository owns.

    And the assertions demand presence rather than absence of the wrong value. The
    coverage check was first written as a bare set comparison and passed on that broken
    walk because both sides were empty; the value check alone still passes when one
    manifest of several drops its version entirely. A guard an absence satisfies has
    stopped guarding.
    """

    VERSION = "0.2.4"
    DECLARED = {
        '.github/plugin/marketplace.json',
        'plugin/.github/plugin.json',
    }

    @classmethod
    def _walk(cls, node: object, where: str = ""):
        """Every `version` string at any depth, with the pointer that reached it."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "version" and isinstance(value, str):
                    yield f"{where}.{key}", value
                else:
                    yield from cls._walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from cls._walk(value, f"{where}[{index}]")

    @classmethod
    def _candidates(cls) -> list:
        """Every JSON file this repository TRACKS -- asked of git, not of the filesystem.

        The filesystem is the wrong referent. CI checks the library out into `_library/`,
        which carries the sibling plugins' own manifests, and an `rglob` swept all six into
        the walk; a denylist would then have to grow a name for every scratch directory
        anyone ever creates, and the first one nobody thought of is a false failure. What
        the question actually means is "files this repository owns", and git is the thing
        that knows. Untracked checkouts and nested worktrees fall out for free.

        No fallback when git cannot answer. A fallback here would silently cover less than
        the caller believes, which is the failure this whole class exists to prevent.
        """
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "*.json"],
            check=True, capture_output=True, text=True).stdout
        return [
            ROOT / name for name in listed.split("\0")
            if name and pathlib.PurePath(name).name != "package-lock.json"
        ]

    def _stated(self) -> list:
        found = []
        for path in self._candidates():
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            found.extend((path, where, value) for where, value in self._walk(body))
        return found

    def test_every_version_this_repo_states_is_the_released_one(self) -> None:
        stated = self._stated()
        self.assertTrue(
            stated, "no file states a version at all -- this guard has stopped guarding")
        for path, where, value in stated:
            self.assertEqual(
                value, self.VERSION,
                f"{path.relative_to(ROOT)}{where} says {value!r}; a partial bump is how a "
                "client gets told it is current while the contents moved underneath it")

    def test_exactly_the_manifests_that_must_declare_a_version_do(self) -> None:
        """Both directions, because each catches a mistake the other cannot see.

        A file the walk misses is a version nobody checks. A file that has stopped
        declaring one is a manifest shipping unversioned -- invisible to the value check
        above, which goes green as soon as any other file still says the right thing.
        Confirmed by sabotage: deleting the key from one of three manifests left it green.
        """
        reached = {str(path.relative_to(ROOT)) for path, _where, _value in self._stated()}
        by_text = {
            str(path.relative_to(ROOT)) for path in self._candidates()
            if '"version"' in path.read_text(encoding="utf-8")
        }
        self.assertEqual(by_text, self.DECLARED, "a manifest gained or lost its version")
        self.assertEqual(reached, self.DECLARED, "the JSON walk missed a stated version")

    def test_the_release_number_is_written_down_exactly_once_in_this_suite(self) -> None:
        """`VERSION` above is the only place the tests name it.

        Ported from claude-memvara, which learned it the same way this repository just
        did: another test asserted the release literally, so a bump had to be applied in
        two places and one of them was missed. Every extra place is the mechanism a
        partial bump needs, and a partial bump is what tells a client it is current while
        the contents moved underneath it.

        The duplicates that prompted this now read `Version.VERSION` instead, which is
        why they no longer count.
        """
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(
            source.count(f'"{self.VERSION}"'), 1,
            f"{self.VERSION} appears more than once in this file; VERSION is meant to be "
            "the single place the suite states the release")


def _readme_prose(root: pathlib.Path) -> str:
    """The README with every run of whitespace collapsed to one space.

    Prose wraps, and where it wraps is not a fact about what it says. Matching the raw
    text pinned a line break: reflowing a paragraph turned a guard red while the sentence
    it guards was present and correct, and the cheapest way out of that is to delete the
    guard. It matters for the negative assertion too -- a claim reintroduced with a
    different wrap would slip past `assertNotIn` on the raw text.
    """
    return " ".join(root.joinpath("README.md").read_text(encoding="utf-8").split())


class ModuleShape(unittest.TestCase):
    """Nothing may be defined below `unittest.main()`.

    Measured, not imagined: `AuthScript` was appended to the end of this file, after the
    `__main__` block. Under `unittest discover` the module is imported, the block does not
    run, and every test is collected. Run directly -- `python3 test/test_plugin.py`, the
    obvious way to check one file -- `unittest.main()` executes before the class exists
    and five guards silently do not run. Both invocations printed `OK`: 26 tests one way
    and 21 the other, with nothing in the output saying so.

    That is this repository's signature failure in miniature, so it gets a guard rather
    than a fixed comment: a passing run must not be able to mean "the check never ran".
    """

    def test_nothing_is_defined_after_the_main_block(self) -> None:
        import ast

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        body = ast.parse(source).body
        guards = [i for i, node in enumerate(body)
                  if isinstance(node, ast.If) and "__main__" in ast.dump(node.test)]
        self.assertEqual(len(guards), 1, "expected exactly one __main__ block")
        after = [type(node).__name__ for node in body[guards[0] + 1:]]
        self.assertEqual(
            after, [],
            f"{after} is defined after `unittest.main()`, so `python3 test/test_plugin.py` runs "
            "without it and still prints OK")


class AuthScript(unittest.TestCase):
    """The skill ships the device-code flow, because this host has nowhere else to put it.

    A Copilot plugin's components are agents, skills, hooks, MCP servers and LSP servers.
    Commands are not among them, so `/memvara authenticate` exists on `claude-memvara` and
    `grok-memvara` and cannot exist here. What this host does load is the skill, and
    skill-relative paths were measured on it before anything was built on them: a probe
    skill whose SKILL.md held no nonce and pointed at a sibling file came back with the
    nonce (`skill(mvprobe)` then `Read secret.md`), and came back "No mvprobe skill is
    available to me" with the registration removed and every file still on disk.

    The bytes arrive by vendoring and `SkillTree` diffs them against the library. These
    check what vendoring cannot: that the file is here, that it RUNS, and that a person is
    told it exists.
    """

    SCRIPT = SKILL / "scripts" / "memvara_auth.py"
    COMMANDS = ("authenticate", "login", "logout", "stats")

    def test_the_skill_ships_the_auth_script(self) -> None:
        """Positive, because the failure to catch is a deletion: "no unexpected file in
        the skill tree" passes on a plugin that stopped shipping the one a locked-out
        user needs."""
        self.assertTrue(
            self.SCRIPT.is_file(),
            f"{self.SCRIPT.relative_to(ROOT)} is missing; the README tells the user it "
            "is there and the skill tells the model to run it")

    def test_the_script_runs_here_and_names_every_command(self) -> None:
        """Executed rather than read. A byte diff against the library cannot see a broken
        script, because a library that shipped one hands every repo two copies that are
        equally broken and agree with each other.

        No network: an unknown command is refused on shape before anything is dialled.
        """
        done = subprocess.run(
            [sys.executable, str(self.SCRIPT), "not-a-command"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        for command in self.COMMANDS:
            self.assertIn(command, done.stdout,
                          f"the usage this prints omits {command}")

    def test_the_readme_says_the_script_is_here_and_where(self) -> None:
        """The path is asserted and then RESOLVED, so a README naming a plausible-looking
        path into the wrong directory fails here rather than sending someone to a file
        that is not there."""
        text = _readme_prose(ROOT)
        quoted = "skills/memvara/scripts/memvara_auth.py"
        self.assertIn(quoted, text,
                      "the README never mentions the auth script, so the only way to "
                      "find it is to read the skill")
        self.assertTrue((PLUGIN / quoted).is_file(),
                        f"the README says {quoted}, and nothing is there")
        self.assertIn("no `pip install`", text,
                      "the README does not say the script needs nothing installed, "
                      "which is the reason it can rescue a locked-out machine")

    def test_the_readme_says_this_host_has_no_slash_commands(self) -> None:
        """The reduced port, stated in the shipped artifact rather than in a plan.

        Asserted positively -- the sentence must be PRESENT -- so deleting the
        explanation fails exactly as loudly as never writing it.
        """
        text = _readme_prose(ROOT)
        self.assertIn("cannot ship slash commands", text)
        self.assertIn("/memvara", text,
                      "the section does not name the thing the user went looking for")

    def test_the_readme_says_what_now_runs_on_the_user_s_machine(self) -> None:
        """This replaces a guard that had gone false while staying green.

        It asserted the README still said "Nothing runs in the background". That was true
        when the plugin was a skill and an MCP block. It stopped being true the moment
        hooks shipped -- `Stop` re-execs itself detached and outlives the turn -- and the
        test would have HELD THE SENTENCE IN PLACE, which is the shape this repository's
        CLAUDE.md warns about: a claim and its guard frozen together, agreeing with each
        other while both are wrong.

        Stated positively, so a rewrite that deletes the section fails as loudly as one
        that lies: the README must name the events, and must say where the hooks account
        for themselves, because a user who cannot see a hook working has nowhere to look
        when it breaks.
        """
        text = _readme_prose(ROOT)
        self.assertNotIn("no local Python process", text,
                         "the README still claims no Python ships, and four hooks do")
        self.assertNotIn(
            "Nothing runs in the background", text,
            "the README still promises nothing runs in the background, and capture now "
            "forks a child that outlives the turn")
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"):
            self.assertIn(event, text,
                          f"the README does not tell the reader that {event} runs")
        self.assertIn("~/.memvara/.hooks/", text,
                      "the README does not say where the hooks account for themselves, "
                      "and nothing they print reaches the screen on this host")
        # The claim this replaces was "every one of them writes a line", and it was FALSE:
        # approve.py has no logging call and run.py notes only failures, skips and the
        # capture detach, so a successful auto-approve leaves no trace. The README said
        # otherwise and this test asserted the README said it -- a claim and its guard
        # frozen together, both wrong, which is the shape CLAUDE.md is written about.
        # Requiring the exception to be stated is what stops it being quietly re-broadened.
        self.assertNotIn(
            "Every one of them writes a line", text,
            "approve does not write a log line, so this claim is false for a quarter of "
            "the hooks the README has just listed")
        self.assertIn("PreToolUse` is the exception", text,
                      "the README does not tell the reader that auto-approve is silent, "
                      "so a matcher that has stopped matching looks exactly like one "
                      "that is working")


class SkillSyncWorkflow(unittest.TestCase):
    """`skill-sync.yml` must open a pull request only when there is something in it.

    The sibling of the hook-sync guards, added for the same pair of defects, found in
    `hooks-sync.yml` and present here verbatim.
    """

    SOURCE = ROOT / ".github" / "workflows" / "skill-sync.yml"

    def test_the_sync_decides_before_it_rewrites_the_lock(self) -> None:
        """A sha bumped first makes the decision fire on every library commit.

        `skill.lock`'s sha used to be written before anything was compared, so the
        comparison saw the lock differ after ANY commit to the library -- one touching
        only the core, the tests or the docs included -- and opened a pull request whose
        entire diff was that one line. Seven repositories carried exactly such a PR,
        pinning one sha, open and unread, until they were closed by hand. Both shas name
        a commit holding these exact bytes when the tree is unchanged, so both are
        truthful, and the freshness gate compares BYTES against the library's current
        HEAD rather than against the pinned sha -- it is green either way.

        Stated positively: the decision must name the skill and CLAUDE.md, and must NOT
        name `skill.lock`, which is the derived half.
        """
        source = self.SOURCE.read_text(encoding="utf-8")
        skill = SKILL.relative_to(ROOT).as_posix()
        self.assertIn(f'if [ -z "$(git status --porcelain -- {skill} CLAUDE.md)" ]; then',
                      source,
                      f"skill-sync.yml must decide on {skill} and CLAUDE.md, before the "
                      "lock is written and without the lock in the comparison")
        decision = source.index("git status --porcelain")
        self.assertLess(decision, source.index("skill.lock').write_text"),
                        "the decision must be taken BEFORE skill.lock is rewritten, or "
                        "the rewritten sha is what the decision sees")

    def test_the_sync_can_see_a_file_the_library_added(self) -> None:
        """`git diff` cannot, and that is how an addition is dropped in silence.

        A file the library ADDS lands untracked after `cp -R`, and `git diff --quiet`
        never reports it -- so the addition sets changed=false, is deleted by the next
        run's `rm -rf`, and is re-copied, nightly, forever. `hosts/cursor.py` was exactly
        such an addition in the hook tree. `git status --porcelain` lists untracked
        entries; this asserts the blind command is gone rather than merely that the
        seeing one is present, because both can be there at once.
        """
        self.assertNotIn("git diff --quiet", self.SOURCE.read_text(encoding="utf-8"),
                         "`git diff` cannot see a file the library ADDED")


if __name__ == "__main__":
    unittest.main()
