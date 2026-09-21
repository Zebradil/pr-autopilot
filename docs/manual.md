# Operator manual

Sections marked *(planned)* have no code behind them yet. Everything else is implemented in `pr_autopilot.py` and
covered by `tests/`.

## Verdicts

The engine emits exactly one verdict per pull request, ordered here from permissive to conservative. Agents may move
a verdict down this list, never up; only a human, by changing labels, moves it up.

| Verdict | Meaning | Action taken |
|---|---|---|
| `merge` | Policy allows this update and the checks agree | Approve, merge, delete branch |
| `gate` | A gate must be satisfied first | Trigger the gate, re-evaluate next sweep |
| `wait` | Checks pending, or mergeability unknown | Nothing; retry next sweep |
| `repair` | Checks failed and policy allows an attempted fix | Dispatch the agent |
| `escalate` | A human is needed | Comment with findings, label, stop |
| `hold` | Hands off | Nothing, ever, until the label is removed |

`ignore` covers pull requests that are not from an allowlisted bot. There is deliberately no verdict that closes a
pull request: a closed Renovate pull request comes back, and dropping an update is a human decision.

## Policy file

Lives in the governed repository at `.github/pr-autopilot.toml`.

```toml
enabled = true

# Documentation only — the engine does not read this table. It records what onboarding
# concluded about the repository and why the policy below looks the way it does.
[risk]
release_mode      = "release-please"   # none | manual | auto-from-main | release-please
deploys_from_main = false
test_confidence   = "high"             # none | low | high
criticality       = "normal"           # low | normal | high

# The only thing the engine consults. Values are verdicts.
[policy]
patch       = "merge"
minor       = "merge"
major       = "escalate"
digest      = "merge"
lockfile    = "merge"
review_when = []                       # ["major"] | ["low-confidence"]  (planned)
allow_without_checks = false           # merge even when no checks ran at all

[limits]
max_merges  = 10                       # per sweep, per repository
max_repairs = 3                        # agent dispatches per sweep
attempt_cap = 2                        # repair attempts per PR, then escalate permanently

[repair]
strategy = "bot-branch"                # bot-branch | side-pr
```

A pull request carrying several upgrades takes the most conservative verdict among them.

Setting `enabled = false` stops all action on the repository. Labelling a single pull request `autopilot/hold` stops
all action on that pull request.

## Labels

Labels are the human control surface and the only way to hand control back to the autopilot.

| Label | Set by | Meaning |
|---|---|---|
| `autopilot/hold` | human | Never touch this pull request |
| `autopilot/escalated` | engine | Needs a human; the autopilot has stopped |
| `autopilot/repairing` | engine | An agent holds a lease on this pull request |
| `autopilot/gated` | engine | Waiting on a gate |
| `autopilot/reviewed-ok` | human | Cleared; the next sweep may merge it |

## State

Per-pull-request state lives in a sticky comment carrying a hidden JSON marker — attempt count, escalation reason,
and the timestamped lease that stops a second sweep from dispatching a second agent. There is no local state file:
a sweep from a laptop and a sweep from CI must behave identically.

## Triggers

All three run the same command.

- **Manual**: `pr-autopilot sweep [PR...]`, with `--dry-run` to see verdicts without acting.
- **Scheduled**: a workflow on `schedule`, or a cron entry, sweeping a repository or a fleet.
- **Reactive**: a workflow on `pull_request` events passing the single pull request number.

The fleet — which repositories a scheduled sweep covers — is an explicit list in the operator's own configuration.

## Facts

Update class comes from the bot, not from prose, in this order:

1. The JSON metadata marker onboarding asks Renovate to emit (`<!-- pr-autopilot:upgrades [...] -->`).
2. The update-class column of the bot's own table, whichever of the four known layouts it uses.
3. Version arithmetic over the two versions the bot printed. A `0.x` minor bump counts as major; a downgrade, an
   unreadable version or a missing side counts as `unknown`.

`unknown` has no entry in the default policy table, so it escalates. Nothing reads release notes in steady state.

A pull request with **no checks at all** escalates rather than merging: an empty check list is not a green one. Set
`allow_without_checks = true` for repositories that have no CI and want updates merged anyway.

Bot identity is compared on the bare name, because `gh` spells the same bot `app/renovate`, `renovate[bot]` or
`renovate` depending on which command produced the payload.

## Gates *(planned)*

A gate is anything beyond green checks that must hold before merging — canonically an infrastructure plan that must
show no changes. Preferred shape: onboarding adds a repository check that fails on a non-empty plan, so the engine
only ever reads check status. Fallback for repositories that cannot run plans in CI: the engine parses the plan
comment for a zero-change line. A non-empty plan is an escalation, not a failure.

## The agent *(planned)*

The engine runs whatever command the operator configured (`claude -p`, `opencode run`, `cursor-agent`, …) with a
rendered prompt. The agent reads, edits and pushes code; it prints one JSON line and exits:

```json
{"outcome": "fixed|needs-human|gave-up", "summary": "...", "verdict_floor": "escalate"}
```

Unparseable output is treated as `needs-human`. Labels, comments, approvals and merges are the engine's job.

## Identity

The engine reads `GH_TOKEN` like `gh` does. For an organisation, install a GitHub App and use its installation
token. A fine-grained personal access token is the accepted shortcut for personal repositories. The built-in Actions
token cannot approve pull requests unless the organisation has enabled that, so repositories whose protection
requires an approval need an identity of their own.

The `Zebradil/pr-autopilot` action takes either, and the workflow templates wire both: set the
`PR_AUTOPILOT_CLIENT_ID` repository **variable** and the `PR_AUTOPILOT_APP_PRIVATE_KEY` secret and it mints an
installation token per run, or leave the variable unset and it falls back to the `PR_AUTOPILOT_TOKEN` secret.

### Permissions

Repository permissions, both for a GitHub App and for a fine-grained personal access token:

| Permission      | Access         | Why                                                                          |
| --------------- | -------------- | ---------------------------------------------------------------------------- |
| Metadata        | Read           | Mandatory; GitHub selects it automatically.                                    |
| Contents        | Read and write | Reads `.github/pr-autopilot.toml`; merges, and deletes the merged branch.      |
| Pull requests   | Read and write | Reads bot pull requests, approves, merges, labels, writes the state comment.   |
| Checks          | Read           | The check-run half of the check rollup a verdict is computed from.             |
| Commit statuses | Read           | The legacy-status half of the same rollup.                                     |
| Issues          | Read and write | `pr_autopilot.py labels` creates labels, which are an Issues endpoint.         |
| Workflows       | Write          | Only where bot pull requests touch `.github/workflows/`.                       |

No account permissions, no organisation permissions, no webhook.

Workflows write is the one to think about: a GitHub Actions version bump edits a workflow file, and an App without
that permission cannot merge it (believed correct, verify before relying on it). Repositories whose bots only touch
application dependencies do not need it.

### Creating the App

An App belongs to a personal account or an organisation and is then *installed* on the repositories it may act on.
Create it once and install it on every repository the autopilot governs.

1. **Settings → Developer settings → GitHub Apps → New GitHub App.** For an organisation, the same page under the
   organisation's settings, so the App is owned by the organisation rather than by you.
2. **Name** it something recognisable in the audit log and on the approvals it leaves — `pr-autopilot` if free,
   otherwise `pr-autopilot-<account>`; the name is global. **Homepage URL** is required but unused: this repository's
   URL does.
3. **Webhook: uncheck Active.** The engine polls; nothing calls back.
4. **Repository permissions:** the table above.
5. **Where can this GitHub App be installed:** *Only on this account*.
6. **Create GitHub App**, then note the **Client ID** from the App's General page — `Iv23li…`. GitHub is moving
   identification from the numeric App ID to the Client ID, and `actions/create-github-app-token` marks its
   `app-id` input deprecated in favour of `client-id`; the templates use the Client ID.
7. **Generate a private key** on the same page. The download is a one-time `.pem`; GitHub keeps only the public half.
8. **Install App** in the left-hand menu → your account → *Only select repositories* → the repositories
   the autopilot governs. Installing it is what grants the permissions; the App does nothing until then.

Then, in each governed repository (Settings → Secrets and variables → Actions):

- variable `PR_AUTOPILOT_CLIENT_ID` — the Client ID from step 6.
- secret `PR_AUTOPILOT_APP_PRIVATE_KEY` — the whole `.pem` file, `-----BEGIN` and `-----END` lines included.

Both can live on the organisation instead of on each repository if more than one is governed.

The App also has to be allowed through whatever guards the default branch — see **Branch rules** below.

### The token shortcut

A fine-grained personal access token with the same repository permissions, stored as the `PR_AUTOPILOT_TOKEN`
secret, works for personal repositories and early testing. It expires, it acts as you in the audit log, and it
carries your access rather than the repository's — which is the whole argument for the App
([ADR 0014](./adr/0014-identity-for-unattended-runs.md)).

## Branch rules

Branch protection and rulesets decide whether a `merge` verdict actually merges. The autopilot never changes them:
they are the repository's own safety net, and an agent relaxing a rule to get a pull request through is the failure
mode the whole design exists to prevent. Onboarding reads them and reports what does not fit; changing them is the
operator's click.

Read the effective rules for a branch with:

```bash
gh api repos/{owner}/{repo}/rules/branches/{branch}   # effective rules; [] when the branch is unguarded
gh api repos/{owner}/{repo}/rulesets                  # repository rulesets, if any
```

Prefer those over `repos/{owner}/{repo}/branches/{branch}/protection`, which answers 404 *Branch not protected* for
an unguarded branch and needs admin rights.

What has to hold for the autopilot to merge:

| Rule                                | Effect                                                     |
| ----------------------------------- | ---------------------------------------------------------- |
| Require a pull request, N approvals | Fine — the App's review counts, and the bot is the author.  |
| Require status checks to pass       | Fine, and wanted ([ADR 0008](./adr/0008-checks-are-the-contract.md)). |
| Dismiss stale approvals on push     | Fine — the next sweep re-approves.                          |
| Require linear history              | Fine — the engine merges with `--squash`.                   |
| Restrict who can push or merge      | The App must be an allowed or bypass actor.                 |
| Require review from Code Owners     | Blocks every merge.                                         |
| Require signed commits              | Blocks `repair`.                                            |

The last two are worth spelling out. **Code owner review** cannot be satisfied by the autopilot at all: CODEOWNERS
takes users and teams, not Apps (believed correct, verify before relying on it), so a merge verdict approves and
then sits at `blocked (approved; likely requires a human review)`. Either exempt bot pull requests from the rule or
accept that this repository is a reporting tool. **Signed commits** leave merging intact — GitHub signs the squash
commit — but a repair agent pushes ordinary commits to the bot's branch, which the rule rejects (believed correct,
verify before relying on it).

## Commands

```bash
pr_autopilot.py sweep                          # every bot PR in the current repository
pr_autopilot.py sweep 123 456 --repo o/r       # just these
pr_autopilot.py sweep --fleet fleet.toml       # a whole fleet
pr_autopilot.py sweep --dry-run --json         # verdicts only, machine readable
pr_autopilot.py sweep --config ./policy.toml   # try a policy before committing it
pr_autopilot.py labels --repo o/r              # create the five autopilot labels
```

`--agent-command` (or `PR_AUTOPILOT_AGENT`) is the command that repairs a red pull request; it receives the rendered
prompt on stdin. Without it, a pull request needing repair escalates instead.

## Reporting

A sweep prints a table by default and a machine-readable document with `--json`. When `GITHUB_STEP_SUMMARY` is set,
the table is also written to the Actions job summary.

## Onboarding *(planned)*

Onboarding studies one repository and delivers a pull request containing the policy file, the labels, the bot
metadata template, and — as separate commits that can be dropped — the checks it thinks are missing. It detects what
is detectable (release mechanism, whether main deploys, gates, bot configuration), reasons about test confidence from
what the tests actually assert, and asks the operator for criticality. Its evidence goes in the pull request
description, so the policy is reviewable without re-deriving it.

Run interactively it proposes and waits for approval. Run unattended it opens the pull request directly — a pull
request, never a merge.
