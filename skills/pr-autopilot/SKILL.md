---
name: pr-autopilot
description: Onboard a repository to pr-autopilot, or investigate a pull request the autopilot escalated. Use when the user asks to set up automated dependency-PR merging, write a pr-autopilot policy, or find out why a bot PR was escalated.
---

# pr-autopilot

The autopilot's steady state is a deterministic engine (`pr_autopilot.py`). You are here for the two jobs that
need judgement: **onboarding** a repository, and **investigating** an escalation. You never merge a pull request
yourself — the engine does that, and only when policy says so.

## Onboarding a repository

Goal: one pull request that leaves the repository governed by a policy a human can read and believe.

### 1. Gather evidence

Deterministic, do these first:

- Release mechanism: look for `release-please-config.json` / `.release-please-manifest.json`, semantic-release
  config, `goreleaser`, tag-triggered workflows, a `CHANGELOG.md` that is clearly generated.
- Does the default branch reach production? Read every workflow triggered by `push` to the default branch and
  follow it to deploy steps, `environment:` blocks, cloud credentials.
- Gates: `atlantis.yaml`, `terraform`/`terragrunt` directories, plan jobs, and the repository's required checks
  (`gh api repos/{owner}/{repo}/branches/{branch}/protection` — may 404 without admin rights).
- Bot configuration in reach: `renovate.json`, `.github/renovate.json5`, `.github/dependabot.yml`.
- Existing checks: what the CI actually runs, and on which events.

Then judge, with evidence you can quote:

- `test_confidence`: not "are there tests" but "would these tests fail if a dependency changed behaviour?"
  A repository whose CI only lints has `none`, whatever its coverage badge says.
- Missing checks worth adding: the ones that would let the autopilot merge more with less risk.

Ask the operator for `criticality` — it is never inferrable. Also ask whether anything about releases or
deployment is not visible in the repository.

### 2. Propose

Show the operator the evidence, the axes you concluded, and the policy table you would write. Wait for
corrections. Running unattended, skip the waiting and put the same content in the pull request description.

Default shape: patch/minor/digest/pin/lockfile merge, major escalates. Tighten when
`deploys_from_main = true` or `test_confidence` is low — in the extreme, everything escalates and the
autopilot is only a reporting tool until the checks improve. Loosen only when the checks genuinely justify it.

### 3. Deliver one pull request

- `.github/pr-autopilot.toml` — from `templates/pr-autopilot.toml`, with the `[risk]` block filled in as the
  record of what you concluded.
- `.github/workflows/pr-autopilot-sweep.yml` (and the reactive one if the operator wants minute-latency merges).
- Renovate metadata, if `renovate.json` is in reach — this is what makes update classes reliable (ADR 0007):

  ```json
  "prBodyNotes": [
    "<!-- pr-autopilot:upgrades [{{#each upgrades}}{\"depName\":\"{{{depName}}}\",\"updateType\":\"{{{updateType}}}\",\"currentValue\":\"{{{currentValue}}}\",\"newValue\":\"{{{newValue}}}\"}{{#unless @last}},{{/unless}}{{/each}}] -->"
  ]
  ```

- Check improvements, each as its own commit with a message saying what it protects against, so the operator can
  drop them individually.

Labels are not part of the pull request — create them directly: `pr_autopilot.py labels --repo <owner/repo>`.

Finally, tell the operator to add the repository to their fleet file and to set `PR_AUTOPILOT_TOKEN`; the
autopilot does nothing until both exist.

### 4. Verify

`pr_autopilot.py sweep --repo <owner/repo> --config .github/pr-autopilot.toml --dry-run` and read the verdict
table with the operator. If a pull request you would have merged by hand shows `escalate`, the policy is wrong or
the metadata is missing — fix it now, not after the first surprise.

## Investigating an escalation

You are given a pull request carrying `autopilot/escalated`. Read the sticky comment for why, then:

1. Establish what actually breaks: failing checks first, the upgrade's own release notes second.
2. Report to the human in the pull request: what the update changes, what breaks, what a fix would involve, and
   whether it is worth doing now.
3. Do not remove `autopilot/escalated` and do not add `autopilot/reviewed-ok`. Those labels are a human's way of
   handing control back to the autopilot; an agent adding them would turn an escalation into a merge, which is
   exactly what the design forbids (ADR 0006).

## Never

- Merge, approve, or label a pull request on the autopilot's behalf.
- Edit a policy file to make a specific stuck pull request merge. Policy describes a repository, not a pull request.
- Weaken or disable a check to turn a red pull request green.
