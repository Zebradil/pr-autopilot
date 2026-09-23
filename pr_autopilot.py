#!/usr/bin/env python3
"""pr-autopilot — deterministic triage of automated dependency-update pull requests.

The verdict for a pull request is a lookup: facts from the bot and the checks, policy from the
repository's own configuration. No language model participates. See docs/adr/ for why.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import re
import shlex
import shutil
import signal
import string
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone

import tomllib

__version__ = "1.2.0"  # x-release-please-version

POLICY_PATH = ".github/pr-autopilot.toml"
STATE_MARKER = "pr-autopilot:state"
UPGRADES_MARKER = "pr-autopilot:upgrades"
DEFAULT_BOTS = ("renovate", "dependabot")

PR_FIELDS = (
    "number,title,author,body,labels,state,isDraft,mergeable,mergeStateStatus,"
    "statusCheckRollup,headRefName,url,comments,files"
)

FAILING_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "STARTUP_FAILURE",
    "ACTION_REQUIRED",
}
FAILING_STATES = {"FAILURE", "ERROR"}
PENDING_STATES = {"PENDING", "EXPECTED"}

# Update classes a policy table can name. "unknown" is what we emit when the bot did not tell us
# and the body could not be parsed; it is deliberately not mergeable by default.
CLASSES = ("patch", "minor", "major", "digest", "pin", "lockfile", "unknown")

# A pull request that touches only these files is lock-file maintenance whatever its body says,
# which is how Renovate itself defines the class. Read only when the body names no upgrade.
LOCK_FILES = frozenset({
    "flake.lock", "Cargo.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lockb", "bun.lock", "poetry.lock", "uv.lock", "Pipfile.lock",
    "pdm.lock", "go.sum", "Gemfile.lock", "composer.lock", "mix.lock", "gradle.lockfile",
    "Package.resolved", "Podfile.lock", "pubspec.lock", "packages.lock.json", "deno.lock",
})


# --- verdicts ---------------------------------------------------------------------------------
# Ordered permissive -> conservative. Combining verdicts always takes the maximum, which is what
# makes "an agent can only tighten a verdict" a property of the code rather than a promise.

VERDICTS = ("merge", "gate", "wait", "repair", "escalate", "hold")
IGNORE = "ignore"


def bot_name(login: str) -> str:
    """`gh` renders the same bot as "app/renovate" or "renovate[bot]" depending on the command."""
    return (login or "").removeprefix("app/").removesuffix("[bot]").lower()


def rank(verdict: str) -> int:
    return VERDICTS.index(verdict)


def worst(*verdicts: str) -> str:
    return max(verdicts, key=rank)


class Label:
    HOLD = "autopilot/hold"
    ESCALATED = "autopilot/escalated"
    REPAIRING = "autopilot/repairing"
    GATED = "autopilot/gated"
    REVIEWED_OK = "autopilot/reviewed-ok"


LABEL_COLOURS = {
    Label.HOLD: ("b60205", "pr-autopilot: never touch this pull request"),
    Label.ESCALATED: ("d93f0b", "pr-autopilot: needs a human"),
    Label.REPAIRING: ("fbca04", "pr-autopilot: an agent is fixing this"),
    Label.GATED: ("c5def5", "pr-autopilot: waiting on a gate"),
    Label.REVIEWED_OK: ("0e8a16", "pr-autopilot: cleared by a human, may merge"),
}

VERDICT_LABEL = {
    "escalate": Label.ESCALATED,
    "repair": Label.REPAIRING,
    "gate": Label.GATED,
}


# --- facts ------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Upgrade:
    name: str
    update_class: str
    current: str = ""
    new: str = ""


@dataclasses.dataclass(frozen=True)
class Facts:
    repo: str
    number: int
    title: str
    author: str
    url: str
    head_ref: str
    state: str
    is_draft: bool
    mergeable: str
    merge_state: str
    labels: frozenset[str]
    upgrades: tuple[Upgrade, ...]
    checks_failing: tuple[str, ...]
    checks_pending: tuple[str, ...]
    checks_total: int
    state_comment: dict
    # The repository is gated on Atlantis; only then is `plan` read.
    plan_check: bool = False
    # Summary line of the latest Atlantis plan; "" when that plan has none, None when no plan ran.
    plan: str | None = None

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(u.update_class for u in self.upgrades) or ("unknown",)

    @property
    def attempts(self) -> int:
        return int(self.state_comment.get("attempts", 0))


def check_lists(rollup) -> tuple[list[str], list[str]]:
    """Split a statusCheckRollup into (failing, pending) check names."""
    failing, pending = [], []
    for c in rollup or []:
        name = c.get("name") or c.get("context") or "?"
        if c.get("__typename") == "StatusContext" or "state" in c and "status" not in c:
            state = (c.get("state") or "").upper()
            if state in FAILING_STATES:
                failing.append(name)
            elif state in PENDING_STATES:
                pending.append(name)
        else:
            if (c.get("status") or "").upper() != "COMPLETED":
                pending.append(name)
            elif (c.get("conclusion") or "").upper() in FAILING_CONCLUSIONS:
                failing.append(name)
    return failing, pending


def parse_upgrades(body: str, files: tuple[str, ...] = ()) -> list[Upgrade]:
    """Upgrades from the bot's own metadata, falling back to its PR body table, then to the diff.

    ADR 0007: we never infer an update class from prose. The JSON marker is what onboarding asks
    Renovate to emit; the table is the fallback for bots we cannot configure. A body that names
    nothing but a diff made only of lock files is lock-file maintenance. Anything else is
    reported as "unknown", which policy treats conservatively.
    """
    body = body or ""
    marker = re.search(rf"<!--\s*{UPGRADES_MARKER}\s*(\[.*?\])\s*-->", body, re.S)
    if marker:
        try:
            return [
                Upgrade(
                    name=u.get("depName", "?"),
                    update_class=normalise_class(u.get("updateType", "")),
                    current=u.get("currentVersion") or u.get("currentValue", ""),
                    new=u.get("newVersion") or u.get("newValue", ""),
                )
                for u in json.loads(marker.group(1))
            ]
        except (json.JSONDecodeError, AttributeError):
            pass
    upgrades = parse_body_table(body) or parse_dependabot(body)
    if not upgrades and files and all(os.path.basename(f) in LOCK_FILES for f in files):
        upgrades = [Upgrade(name=f, update_class="lockfile") for f in files]
    return upgrades


def normalise_class(raw: str) -> str:
    raw = (raw or "").strip().lower()
    aliases = {
        "lockfilemaintenance": "lockfile",
        "lock file maintenance": "lockfile",
        "pindigest": "pin",
        "bump": "patch",
        "rollback": "unknown",
        "replacement": "unknown",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in CLASSES else "unknown"


VERSION = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
HEX = re.compile(r"^[0-9a-f]{7,64}$")


def version_parts(raw: str) -> tuple[int, int, int] | None:
    m = VERSION.search(raw or "")
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())  # type: ignore[return-value]


def classify(current: str, new: str) -> str:
    """Update class from version arithmetic, for bots that do not label it themselves.

    Comparing two version strings is arithmetic, not prose inference (ADR 0007). Anything this
    cannot read confidently comes back "unknown", which policy treats conservatively — including
    downgrades, and 0.x minor bumps, which are breaking in most ecosystems.
    """
    current, new = (current or "").strip("`"), (new or "").strip("`")
    if not current or not new or current == new:
        return "unknown"
    if (
        "sha256:" in current
        or "sha256:" in new
        or (HEX.match(current) and HEX.match(new))
    ):
        return "digest"
    cur, nxt = version_parts(current), version_parts(new)
    if not cur or not nxt or nxt < cur:
        return "unknown"
    if nxt[0] != cur[0]:
        return "major"
    if nxt[1] != cur[1]:
        return "major" if cur[0] == 0 else "minor"
    if nxt[2] != cur[2]:
        return "patch"
    return "unknown"


TABLE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
PARENTHETICAL = re.compile(r"\s*\((?:source|changelog|[^)]*)\)\s*$")
CHANGE_SEP = re.compile(r"\s*(?:\u2192|->|»)\s*")
DEPENDABOT_SINGLE = re.compile(
    r"Bumps\s+(?:\[(?P<linked>[^\]]+)\]\([^)]*\)|`?(?P<plain>[\w@/.\-]+)`?)"
    r"\s+from\s+`?(?P<current>[^\s`]+)`?\s+to\s+`?(?P<new>[^\s`]+)`?",
    re.I,
)


def split_change(cell: str) -> tuple[str, str]:
    """ "`1.2.3` → `1.3.0`" into its two sides; anything without an arrow yields nothing."""
    parts = CHANGE_SEP.split(cell, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def parse_body_table(body: str) -> list[Upgrade]:
    """Parse the update table a bot puts in its pull request body.

    Covers what real bots actually emit: Renovate's default columns, the variants without a Type
    or Update column, its lock-file-maintenance table with no Package column at all, and
    Dependabot's grouped Package/From/To table. Cells are read positionally by header name, so a
    repository reordering `prBodyColumns` does not break it.
    """
    rows: list[Upgrade] = []
    header: list[str] | None = None
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [
            PARENTHETICAL.sub("", TABLE_LINK.sub(r"\1", c).strip())
            for c in line.strip("|").split("|")
        ]
        lowered = [c.lower() for c in cells]
        if {"change", "to", "update"} & set(lowered) and (
            "package" in lowered or "update" in lowered
        ):
            header = lowered
            continue
        if header is None or all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        cell = dict(zip(header, cells))
        current, new = cell.get("from", ""), cell.get("to", "")
        if "change" in cell:
            current, new = split_change(cell["change"])
        update_class = normalise_class(cell.get("update", ""))
        if update_class == "unknown":
            update_class = classify(current, new)
        name = cell.get("package") or (
            "(lock files)" if update_class == "lockfile" else ""
        )
        if not name:
            continue
        rows.append(
            Upgrade(
                name=name,
                update_class=update_class,
                current=current.strip("` "),
                new=new.strip("` "),
            )
        )
    return rows


def parse_dependabot(body: str) -> list[Upgrade]:
    """Dependabot's single-update sentence: "Bumps [name](url) from X to Y."."""
    m = DEPENDABOT_SINGLE.search(body or "")
    if not m:
        return []
    name = m.group("linked") or m.group("plain")
    current, new = m.group("current").rstrip("."), m.group("new").rstrip(".")
    return [
        Upgrade(
            name=PARENTHETICAL.sub("", name),
            update_class=classify(current, new),
            current=current,
            new=new,
        )
    ]


def parse_state_comment(comments) -> dict:
    for c in reversed(comments or []):
        m = re.search(
            rf"<!--\s*{STATE_MARKER}\s*(\{{.*?\}})\s*-->", c.get("body", ""), re.S
        )
        if m:
            try:
                state = json.loads(m.group(1))
                # REST wants the numeric id; `id` here is a GraphQL node id, which REST 404s on.
                numeric = re.search(r"#issuecomment-(\d+)$", c.get("url", ""))
                if numeric:
                    state["_comment_id"] = numeric.group(1)
                return state
            except json.JSONDecodeError:
                continue
    return {}


# ponytail: Atlantis's default status name; a server run with another --vcs-status-name needs this
# in policy.
PLAN_CHECK = "atlantis/plan"
PLAN_SUMMARY = re.compile(r"^\d+ projects?, \d+ with changes, \d+ with no changes, \d+ failed$", re.M)
PLAN_CLEAN = re.compile(r"^\d+ projects?, 0 with changes, \d+ with no changes, 0 failed$")
# What Atlantis sets on its plan status when the diff touches no project. It posts no plan comment
# then, so there is nothing to gate on.
PLAN_NO_PROJECTS = "0/0 projects"


def parse_plan(comments, authors: tuple[str, ...] = ()) -> str | None:
    """The summary of the latest Atlantis plan, which may be split over several comments.

    Only the last comment of a split plan carries the summary, so the first part resets it to "".
    With no `authors`, any commenter counts: Atlantis runs as an ordinary user whose association is
    often NONE, so there is nothing else to tell it apart by, and anyone able to comment could post
    a clean summary.
    """
    trusted = {bot_name(a) for a in authors}
    plan = None
    for c in comments or []:
        if trusted and bot_name((c.get("author") or {}).get("login", "")) not in trusted:
            continue
        body = c.get("body", "")
        if body.startswith("Ran Plan for"):
            plan = ""
        if plan is not None and (m := PLAN_SUMMARY.search(body)):
            plan = m.group(0)
    return plan


def facts_from_json(repo: str, pr: dict, atlantis: tuple[str, ...] = ()) -> Facts:
    failing, pending = check_lists(pr.get("statusCheckRollup"))
    return Facts(
        repo=repo,
        number=pr["number"],
        title=pr.get("title", ""),
        author=(pr.get("author") or {}).get("login", ""),
        url=pr.get("url", ""),
        head_ref=pr.get("headRefName", ""),
        state=pr.get("state", ""),
        is_draft=bool(pr.get("isDraft")),
        mergeable=pr.get("mergeable", ""),
        merge_state=pr.get("mergeStateStatus", ""),
        labels=frozenset(l["name"] for l in pr.get("labels") or []),
        upgrades=tuple(
            parse_upgrades(
                pr.get("body", ""), tuple(f["path"] for f in pr.get("files") or [])
            )
        ),
        checks_failing=tuple(failing),
        checks_pending=tuple(pending),
        checks_total=len(pr.get("statusCheckRollup") or []),
        state_comment=parse_state_comment(pr.get("comments")),
        plan_check=any(
            (c.get("name") or c.get("context")) == PLAN_CHECK
            and not (c.get("description") or "").startswith(PLAN_NO_PROJECTS)
            for c in pr.get("statusCheckRollup") or []
        ),
        plan=parse_plan(pr.get("comments"), atlantis),
    )


# --- policy -----------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Policy:
    enabled: bool = True
    bots: tuple[str, ...] = DEFAULT_BOTS
    atlantis: tuple[str, ...] = ()
    table: dict = dataclasses.field(default_factory=dict)
    max_merges: int = 10
    max_repairs: int = 3
    attempt_cap: int = 2
    allow_without_checks: bool = False
    repair_strategy: str = "bot-branch"
    lease_minutes: int = 30

    @staticmethod
    def from_toml(raw: bytes, defaults: Operator | None = None) -> "Policy":
        return Policy.from_dict(tomllib.loads(raw.decode()), defaults)

    @staticmethod
    def from_dict(d: dict, defaults: Operator | None = None) -> "Policy":
        """`defaults` supplies the operator's `bots` and `atlantis` to a policy that omits them."""
        defaults = defaults or Operator()
        if "policy" not in d:
            raise ValueError(
                "no [policy] table; an operator file goes in --config or $PR_AUTOPILOT_CONFIG"
            )
        table = {k: v for k, v in (d.get("policy") or {}).items() if k in CLASSES}
        extras = {"review_when", "allow_without_checks"}
        unknown = {
            k for k in (d.get("policy") or {}) if k not in CLASSES and k not in extras
        }
        if unknown:
            raise ValueError(f"unknown update classes in [policy]: {sorted(unknown)}")
        bad = {v for v in table.values() if v not in VERDICTS}
        if bad:
            raise ValueError(f"unknown verdicts in [policy]: {sorted(bad)}")
        limits = d.get("limits") or {}
        return Policy(
            enabled=d.get("enabled", True),
            bots=tuple(d.get("bots") or defaults.bots),
            atlantis=tuple(d.get("atlantis") or defaults.atlantis),
            table=table,
            max_merges=limits.get("max_merges", 10),
            max_repairs=limits.get("max_repairs", 3),
            attempt_cap=limits.get("attempt_cap", 2),
            allow_without_checks=(d.get("policy") or {}).get(
                "allow_without_checks", False
            ),
            repair_strategy=(d.get("repair") or {}).get("strategy", "bot-branch"),
            lease_minutes=limits.get("lease_minutes", 30),
        )

    def for_class(self, cls: str) -> str:
        return self.table.get(cls, "escalate")


# --- the decision -----------------------------------------------------------------------------


def decide(
    facts: Facts, policy: Policy, now: datetime | None = None
) -> tuple[str, str]:
    """Return (verdict, reason). Pure: no IO, no clock beyond what is passed in."""
    now = now or datetime.now(timezone.utc)

    if facts.state != "OPEN":
        return IGNORE, f"state {facts.state.lower()}"
    if bot_name(facts.author) not in {bot_name(b) for b in policy.bots}:
        return IGNORE, f"author {facts.author} not an allowlisted bot"
    if not policy.enabled:
        return "hold", "autopilot disabled for this repository"
    if Label.HOLD in facts.labels:
        return "hold", f"{Label.HOLD} label"
    if facts.is_draft:
        return "wait", "draft"
    if Label.ESCALATED in facts.labels:
        return "escalate", "already escalated, waiting for a human"
    if leased(facts, policy, now):
        return "wait", "another sweep holds the repair lease"

    if Label.REVIEWED_OK in facts.labels:
        policy_verdict, why = "merge", f"{Label.REVIEWED_OK} label"
    else:
        policy_verdict = worst(*(policy.for_class(c) for c in facts.classes))
        why = "policy: " + ", ".join(sorted(set(facts.classes)))

    if facts.attempts >= policy.attempt_cap:
        return worst(
            policy_verdict, "escalate"
        ), f"{facts.attempts} repair attempts, cap reached"

    if facts.mergeable == "CONFLICTING":
        return worst(policy_verdict, "repair"), "conflicting"
    if facts.checks_failing:
        # One check per line: matrix job names carry their own commas, "validate (a, b, 1.16)".
        return worst(policy_verdict, "repair"), "failing:" + "".join(
            f"\n- {c}" for c in facts.checks_failing[:3]
        )
    if facts.checks_pending:
        return worst(policy_verdict, "wait"), "checks pending"
    if facts.mergeable == "UNKNOWN":
        return worst(policy_verdict, "wait"), "mergeability unknown"
    if not facts.checks_total and not policy.allow_without_checks:
        # An empty check list is not a green one. A repository with no CI has to say so on purpose.
        return worst(policy_verdict, "escalate"), "no checks ran"
    if (
        facts.plan_check
        and not PLAN_CLEAN.match(facts.plan or "")
        and Label.REVIEWED_OK not in facts.labels
    ):
        # A non-empty plan means merging changes infrastructure: drift or a behaviour change.
        # A missing one means nothing proves otherwise.
        if facts.plan is None:
            return worst(policy_verdict, "escalate"), f"{PLAN_CHECK} ran, no Atlantis plan comment"
        return worst(policy_verdict, "escalate"), f"plan: {facts.plan or 'no summary'}"
    return policy_verdict, why


def leased(facts: Facts, policy: Policy, now: datetime) -> bool:
    until = facts.state_comment.get("lease_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except ValueError:
        return False


# --- output -----------------------------------------------------------------------------------
# stdout carries the report (or JSON); progress and diagnostics go to stderr so `--json | jq` works.

VERDICT_COLOURS = {
    "merge": "32", "gate": "33", "wait": "33", "repair": "36", "escalate": "31", "hold": "31",
    IGNORE: "2",
}
QUIET = False


def colour_enabled(stream) -> bool:
    """https://no-color.org; Actions logs render ANSI although the runner's stream is no terminal."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    return stream.isatty()


def paint(text: str, sgr: str, stream) -> str:
    return f"\033[{sgr}m{text}\033[0m" if colour_enabled(stream) else text


def progress(msg: str) -> None:
    if not QUIET:
        print(msg, file=sys.stderr, flush=True)


def error(msg: str) -> None:
    print(f"{paint('pr-autopilot:', '1;31', sys.stderr)} {msg}", file=sys.stderr)


# --- github io --------------------------------------------------------------------------------


class GhError(RuntimeError):
    pass


class PolicyMissing(GhError):
    """The repository has no policy file — the one gh failure that means "not governed"."""


def gh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout).strip())
    return (proc.stdout or proc.stderr).strip()


def gh_json(*args: str):
    return json.loads(gh(*args))


@dataclasses.dataclass(frozen=True)
class Operator:
    """The operator's own file: bot allowlist, named presets, and the fleet with what governs each repo."""

    bots: tuple[str, ...] = DEFAULT_BOTS
    atlantis: tuple[str, ...] = ()
    presets: dict = dataclasses.field(default_factory=dict)
    repos: dict = dataclasses.field(
        default_factory=dict
    )  # "owner/name" -> {"preset": ...} | {"policy": ...} | {}
    default: str | None = (
        None  # preset for a repository with no entry and no in-repo file
    )
    base_dir: str = "."

    @staticmethod
    def load(path: str) -> "Operator":
        with open(path, "rb") as fh:
            d = tomllib.load(fh)
        repos = d.get("repos") or {}
        if isinstance(repos, list):
            repos = {name: {} for name in repos}
        for name, entry in repos.items():
            if (
                not isinstance(entry, dict)
                or set(entry) - {"preset", "policy"}
                or len(entry) > 1
            ):
                raise ValueError(
                    f"repos.{name!r}: want a table with at most one of preset, policy; got {entry!r}"
                )
        presets = d.get("presets") or {}
        default = d.get("default")
        if default is not None and default not in presets:
            raise ValueError(
                f"default: unknown preset {default!r}; known: {sorted(presets)}"
            )
        return Operator(
            bots=tuple(d.get("bots") or DEFAULT_BOTS),
            atlantis=tuple(d.get("atlantis") or ()),
            presets=presets,
            repos=repos,
            default=default,
            base_dir=os.path.dirname(os.path.abspath(path)),
        )

    def preset(self, name: str) -> Policy:
        if name not in self.presets:
            raise ValueError(f"unknown preset {name!r}; known: {sorted(self.presets)}")
        return Policy.from_dict(self.presets[name], self)

    def policy_file(self, path: str) -> Policy:
        with open(os.path.join(self.base_dir, path), "rb") as fh:
            return Policy.from_toml(fh.read(), self)


def default_operator_path() -> str:
    env = os.environ.get("PR_AUTOPILOT_CONFIG")
    if env:
        return env
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "pr-autopilot", "config.toml")


def resolve_policy(
    repo: str, operator: Operator, policy_file: str | None, preset: str | None
) -> Policy:
    """Explicit CLI choice first, then the operator's entry for the repo, the in-repo file, the operator's default."""
    if policy_file:
        with open(policy_file, "rb") as fh:
            return Policy.from_toml(fh.read(), operator)
    if preset:
        return operator.preset(preset)
    entry = operator.repos.get(repo) or {}
    if "policy" in entry:
        return operator.policy_file(entry["policy"])
    if "preset" in entry:
        return operator.preset(entry["preset"])
    try:
        return fetch_policy(repo, operator)
    except PolicyMissing:
        if not operator.default:
            raise
    print(
        f"{repo}: no {POLICY_PATH}; using default preset {operator.default!r}",
        file=sys.stderr,
    )
    return operator.preset(operator.default)


def fetch_policy(repo: str, defaults: Operator | None = None) -> Policy:
    try:
        blob = gh_json("api", f"repos/{repo}/contents/{POLICY_PATH}")
    except GhError as err:
        # Anything else — a bad or missing token, a network fault, a 5xx — is a broken sweep,
        # not an ungoverned repository, and must not be swallowed as one.
        if "404" in str(err) or "Not Found" in str(err):
            raise PolicyMissing(str(err)) from err
        raise
    return Policy.from_toml(base64.b64decode(blob["content"]), defaults)


def fetch_pr(repo: str, number: int, atlantis: tuple[str, ...] = ()) -> Facts:
    pr = gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    if pr.get("mergeable") == "UNKNOWN":
        # GitHub computes mergeability lazily: the first request triggers it, the second sees it.
        progress(f"  #{number}: mergeability not computed yet, retrying in 3s")
        time.sleep(3)
        pr = gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    add_plan_description(repo, number, pr)
    return facts_from_json(repo, pr, atlantis)


def add_plan_description(repo: str, number: int, pr: dict) -> None:
    """Copy the plan status description, which `gh pr view` omits, into the rollup.

    Any failure leaves it out, and the plan gate then holds as if projects were planned.
    """
    plan = [c for c in pr.get("statusCheckRollup") or [] if c.get("context") == PLAN_CHECK]
    if not plan:
        return
    # `gh pr checks` exits non-zero for failing or pending checks but still prints the JSON.
    out = gh("pr", "checks", str(number), "--repo", repo, "--json", "name,description", check=False)
    try:
        described = {c["name"]: c.get("description", "") for c in json.loads(out)}
    except (json.JSONDecodeError, TypeError, KeyError):
        return
    for c in plan:
        c["description"] = described.get(PLAN_CHECK, "")


def list_bot_prs(repo: str, policy: Policy) -> list[int]:
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,author",
    )
    allowed = {bot_name(b) for b in policy.bots}
    return [
        p["number"]
        for p in prs
        if bot_name((p.get("author") or {}).get("login", "")) in allowed
    ]


# --- actions ----------------------------------------------------------------------------------


def approve_and_merge(facts: Facts) -> tuple[bool, str]:
    """Approve, then merge with the fallbacks a protected repository needs."""
    gh(
        "pr",
        "review",
        str(facts.number),
        "--repo",
        facts.repo,
        "--approve",
        check=False,
    )
    out = gh(
        "pr",
        "merge",
        str(facts.number),
        "--repo",
        facts.repo,
        "--squash",
        "--auto",
        "--delete-branch",
        check=False,
    )
    if re.search(r"auto.?merge", out, re.I):
        out = gh(
            "pr",
            "merge",
            str(facts.number),
            "--repo",
            facts.repo,
            "--squash",
            "--delete-branch",
            check=False,
        )
    after = gh_json(
        "pr",
        "view",
        str(facts.number),
        "--repo",
        facts.repo,
        "--json",
        "state,mergeStateStatus",
    )
    if after["state"] == "MERGED":
        return True, "merged"
    if after["mergeStateStatus"] == "BLOCKED":
        return False, "blocked (approved; likely requires a human review)"
    return False, f"not merged: {out or after['mergeStateStatus']}"


def sync_labels(facts: Facts, verdict: str, dry_run: bool) -> None:
    wanted = VERDICT_LABEL.get(verdict)
    ours = {Label.ESCALATED, Label.REPAIRING, Label.GATED}
    add = {wanted} - facts.labels if wanted else set()
    remove = (facts.labels & ours) - ({wanted} if wanted else set())
    if not add and not remove or dry_run:
        return
    args = ["pr", "edit", str(facts.number), "--repo", facts.repo]
    for l in add:
        args += ["--add-label", l]
    for l in remove:
        args += ["--remove-label", l]
    gh(*args, check=False)


def write_state(facts: Facts, dry_run: bool, **updates) -> None:
    """Upsert the sticky comment that carries this PR's autopilot state.

    Updates land in `facts.state_comment` too, so the next write in the same sweep builds on them
    instead of on the state as fetched: a repair's attempt count must survive its closing note.
    """
    facts.state_comment.update(updates, updated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    state = {k: v for k, v in facts.state_comment.items() if not k.startswith("_")}
    body = (
        "**pr-autopilot**\n\n"
        f"{state.get('note', '')}\n\n"
        f"<!-- {STATE_MARKER} {json.dumps(state, sort_keys=True)} -->"
    )
    if dry_run:
        return
    comment_id = facts.state_comment.get("_comment_id")
    if comment_id:
        try:
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{facts.repo}/issues/comments/{comment_id}",
                "-f",
                f"body={body}",
            )
            return
        except GhError:
            pass  # comment deleted meanwhile — fall through to a new one
    url = gh(
        "pr",
        "comment",
        str(facts.number),
        "--repo",
        facts.repo,
        "--body",
        body,
        check=False,
    )
    created = re.search(r"#issuecomment-(\d+)", url)
    if created:
        facts.state_comment["_comment_id"] = created.group(1)


def dispatch_repair(
    facts: Facts, policy: Policy, reason: str, agent_command: str, dry_run: bool
) -> tuple[str, str]:
    """Run the configured agent on one PR. ADR 0011: it edits code, we own GitHub state."""
    if not agent_command:
        return "escalate", "repair needed but no agent configured"
    lease = (
        datetime.now(timezone.utc) + timedelta(minutes=policy.lease_minutes)
    ).isoformat(timespec="seconds")
    write_state(
        facts,
        dry_run,
        attempts=facts.attempts + 1,
        lease_until=lease,
        note=f"Repairing: {reason}",
    )
    prompt = render_prompt(facts, reason, policy)
    if dry_run:
        return "repair", "would dispatch agent"
    progress(f"  #{facts.number}: running agent to repair it")
    stdout = run_agent(agent_command, prompt, facts.number, policy.lease_minutes * 60)
    if stdout is None:
        result = {
            "outcome": "needs-human",
            "summary": f"agent killed after {policy.lease_minutes} minutes, when its lease ran out",
        }
    else:
        result = parse_agent_result(stdout)
    write_state(facts, dry_run, lease_until=None, note=result["summary"])
    if result["outcome"] == "fixed":
        return "wait", f"agent fixed: {result['summary']}"
    return worst("escalate", result.get("verdict_floor", "escalate")), result["summary"]


AGENT_HEARTBEAT = 30  # seconds between "still running" lines


def run_agent(command: str, prompt: str, number: int, timeout: float) -> str | None:
    """The agent's stdout, or None when it outlived `timeout` and was killed.

    The timeout is the lease: past it, another sweep may dispatch a second agent on the same PR.
    stderr is not captured, so whatever the agent logs there shows up live.
    """
    # Own process group: `shell=True` puts a shell between us and the agent, and killing only
    # the shell would leave the agent running and holding stdout open.
    proc = subprocess.Popen(
        command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    stdin = prompt
    try:
        while True:
            left = timeout - (time.monotonic() - started)
            try:
                return proc.communicate(stdin, timeout=max(0.1, min(AGENT_HEARTBEAT, left)))[0]
            except subprocess.TimeoutExpired:
                stdin = None  # already sent; communicate() refuses input on a retry
            elapsed = int(time.monotonic() - started)
            if elapsed >= timeout:
                break
            progress(f"  #{number}: agent still running ({elapsed // 60}m{elapsed % 60:02d}s)")
    finally:
        # Also on Ctrl-C: the agent's own session does not receive the terminal's SIGINT.
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
    return None


def parse_agent_result(stdout: str) -> dict:
    """Last JSON object printed wins; anything unparseable fails conservative (ADR 0011)."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "outcome" in data:
                data.setdefault("summary", "")
                if data["outcome"] not in ("fixed", "needs-human", "gave-up"):
                    data["outcome"] = "needs-human"
                return data
    return {"outcome": "needs-human", "summary": "agent produced no parseable result"}


def render_prompt(facts: Facts, reason: str, policy: Policy) -> str:
    template_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "repair.md"
    )
    with open(template_path) as fh:
        template = fh.read()
    upgrades = "\n".join(
        f"- {u.name}: {u.current} -> {u.new} ({u.update_class})" for u in facts.upgrades
    )
    # Template, not format(): the prompt contains a JSON example full of braces.
    return string.Template(template).safe_substitute(
        repo=facts.repo,
        number=facts.number,
        title=facts.title,
        url=facts.url,
        head_ref=facts.head_ref,
        reason=reason,
        upgrades=upgrades or "- (not parsed)",
        failing=", ".join(facts.checks_failing) or "(none)",
        strategy=policy.repair_strategy,
    )


def escalate(facts: Facts, reason: str, dry_run: bool) -> None:
    write_state(
        facts,
        dry_run,
        note=f"Needs a human: {reason}\n\n"
        f"Remove `{Label.ESCALATED}` and add `{Label.REVIEWED_OK}` to let the autopilot merge it.",
    )


# --- sweep ------------------------------------------------------------------------------------


@dataclasses.dataclass
class Result:
    repo: str
    number: int
    title: str
    verdict: str
    reason: str
    outcome: str


def sweep_repo(repo: str, numbers: list[int], policy: Policy, args) -> list[Result]:
    results: list[Result] = []
    merged = repairs = 0
    for i, number in enumerate(numbers, 1):
        facts = fetch_pr(repo, number, policy.atlantis)
        verdict, reason = decide(facts, policy)
        outcome = "no action"

        if verdict == "merge":
            if merged >= policy.max_merges:
                verdict, reason, outcome = "wait", "max_merges reached", "deferred"
            elif args.dry_run:
                outcome = "would merge"
            else:
                ok, outcome = approve_and_merge(facts)
                merged += ok
        elif verdict == "repair":
            if repairs >= policy.max_repairs:
                verdict, reason, outcome = "wait", "max_repairs reached", "deferred"
            else:
                repairs += 1
                verdict, outcome = dispatch_repair(
                    facts, policy, reason, args.agent, args.dry_run
                )

        # After the branches above: a repair that could not run or did not fix it escalates too.
        if verdict == "escalate" and Label.ESCALATED not in facts.labels:
            if outcome == "no action":
                escalate(facts, reason, args.dry_run)
                outcome = "would escalate" if args.dry_run else "escalated"
            else:
                escalate(facts, f"{outcome}\n\n{reason}", args.dry_run)

        sync_labels(facts, verdict, args.dry_run)
        progress(
            f"  [{i}/{len(numbers)}] #{number} "
            f"{paint(verdict, VERDICT_COLOURS.get(verdict, '0'), sys.stderr)} -> {outcome}"
        )
        results.append(Result(repo, number, facts.title, verdict, reason, outcome))
    return results


def report(results: list[Result], as_json: bool) -> None:
    if as_json:
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
        return
    if not results:
        print("no bot pull requests")
        return
    width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    print(table(results, width, colour=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### pr-autopilot\n\n```\n{table(results, None, colour=False)}\n```\n")


def wrap(text: str, width: int | None, indent: str = "") -> list[str]:
    if not width:
        return [text]
    return textwrap.wrap(text, width, subsequent_indent=indent, break_on_hyphens=False) or [text]


def table(results: list[Result], width: int | None, colour: bool) -> str:
    """TITLE and REASON / OUTCOME wrap to fit `width`; None never wraps (logs, the job summary)."""

    def style(text: str, sgr: str) -> str:
        return paint(text, sgr, sys.stdout) if colour else text

    prs = [f"{r.repo.split('/')[-1]}#{r.number}" for r in results]
    pr_w = max(len("PR"), *map(len, prs))
    verdict_w = max(len("VERDICT"), *map(len, VERDICTS + (IGNORE,)))
    title_w = max(len("TITLE"), *(len(r.title) for r in results))
    details_w = None
    if width:
        room = width - pr_w - verdict_w - 6  # three two-space gutters
        title_w = min(title_w, max(20, room // 2))
        details_w = max(20, room - title_w)

    rows = []
    for pr, r in zip(prs, results):
        title = wrap(r.title, width and title_w)
        details = []
        for line in r.reason.splitlines():
            details += wrap(line, details_w, "  " if line.startswith("- ") else "")
        outcome = "2" if r.outcome == "no action" else "1;" + VERDICT_COLOURS.get(r.verdict, "0")
        details += [style(line, outcome) for line in wrap(f"-> {r.outcome}", details_w, "   ")]
        lines = []
        for i in range(max(len(title), len(details))):
            # Pad before painting: escape codes would otherwise count towards the column width.
            verdict = f"{r.verdict if i == 0 else '':<{verdict_w}}"
            if i == 0:
                verdict = style(verdict, VERDICT_COLOURS.get(r.verdict, "0"))
            cells = (
                f"{pr if i == 0 else '':>{pr_w}}",
                verdict,
                f"{title[i] if i < len(title) else '':<{title_w}}",
                details[i] if i < len(details) else "",
            )
            lines.append("  ".join(cells).rstrip())
        rows.append("\n".join(lines))

    header = f"{'PR':>{pr_w}}  {'VERDICT':<{verdict_w}}  {'TITLE':<{title_w}}  REASON / OUTCOME"
    # Blank lines between rows only when some row spans several; single-line rows stay dense.
    sep = "\n\n" if any("\n" in row for row in rows) else "\n"
    return sep.join([header, *rows])


def create_labels(repo: str, dry_run: bool) -> None:
    for name, (colour, description) in LABEL_COLOURS.items():
        if dry_run:
            print(f"would create {name}")
            continue
        gh(
            "label",
            "create",
            name,
            "--repo",
            repo,
            "--color",
            colour,
            "--description",
            description,
            "--force",
            check=False,
        )
        print(f"{name}")


ONBOARD_HEADER = """\
Onboard the repository in the current directory to pr-autopilot, following the instructions below.

pr-autopilot $version is installed at $root. Paths in the instructions (`templates/`, `docs/manual.md`,
`pr_autopilot.py`) are relative to it; run the engine as `$engine`. Pin the workflows to
`Zebradil/pr-autopilot@v$version`.

"""


def onboard(agent: str | None) -> int:
    """Hand the terminal to an interactive agent primed with the onboarding skill (ADR 0009)."""
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "skills", "pr-autopilot", "SKILL.md")) as f:
        skill = f.read()
    if skill.startswith("---"):
        skill = skill.split("---", 2)[2].lstrip()
    prompt = string.Template(ONBOARD_HEADER).substitute(
        version=__version__, root=root, engine=os.path.join(root, "pr_autopilot.py")
    ) + skill
    if not agent:
        print(prompt)
        print(
            "pr-autopilot: no --agent given, printed the onboarding prompt instead; "
            'pass --agent "claude" (or another interactive agent) to onboard',
            file=sys.stderr,
        )
        return 0
    # Interactive agents take the opening prompt as their last argument (`claude`, `codex`,
    # `cursor-agent`); `{prompt}` places it for those that want a flag, like `opencode --prompt`.
    argv = shlex.split(agent)
    if "{prompt}" in argv:
        argv = [prompt if a == "{prompt}" else a for a in argv]
    else:
        argv.append(prompt)
    try:
        os.execvp(argv[0], argv)
    except OSError as err:
        error(f"{argv[0]}: {err}")
        return 127


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pr-autopilot", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    targets = argparse.ArgumentParser(add_help=False)
    targets.add_argument(
        "--repo", help="owner/name (default: the repository in the current directory)"
    )
    targets.add_argument(
        "--fleet",
        action="store_true",
        help="every repository in the operator file "
        "($PR_AUTOPILOT_CONFIG or ~/.config/pr-autopilot/config.toml)",
    )
    targets.add_argument(
        "--config",
        help="operator file (default: $PR_AUTOPILOT_CONFIG or ~/.config/pr-autopilot/config.toml)",
    )
    targets.add_argument("--dry-run", action="store_true")

    sweep = commands.add_parser(
        "sweep", parents=[targets], help="decide and act on open bot pull requests"
    )
    sweep.add_argument(
        "prs", nargs="*", type=int, help="pull request numbers (default: all bot PRs)"
    )
    sweep.add_argument(
        "--policy", help="policy file to use instead of the one in the repository"
    )
    sweep.add_argument(
        "--preset", help="named preset from the operator file to use as the policy"
    )
    sweep.add_argument(
        "--agent",
        default=os.environ.get("PR_AUTOPILOT_AGENT", ""),
        help="headless command that repairs a PR, fed a prompt on stdin, e.g. \"claude -p\" "
        "(default: $PR_AUTOPILOT_AGENT)",
    )
    sweep.add_argument("--json", dest="as_json", action="store_true")
    sweep.add_argument(
        "-q", "--quiet", action="store_true", help="no progress on stderr, only the report"
    )

    commands.add_parser("labels", parents=[targets], help="create the autopilot labels")

    onboarding = commands.add_parser(
        "onboard", help="start an agent that onboards the repository in the current directory"
    )
    onboarding.add_argument(
        "--agent",
        help="interactive agent command; the prompt is appended, or replaces a {prompt} argument "
        "(default: print the prompt)",
    )
    args = parser.parse_args(argv)

    if args.command == "onboard":
        return onboard(args.agent)

    if not shutil.which("gh"):
        error("gh is not installed")
        return 2

    # Loaded even for a single repository: its `bots` and `atlantis` are defaults for every policy.
    operator_path = args.config or default_operator_path()
    try:
        operator = (
            Operator.load(operator_path)
            if args.config or args.fleet or os.path.exists(operator_path)
            else Operator()
        )
    except (OSError, ValueError) as err:
        error(f"{operator_path}: {err}")
        return 1
    if args.fleet:
        repos = list(operator.repos)
    elif args.repo:
        repos = [args.repo]
    else:
        repos = [gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]]

    if args.command == "labels":
        for repo in repos:
            create_labels(repo, args.dry_run)
        return 0

    if args.preset and args.preset not in operator.presets:
        error(f"{operator_path}: no preset {args.preset!r}; known: {sorted(operator.presets)}")
        return 1

    global QUIET
    QUIET = args.quiet
    results: list[Result] = []
    for repo in repos:
        try:
            policy = resolve_policy(repo, operator, args.policy, args.preset)
        except PolicyMissing:
            print(
                f"{repo}: no {POLICY_PATH}; pass --preset/--policy, add a [repos] entry, or set default; skipping",
                file=sys.stderr,
            )
            continue
        except (GhError, ValueError, OSError) as err:
            error(f"{repo}: {err}")
            return 1
        try:
            numbers = args.prs or list_bot_prs(repo, policy)
            progress(f"{paint(repo, '1', sys.stderr)}: {len(numbers)} bot pull request(s)")
            results += sweep_repo(repo, numbers, policy, args)
        except GhError as err:
            error(f"{repo}: {err}")
            return 1

    if not args.as_json:
        progress("")
    report(results, args.as_json)
    # Exit code reports whether the sweep ran, never what it decided: a pull request waiting on
    # checks, or correctly escalated, is a successful sweep. Non-zero is reserved for a sweep that
    # could not run — a bad token or an unreachable API — which otherwise reads as "nothing to do".
    return 0


if __name__ == "__main__":
    sys.exit(main())
