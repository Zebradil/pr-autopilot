# pr-autopilot

Unattended triage of automated dependency-update pull requests across repositories with different risk profiles.

Renovate opens a lot of pull requests. Most of them are safe and boring, a few are not, and telling the two apart by
hand does not scale past a handful of repositories. pr-autopilot decides what happens to each one from a policy you
wrote once per repository, merges what policy says to merge, and spends an AI agent only where code genuinely cannot
help: fixing a broken update, or investigating one before handing it to a human.

**Status: the engine works.** Sweeps, verdicts, merging, labels, sticky state and agent dispatch are implemented and
tested against recorded payloads from real Renovate and Dependabot pull requests. Onboarding is a skill an agent
runs; gates and advisory reviews are not built yet. Decisions are recorded in [`docs/adr/`](./docs/adr/), vocabulary
in [`CONTEXT.md`](./CONTEXT.md), operation in [`docs/manual.md`](./docs/manual.md).

## How it works

A deterministic engine does the deciding. For each bot pull request it gathers facts — which packages move, from
which version to which, what the checks say — and looks the result up in the repository's policy table to produce one
verdict: `merge`, `gate`, `wait`, `repair`, `escalate` or `hold`. Then it acts: approve and merge, wait for checks,
dispatch an agent, or escalate to a human with a comment and a label.

Three properties are load-bearing:

- **No model decides a merge.** Verdicts come from a lookup table ([ADR 0001](./docs/adr/0001-code-owns-the-verdict.md)).
- **An agent can only make a verdict more conservative**, never more permissive, and it never touches GitHub state
  itself ([ADR 0006](./docs/adr/0006-agents-can-only-tighten-a-verdict.md),
  [ADR 0011](./docs/adr/0011-the-engine-owns-github-state.md)). The worst case of a bad model call is a pull request
  that stays open a day longer.
- **Reasoning is spent at onboarding, not per pull request.** A capable model studies the repository once, writes the
  policy, and proposes the missing checks that would let the autopilot trust it
  ([ADR 0009](./docs/adr/0009-onboarding-is-where-reasoning-is-spent.md),
  [ADR 0008](./docs/adr/0008-checks-are-the-contract.md)).

The engine is a single Python file with no dependencies beyond `gh`, so the same command runs from a laptop, a cron
entry, and a GitHub Actions workflow — manual, scheduled and reactive runs are the same code path with different
arguments ([ADR 0003](./docs/adr/0003-one-engine-thin-triggers.md)).

## Usage

```bash
pr_autopilot.py sweep --dry-run                  # every open bot PR here: verdicts, touch nothing
pr_autopilot.py sweep 123 456 --repo owner/name  # just these
pr_autopilot.py sweep --fleet ~/.config/pr-autopilot/fleet.toml
pr_autopilot.py labels --repo owner/name         # create the autopilot labels
```

Onboarding a repository is an agent's job, not a subcommand: point your agent at
[`skills/pr-autopilot/SKILL.md`](./skills/pr-autopilot/SKILL.md). It writes the policy, adds the workflows, and
proposes the checks that would let the autopilot merge more.

Policy lives in the repository it governs, at `.github/pr-autopilot.toml`
([template](./templates/pr-autopilot.toml)). See [the manual](./docs/manual.md).

## Roadmap

Done: engine core (facts, verdicts, merging, limits, sticky state, `--dry-run`), workflow templates, repair
dispatch and the agent contract, the onboarding skill.

Next: run it on personal repositories for a while; gates, including the Atlantis plan-is-empty case;
policy-bought advisory reviews; a fixture for pending checks (none existed when the fixtures were captured).
