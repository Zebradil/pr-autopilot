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
import shutil
import string
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone

POLICY_PATH = ".github/pr-autopilot.toml"
STATE_MARKER = "pr-autopilot:state"
UPGRADES_MARKER = "pr-autopilot:upgrades"
DEFAULT_BOTS = ("renovate", "dependabot")

PR_FIELDS = (
    "number,title,author,body,labels,state,isDraft,mergeable,mergeStateStatus,"
    "statusCheckRollup,headRefName,url,comments"
)

FAILING_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED"}
FAILING_STATES = {"FAILURE", "ERROR"}
PENDING_STATES = {"PENDING", "EXPECTED"}

# Update classes a policy table can name. "unknown" is what we emit when the bot did not tell us
# and the body could not be parsed; it is deliberately not mergeable by default.
CLASSES = ("patch", "minor", "major", "digest", "pin", "lockfile", "unknown")


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


def parse_upgrades(body: str) -> list[Upgrade]:
    """Upgrades from the bot's own metadata, falling back to its PR body table.

    ADR 0007: we never infer an update class from prose. The JSON marker is what onboarding asks
    Renovate to emit; the table is the fallback for bots we cannot configure. Anything else is
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
    return parse_body_table(body) or parse_dependabot(body)


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
    if "sha256:" in current or "sha256:" in new or (HEX.match(current) and HEX.match(new)):
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
    """"`1.2.3` → `1.3.0`" into its two sides; anything without an arrow yields nothing."""
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
        cells = [PARENTHETICAL.sub("", TABLE_LINK.sub(r"\1", c).strip()) for c in line.strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if {"change", "to", "update"} & set(lowered) and ("package" in lowered or "update" in lowered):
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
        name = cell.get("package") or ("(lock files)" if update_class == "lockfile" else "")
        if not name:
            continue
        rows.append(Upgrade(name=name, update_class=update_class,
                            current=current.strip("` "), new=new.strip("` ")))
    return rows


def parse_dependabot(body: str) -> list[Upgrade]:
    """Dependabot's single-update sentence: "Bumps [name](url) from X to Y."."""
    m = DEPENDABOT_SINGLE.search(body or "")
    if not m:
        return []
    name = m.group("linked") or m.group("plain")
    current, new = m.group("current").rstrip("."), m.group("new").rstrip(".")
    return [Upgrade(name=PARENTHETICAL.sub("", name), update_class=classify(current, new),
                    current=current, new=new)]


def parse_state_comment(comments) -> dict:
    for c in reversed(comments or []):
        m = re.search(rf"<!--\s*{STATE_MARKER}\s*(\{{.*?\}})\s*-->", c.get("body", ""), re.S)
        if m:
            try:
                state = json.loads(m.group(1))
                state["_comment_id"] = c.get("id")
                return state
            except json.JSONDecodeError:
                continue
    return {}


def facts_from_json(repo: str, pr: dict) -> Facts:
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
        upgrades=tuple(parse_upgrades(pr.get("body", ""))),
        checks_failing=tuple(failing),
        checks_pending=tuple(pending),
        checks_total=len(pr.get("statusCheckRollup") or []),
        state_comment=parse_state_comment(pr.get("comments")),
    )


# --- policy -----------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Policy:
    enabled: bool = True
    bots: tuple[str, ...] = DEFAULT_BOTS
    table: dict = dataclasses.field(default_factory=dict)
    max_merges: int = 10
    max_repairs: int = 3
    attempt_cap: int = 2
    allow_without_checks: bool = False
    repair_strategy: str = "bot-branch"
    lease_minutes: int = 30

    @staticmethod
    def from_toml(raw: bytes, default_bots: tuple[str, ...] = DEFAULT_BOTS) -> "Policy":
        return Policy.from_dict(tomllib.loads(raw.decode()), default_bots)

    @staticmethod
    def from_dict(d: dict, default_bots: tuple[str, ...] = DEFAULT_BOTS) -> "Policy":
        table = {k: v for k, v in (d.get("policy") or {}).items() if k in CLASSES}
        extras = {"review_when", "allow_without_checks"}
        unknown = {k for k in (d.get("policy") or {}) if k not in CLASSES and k not in extras}
        if unknown:
            raise ValueError(f"unknown update classes in [policy]: {sorted(unknown)}")
        bad = {v for v in table.values() if v not in VERDICTS}
        if bad:
            raise ValueError(f"unknown verdicts in [policy]: {sorted(bad)}")
        limits = d.get("limits") or {}
        return Policy(
            enabled=d.get("enabled", True),
            bots=tuple(d.get("bots") or default_bots),
            table=table,
            max_merges=limits.get("max_merges", 10),
            max_repairs=limits.get("max_repairs", 3),
            attempt_cap=limits.get("attempt_cap", 2),
            allow_without_checks=(d.get("policy") or {}).get("allow_without_checks", False),
            repair_strategy=(d.get("repair") or {}).get("strategy", "bot-branch"),
            lease_minutes=limits.get("lease_minutes", 30),
        )

    def for_class(self, cls: str) -> str:
        return self.table.get(cls, "escalate")


# --- the decision -----------------------------------------------------------------------------


def decide(facts: Facts, policy: Policy, now: datetime | None = None) -> tuple[str, str]:
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
        return worst(policy_verdict, "escalate"), f"{facts.attempts} repair attempts, cap reached"

    if facts.mergeable == "CONFLICTING":
        return worst(policy_verdict, "repair"), "conflicting"
    if facts.checks_failing:
        return worst(policy_verdict, "repair"), "failing: " + ", ".join(facts.checks_failing[:3])
    if facts.checks_pending:
        return worst(policy_verdict, "wait"), "checks pending"
    if facts.mergeable == "UNKNOWN":
        return worst(policy_verdict, "wait"), "mergeability unknown"
    if not facts.checks_total and not policy.allow_without_checks:
        # An empty check list is not a green one. A repository with no CI has to say so on purpose.
        return worst(policy_verdict, "escalate"), "no checks ran"
    return policy_verdict, why


def leased(facts: Facts, policy: Policy, now: datetime) -> bool:
    until = facts.state_comment.get("lease_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except ValueError:
        return False


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
    presets: dict = dataclasses.field(default_factory=dict)
    repos: dict = dataclasses.field(default_factory=dict)  # "owner/name" -> {"preset": ...} | {"policy": ...} | {}
    base_dir: str = "."

    @staticmethod
    def load(path: str) -> "Operator":
        with open(path, "rb") as fh:
            d = tomllib.load(fh)
        repos = d.get("repos") or {}
        if isinstance(repos, list):
            repos = {name: {} for name in repos}
        for name, entry in repos.items():
            if not isinstance(entry, dict) or set(entry) - {"preset", "policy"} or len(entry) > 1:
                raise ValueError(f"repos.{name!r}: want a table with at most one of preset, policy; got {entry!r}")
        return Operator(
            bots=tuple(d.get("bots") or DEFAULT_BOTS),
            presets=d.get("presets") or {},
            repos=repos,
            base_dir=os.path.dirname(os.path.abspath(path)),
        )

    def preset(self, name: str) -> Policy:
        if name not in self.presets:
            raise ValueError(f"unknown preset {name!r}; known: {sorted(self.presets)}")
        return Policy.from_dict(self.presets[name], self.bots)

    def policy_file(self, path: str) -> Policy:
        with open(os.path.join(self.base_dir, path), "rb") as fh:
            return Policy.from_toml(fh.read(), self.bots)


def default_operator_path() -> str:
    env = os.environ.get("PR_AUTOPILOT_CONFIG")
    if env:
        return env
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "pr-autopilot", "config.toml")


def resolve_policy(repo: str, operator: Operator, config: str | None, preset: str | None) -> Policy:
    """Explicit CLI choice first, then the operator's entry for the repo, then the in-repo file."""
    if config:
        with open(config, "rb") as fh:
            return Policy.from_toml(fh.read(), operator.bots)
    if preset:
        return operator.preset(preset)
    entry = operator.repos.get(repo) or {}
    if "policy" in entry:
        return operator.policy_file(entry["policy"])
    if "preset" in entry:
        return operator.preset(entry["preset"])
    return fetch_policy(repo, operator.bots)


def fetch_policy(repo: str, default_bots: tuple[str, ...] = DEFAULT_BOTS) -> Policy:
    try:
        blob = gh_json("api", f"repos/{repo}/contents/{POLICY_PATH}")
    except GhError as err:
        # Anything else — a bad or missing token, a network fault, a 5xx — is a broken sweep,
        # not an ungoverned repository, and must not be swallowed as one.
        if "404" in str(err) or "Not Found" in str(err):
            raise PolicyMissing(str(err)) from err
        raise
    return Policy.from_toml(base64.b64decode(blob["content"]), default_bots)


def fetch_pr(repo: str, number: int) -> Facts:
    pr = gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    if pr.get("mergeable") == "UNKNOWN":
        # GitHub computes mergeability lazily: the first request triggers it, the second sees it.
        time.sleep(3)
        pr = gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)
    return facts_from_json(repo, pr)


def list_bot_prs(repo: str, policy: Policy) -> list[int]:
    prs = gh_json("pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
                  "--json", "number,author")
    allowed = {bot_name(b) for b in policy.bots}
    return [p["number"] for p in prs if bot_name((p.get("author") or {}).get("login", "")) in allowed]


# --- actions ----------------------------------------------------------------------------------


def approve_and_merge(facts: Facts) -> tuple[bool, str]:
    """Approve, then merge with the fallbacks a protected repository needs."""
    gh("pr", "review", str(facts.number), "--repo", facts.repo, "--approve", check=False)
    out = gh("pr", "merge", str(facts.number), "--repo", facts.repo,
             "--squash", "--auto", "--delete-branch", check=False)
    if re.search(r"auto.?merge", out, re.I):
        out = gh("pr", "merge", str(facts.number), "--repo", facts.repo,
                 "--squash", "--delete-branch", check=False)
    after = gh_json("pr", "view", str(facts.number), "--repo", facts.repo, "--json", "state,mergeStateStatus")
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
    """Upsert the sticky comment that carries this PR's autopilot state."""
    state = {k: v for k, v in facts.state_comment.items() if not k.startswith("_")}
    state.update(updates)
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = (
        "**pr-autopilot**\n\n"
        f"{state.get('note', '')}\n\n"
        f"<!-- {STATE_MARKER} {json.dumps(state, sort_keys=True)} -->"
    )
    if dry_run:
        return
    comment_id = facts.state_comment.get("_comment_id")
    if comment_id:
        numeric = str(comment_id).rsplit("_", 1)[-1] if str(comment_id).startswith("IC_") else comment_id
        try:
            gh("api", "-X", "PATCH", f"repos/{facts.repo}/issues/comments/{numeric}",
               "-f", f"body={body}")
            return
        except GhError:
            pass  # comment gone or id not numeric — fall through to a new one
    gh("pr", "comment", str(facts.number), "--repo", facts.repo, "--body", body, check=False)


def dispatch_repair(facts: Facts, policy: Policy, reason: str, agent_command: str, dry_run: bool) -> tuple[str, str]:
    """Run the configured agent on one PR. ADR 0011: it edits code, we own GitHub state."""
    if not agent_command:
        return "escalate", "repair needed but no agent configured"
    lease = (datetime.now(timezone.utc) + timedelta(minutes=policy.lease_minutes)).isoformat(timespec="seconds")
    write_state(facts, dry_run, attempts=facts.attempts + 1, lease_until=lease,
                note=f"Repairing: {reason}")
    prompt = render_prompt(facts, reason, policy)
    if dry_run:
        return "repair", "would dispatch agent"
    proc = subprocess.run(agent_command, shell=True, input=prompt, capture_output=True, text=True)
    result = parse_agent_result(proc.stdout)
    write_state(facts, dry_run, lease_until=None, note=result["summary"])
    if result["outcome"] == "fixed":
        return "wait", f"agent fixed: {result['summary']}"
    return worst("escalate", result.get("verdict_floor", "escalate")), result["summary"]


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
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "repair.md")
    with open(template_path) as fh:
        template = fh.read()
    upgrades = "\n".join(f"- {u.name}: {u.current} -> {u.new} ({u.update_class})" for u in facts.upgrades)
    # Template, not format(): the prompt contains a JSON example full of braces.
    return string.Template(template).safe_substitute(
        repo=facts.repo, number=facts.number, title=facts.title, url=facts.url,
        head_ref=facts.head_ref, reason=reason, upgrades=upgrades or "- (not parsed)",
        failing=", ".join(facts.checks_failing) or "(none)",
        strategy=policy.repair_strategy,
    )


def escalate(facts: Facts, reason: str, dry_run: bool) -> None:
    write_state(facts, dry_run, note=f"Needs a human: {reason}\n\n"
                f"Remove `{Label.ESCALATED}` and add `{Label.REVIEWED_OK}` to let the autopilot merge it.")


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
    for number in numbers:
        facts = fetch_pr(repo, number)
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
                verdict, outcome = dispatch_repair(facts, policy, reason, args.agent_command, args.dry_run)
        elif verdict == "escalate" and Label.ESCALATED not in facts.labels:
            escalate(facts, reason, args.dry_run)
            outcome = "escalated"

        sync_labels(facts, verdict, args.dry_run)
        results.append(Result(repo, number, facts.title, verdict, reason, outcome))
    return results


def report(results: list[Result], as_json: bool) -> None:
    if as_json:
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
        return
    if not results:
        print("no bot pull requests")
        return
    width = max(len(r.title) for r in results)
    lines = [f"{'PR':>16}  {'VERDICT':<9} {'TITLE':<{width}}  REASON / OUTCOME"]
    for r in results:
        lines.append(
            f"{r.repo.split('/')[-1] + '#' + str(r.number):>16}  {r.verdict:<9} "
            f"{r.title:<{width}}  {r.reason} -> {r.outcome}"
        )
    out = "\n".join(lines)
    print(out)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### pr-autopilot\n\n```\n{out}\n```\n")


def create_labels(repo: str, dry_run: bool) -> None:
    for name, (colour, description) in LABEL_COLOURS.items():
        if dry_run:
            print(f"would create {name}")
            continue
        gh("label", "create", name, "--repo", repo, "--color", colour,
           "--description", description, "--force", check=False)
        print(f"{name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pr-autopilot", description=__doc__)
    parser.add_argument("command", choices=["sweep", "labels"])
    parser.add_argument("prs", nargs="*", type=int, help="pull request numbers (default: all bot PRs)")
    parser.add_argument("--repo", help="owner/name (default: the repository in the current directory)")
    parser.add_argument("--fleet", action="store_true",
                        help="sweep every repository in the operator file "
                             "($PR_AUTOPILOT_CONFIG or ~/.config/pr-autopilot/config.toml)")
    parser.add_argument("--config", help="policy file to use instead of the one in the repository")
    parser.add_argument("--preset", help="named preset from the operator file to use as the policy")
    parser.add_argument("--agent-command", default=os.environ.get("PR_AUTOPILOT_AGENT", ""),
                        help="command that repairs a PR, fed a prompt on stdin")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    if not shutil.which("gh"):
        print("pr-autopilot: gh is not installed", file=sys.stderr)
        return 2

    # Loaded even for a single repository: its `bots` is the default allowlist for every policy.
    operator_path = default_operator_path()
    try:
        operator = Operator.load(operator_path) if args.fleet or os.path.exists(operator_path) else Operator()
    except (OSError, ValueError) as err:
        print(f"pr-autopilot: {operator_path}: {err}", file=sys.stderr)
        return 1
    if args.preset and args.preset not in operator.presets:
        print(f"pr-autopilot: {operator_path}: no preset {args.preset!r}; known: {sorted(operator.presets)}",
              file=sys.stderr)
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

    results: list[Result] = []
    for repo in repos:
        try:
            policy = resolve_policy(repo, operator, args.config, args.preset)
        except PolicyMissing:
            print(f"{repo}: no {POLICY_PATH}; pass --preset/--config or add a [repos] entry; skipping",
                  file=sys.stderr)
            continue
        except (GhError, ValueError, OSError) as err:
            print(f"pr-autopilot: {repo}: {err}", file=sys.stderr)
            return 1
        try:
            numbers = args.prs or list_bot_prs(repo, policy)
            results += sweep_repo(repo, numbers, policy, args)
        except GhError as err:
            print(f"pr-autopilot: {repo}: {err}", file=sys.stderr)
            return 1

    report(results, args.as_json)
    # Exit code reports whether the sweep ran, never what it decided: a pull request waiting on
    # checks, or correctly escalated, is a successful sweep. Non-zero is reserved for a sweep that
    # could not run — a bad token or an unreachable API — which otherwise reads as "nothing to do".
    return 0


if __name__ == "__main__":
    sys.exit(main())
