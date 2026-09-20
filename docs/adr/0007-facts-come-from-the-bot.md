# Update facts come from the bot, arranged at onboarding

The engine does not infer an upgrade's update class from the PR title, branch name or body prose, and does not ask a
model to read the diff. Renovate already computed `depName`, `currentVersion`, `newVersion` and `updateType` for every
upgrade, so onboarding configures it to emit them as machine-readable JSON in the PR body (`prBodyNotes` / `prFooter`
over `{{#each upgrades}}`), and the engine reads that. Parsing the standard body table remains as a fallback for
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
