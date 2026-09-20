# Unattended runs act as a GitHub App, not as a person

The documented path for an organisation is a GitHub App installed per repository, whose installation token the
engine uses: the actor in the audit log is the autopilot rather than a human, permissions are scoped per repository,
and no person's credential lives in CI. A fine-grained personal access token is the accepted shortcut for personal
repositories and early testing.

Note the constraint that shapes this: the built-in `GITHUB_TOKEN` cannot approve pull requests unless the
organisation enables Actions to create and approve pull requests (believed correct, verify before relying on it), so
repositories whose protection requires an approval need a separate identity anyway. The engine itself only reads
`GH_TOKEN` and never knows which kind it holds.
