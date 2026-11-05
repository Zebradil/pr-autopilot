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
- Gates: `atlantis.yaml`, `terraform`/`terragrunt` directories, plan jobs.
- Branch rules on the default branch: `gh api repos/{owner}/{repo}/rules/branches/{branch}` for the effective
  rules (`[]` means unguarded) and `gh api repos/{owner}/{repo}/rulesets`. Check them against the table in
  `docs/manual.md`, "Branch rules" — a required code-owner review, or a push restriction the App is not listed in,
  means `merge` verdicts will stall no matter how good the policy is.
- Bot configuration in reach: `renovate.json`, `.github/renovate.json5`, `.github/dependabot.yml`.
- Existing checks: what the CI actually runs, and on which events.
- Vacuous green: for each class of file the bot updates (list the files of the open bot PRs with
  `gh pr view --json files`), name the check that does real work when only those files change. A check that is
  green because its path filter, discovery step or `when_modified` glob selected nothing is not a check. Typical
  miss: a toolchain pin (`.terraform-version`, `.mise.toml`, the `terraform_version` fields of `atlantis.yaml`)
  that no project directory contains, so neither CI nor the plan bot runs anything.

Then judge, with evidence you can quote:

- `test_confidence`: not "are there tests" but "would these tests fail if a dependency changed behaviour?"
  A repository whose CI only lints has `none`, whatever its coverage badge says.
- Missing checks worth adding: the ones that would let the autopilot merge more with less risk.

Ask the operator for `criticality` — it is never inferrable. Also ask whether anything about releases or
deployment is not visible in the repository.

### 2. Propose

Show the operator the evidence, the axes you concluded, and the policy table you would write. Wait for
corrections. Running unattended, skip the waiting and put the same content in the pull request description.

Ask about the labels in the same message, not after the pull request: once it is open, the operator's attention
moves to merging it, and a question asked then is easy to miss. `pr_autopilot.py labels --repo <owner/repo>` writes
the five labels straight to GitHub, outside the pull request the operator will review, so show the command and the
labels it creates and wait for an explicit go-ahead. On a yes, run it before opening the pull request. The first
real sweep needs the labels: the engine does not check its label writes, so without `autopilot/escalated` an
escalation fails silently and does not stick — a later sweep re-derives the verdict as if no human had been asked.
Never create labels unasked, and never run it against a repository the operator has not named. Running unattended,
there is no go-ahead to get, so the command becomes the first of the operator's steps below.

Default shape: patch/minor/digest/pin/lockfile merge, major escalates. Tighten when
`deploys_from_main = true` or `test_confidence` is low — in the extreme, everything escalates and the
autopilot is only a reporting tool until the checks improve. Loosen only when the checks genuinely justify it.

When the checks justify a looser rule for one ecosystem only, write a manager override rather than loosening the
whole table — e.g. `[policy.github-actions]` with `major = "merge"` when every workflow that uses the actions runs
on pull requests. Names are Renovate's manager names (`github-actions`, `npm`, `cargo`, …) and Dependabot's
ecosystem names (`github_actions`, `npm_and_yarn`, …); write both if both bots run.

### 3. Deliver one pull request

- `.github/pr-autopilot.toml` — from `templates/pr-autopilot.toml`, with the `[risk]` block filled in as the
  record of what you concluded.
- `.github/workflows/pr-autopilot-sweep.yml` (and the reactive one if the operator wants minute-latency merges),
  copied from `templates/workflows/`. They are thin callers of the `Zebradil/pr-autopilot` action, pinned to a
  release; Renovate bumps the pin like any other action. Drop the `# x-release-please-version` marker, which only
  means something in this repository. `runs-on` is the operator's choice; the templates default to `ubuntu-slim`.
- Renovate metadata, if `renovate.json` is in reach — this is what makes update classes reliable (ADR 0007):

  ```json
  "prHeader": "<!-- pr-autopilot:upgrades [{{#each upgrades}}{\"depName\":\"{{{depName}}}\",\"updateType\":\"{{{updateType}}}\",\"currentValue\":\"{{{currentValue}}}\",\"newValue\":\"{{{newValue}}}\",\"manager\":\"{{{manager}}}\"}{{#unless @last}},{{/unless}}{{/each}}] -->"
  ```

  `prHeader`, not `prBodyNotes`: notes are compiled once per upgrade with only that upgrade in scope, so the
  `upgrades` loop renders an empty list. `prFooter` has the right scope but replaces Renovate's attribution line.

  In the same file, the autopilot's own commit email in `gitIgnoredAuthors`. When a merge leaves another pull
  request behind the base, the sweep merges the base into that branch. Renovate treats any branch with another
  author's commit as modified and stops rebasing it, so without this entry that pull request would get no more
  updates from Renovate:

  ```json
  "gitIgnoredAuthors": ["<user-id>+<slug>[bot]@users.noreply.github.com"]
  ```

  For a GitHub App, `<slug>` is the App's slug and `<user-id>` comes from
  `gh api 'users/<slug>[bot]' --jq .id`. This is the App's bot account ID, not its App ID. If the prompt names the
  operator's App, fill both in. If not, leave the placeholder and add it to the operator's steps. For the token
  shortcut, use the token owner's commit email instead.

- Check improvements, each as its own commit with a message saying what it protects against, so the operator can
  drop them individually.

Branch rules live in repository settings, not in the diff, so they go in the pull request description as a short
list: the rule, why it blocks the autopilot, and the smallest change that would unblock it. Suggest, never apply —
editing a repository's protection to make pull requests merge is the same move as weakening a check, and it is not
yours to make even with an admin token. If the rules are already compatible, say so in one line; the operator
should not have to re-derive it.

Finally, the identity: the delivered workflow sweeps this repository on its own schedule, so the one thing left
outside the pull request is the credential it runs as, without which the autopilot does nothing. Point the operator
at a GitHub App installed on the repository — the documented path (ADR 0014): the audit log shows the autopilot
rather than a person, and permissions are scoped per repository. They set the `PR_AUTOPILOT_CLIENT_ID` repository
variable and the `PR_AUTOPILOT_APP_PRIVATE_KEY` secret, and the workflow mints an installation token per run. A
fine-grained personal access token in `PR_AUTOPILOT_TOKEN` is the fallback the templates also accept — a shortcut
for a personal repository or early testing, not the recommendation. Either identity needs approve and merge rights,
because `GITHUB_TOKEN` cannot approve. The steps for creating the App and the permissions it needs are in
`docs/manual.md`, "Identity" — send the operator there rather than reciting them.

If the prompt names an octo-sts domain, there is no credential to set: wire the delivered workflows to it as
`docs/manual.md`, "octo-sts", shows — `id-token: write`, the `octo-sts/action` step, its token as the action's only
identity input — and drop the App-key and token inputs. Check that the repository's default branch matches the
`workflow_ref` in that section's policy; if not, tell the operator the policy needs widening.

The pull request description ends with the steps left to the operator before the first sweep can act, in order, and
your closing message after opening the pull request repeats them. Link the App in the step that installs it: its
installation page, `https://github.com/apps/<slug>/installations/new`, when the prompt names the operator's App;
otherwise `docs/manual.md`, "Creating the App". Include only the steps this repository still needs:

- If you did not create the labels during the proposal, create them before merging:
  `pr_autopilot.py labels --repo <owner/repo>`.
- If the repository's owner is not the account that owns the App, the App must first be made installable on any
  account (the App's settings, "Make public").
- Install the App on the repository; with octo-sts, on the owner's `.github` repository too.
- With octo-sts and a new owner, add that owner to the service's App file and add the trust policy to the owner's
  `.github` repository (`docs/manual.md`, "octo-sts", steps 2 and 3).
- After merging, run the sweep once by hand with `dry_run` to prove the identity works.

### 4. Verify

`pr_autopilot.py sweep --repo <owner/repo> --policy .github/pr-autopilot.toml --dry-run` and read the verdict
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
- Change branch protection or a ruleset. Report what blocks the autopilot; the operator decides.
