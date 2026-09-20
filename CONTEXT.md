# PR Autopilot

Unattended triage of automated dependency-update pull requests (Renovate, Dependabot and similar bots) across many
repositories with different risk profiles. Deterministic code decides what happens to each PR; an AI agent is only
invoked for repair and investigation work that code cannot do.

## Language

### Core

**Autopilot**:
The whole system: policy configuration, the decision engine, its triggers, and the agent it dispatches.
_Avoid_: bot (means the PR author), automation, pipeline

**Sweep**:
One run of the engine over a set of PRs — one PR, a selection, or every open bot PR in a repository.
_Avoid_: batch, job, scan

**Bot PR**:
A pull request opened by an automated dependency updater, not by a person.
_Avoid_: renovate PR (Renovate is one of several sources), auto PR

**Engine**:
The deterministic program that turns PR facts plus policy into a verdict and carries out the resulting action.
No language model participates in a verdict.
_Avoid_: brain, orchestrator, triage script

**Agent**:
An AI coding agent invoked by the engine for a bounded task (fix this PR, investigate and comment). Runs under
whichever harness the operator configured; the engine treats it as a command-line program.
_Avoid_: LLM, assistant, AI

**Verdict**:
The engine's decision for one PR, derived from policy. Final — the agent never overrides it.
_Avoid_: decision, status, classification

**Policy**:
The configuration that maps PR facts and repository risk axes onto verdicts.
_Avoid_: rules file, settings, ruleset

**Risk axis**:
One named, orthogonal property of a repository that policy reads (for example: how releases happen, whether main
deploys to production, how much test confidence exists). Deliberately not collapsed into a single risk level.
_Avoid_: risk level, risk score, tier

**Onboarding**:
The one-time act of giving a repository a policy: interviewing the operator or inferring answers, writing the
configuration, and creating any labels the autopilot needs.
_Avoid_: init, setup, bootstrap

**Operator**:
The human who owns the autopilot's configuration and receives its escalations.
_Avoid_: user, maintainer, owner

### PR facts

**Upgrade**:
One package moving from one version to another. A bot PR carries one or more.
_Avoid_: bump, dependency change

**Update class**:
The kind of version move an upgrade makes — major, minor, patch, digest, pin, lock-file maintenance. Taken from
what the bot itself computed, never guessed from prose.
_Avoid_: severity, update type (except when quoting Renovate's own field name), semver level

**Group PR**:
A bot PR carrying more than one upgrade. Its verdict is the most conservative verdict among its upgrades.
_Avoid_: batch PR, grouped update

**Gate**:
A condition beyond "checks are green" that must hold before a merge — canonically an infrastructure plan that must
show no changes. Preferably expressed as a repository check so the engine only ever reads check status.
_Avoid_: approval, guard, precondition

### Acting

**Review**:
An advisory agent pass over a PR, bought deliberately by policy for repositories where checks cannot carry the
weight. A review can only make a verdict more conservative.
_Avoid_: analysis, assessment, AI review (all reviews here are agent reviews)

**Escalation**:
Handing a PR to a human: a comment stating what the autopilot found and what it needs, plus a label. The autopilot
takes no further action on that PR until a human changes the labels.
_Avoid_: alert, ticket, handoff

**Lease**:
A timestamped claim on a PR recorded in its sticky comment, so a second sweep does not dispatch a second agent onto
a PR already being repaired.
_Avoid_: lock, mutex, claim

**Fleet**:
The set of repositories an operator's autopilot sweeps, listed explicitly in operator configuration.
_Avoid_: org, scope, targets
