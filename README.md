# pr-autopilot

Unattended triage of Renovate and Dependabot pull requests, driven by a per-repository policy.

A deterministic engine reads each bot pull request (which packages move, how far, what the checks say), looks the
result up in the repository's policy table, and acts on the verdict:

| Verdict    | Action                                                  |
| ---------- | ------------------------------------------------------- |
| `merge`    | approve, merge, delete branch                           |
| `gate`     | trigger a gate, re-evaluate next sweep *(planned)*      |
| `wait`     | nothing; checks pending, retry next sweep               |
| `repair`   | dispatch an agent to fix the failing update             |
| `escalate` | comment with findings, label, stop; a human takes over  |
| `hold`     | nothing, ever, until the label is removed               |

## Design guarantees

- **No model decides a merge.** Verdicts come from a lookup table ([ADR 0001](./docs/adr/0001-code-owns-the-verdict.md)).
- **Agents only tighten.** An agent can make a verdict more conservative, never more permissive, and never touches
  GitHub state ([ADR 0006](./docs/adr/0006-agents-can-only-tighten-a-verdict.md),
  [ADR 0011](./docs/adr/0011-the-engine-owns-github-state.md)).
- **Reasoning is spent once, at onboarding.** An LLM studies the repository, writes the policy, and proposes the
  checks that would let the autopilot merge more ([ADR 0009](./docs/adr/0009-onboarding-is-where-reasoning-is-spent.md)).
- **One engine, thin triggers.** A single Python file needing only `gh`; laptop, cron and GitHub Actions run the same
  code ([ADR 0003](./docs/adr/0003-one-engine-thin-triggers.md)).

## Onboard a repository with an LLM

From a checkout of the repository to onboard:

```bash
nix run github:Zebradil/pr-autopilot -- onboard                              # print the prompt, run no agent
nix run github:Zebradil/pr-autopilot -- onboard --agent "claude --model sonnet"
nix run github:Zebradil/pr-autopilot -- onboard --agent "opencode --prompt {prompt}"
```

`onboard --agent` starts the agent interactively with the onboarding instructions as its first prompt, appended to the
command or put in place of `{prompt}`. The agent asks for the repository's criticality, then opens a pull request
with the policy, the workflows and suggested check improvements. It leaves two steps to you: creating the labels and
setting up the identity ([manual, "Identity"](./docs/manual.md#identity)).

To investigate an escalated pull request, point an agent at the skill: "Investigate why PR #123 was escalated:
https://github.com/Zebradil/pr-autopilot/blob/main/skills/pr-autopilot/SKILL.md".

## Manual usage

```bash
pr_autopilot.py sweep --dry-run                  # verdicts for every open bot PR here, act on nothing
pr_autopilot.py sweep 123 456 --repo owner/name  # only these PRs
pr_autopilot.py sweep --policy ./policy.toml     # try a policy before committing it
pr_autopilot.py sweep --preset infra --repo o/n  # try a preset from ~/.config/pr-autopilot/config.toml
pr_autopilot.py sweep --config ./ops.toml --fleet # operator file other than the default
pr_autopilot.py sweep --fleet                    # every repository in that file
pr_autopilot.py labels --repo owner/name         # create the autopilot labels
```

In CI, use the action ([`action.yml`](./action.yml)); the [workflow templates](./templates/workflows/) call it.

## Documentation

- [`docs/manual.md`](./docs/manual.md) — policy file, labels, triggers, identity, branch rules
- [`CONTEXT.md`](./CONTEXT.md) — vocabulary
- [`docs/adr/`](./docs/adr/) — design decisions

## Status

Working: facts, verdicts, merging, limits, sticky state, `--dry-run`, the GitHub Action, repair dispatch, the
onboarding skill. Tested against recorded payloads from real Renovate and Dependabot pull requests.

Planned: gates (including Atlantis empty-plan), policy-bought advisory reviews.
