# Update facts come from the bot, arranged at onboarding

The engine does not infer an upgrade's update class from the PR title, branch name or body prose, and does not ask a
model to read the diff. Renovate already computed `depName`, `currentVersion`, `newVersion` and `updateType` for every
upgrade, so onboarding configures it to emit them as machine-readable JSON in the PR body (`prHeader` over
`{{#each upgrades}}`), and the engine reads that. It has to be `prHeader`: Renovate compiles `prBodyNotes` once per
upgrade, where `upgrades` is not in scope and the loop renders an empty list, while `prFooter` would displace
Renovate's own attribution line. Parsing the standard body table remains as a fallback for
repositories whose bot configuration is out of reach and for Dependabot.

Prose parsing was rejected because grouping conventions differ per repository and per team: a single PR may carry
twenty minor upgrades or one major, and a heuristic that works on one team's PRs silently misreads another's.

## Addendum: version arithmetic is allowed, prose is not

Real bots emit four different table shapes, and several omit the update-class column entirely (Renovate's
merge-confidence layout has only Package and Change; Dependabot's single-update pull requests have no table at all).
Where the class is not stated, the engine derives it by comparing the two version strings, which is arithmetic over
data the bot printed, not interpretation of English. Anything the comparison cannot read confidently — a downgrade,
a digest it cannot recognise, a missing side — comes back `unknown`, which policy treats conservatively. A `0.x`
minor bump is classified `major`, because in most ecosystems that is what it means.

## Addendum: the diff is a fact too

Home-grown updaters — a CI job running `nix flake update` or `npm update` — write free prose and no table, so
every step above yields nothing. When the body names no upgrade and every changed file is a known lock file, the
engine classifies the pull request `lockfile`. The file list is data GitHub reports about the change, not
interpretation of English, and "only lock files moved" is Renovate's own definition of lock-file maintenance.
The fallback never overrides a class the bot stated, and one non-lock file in the diff keeps `unknown`.
