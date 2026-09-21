# The runtime ships as an action

Governed repositories run the engine through the `Zebradil/pr-autopilot` composite action, not through steps copied
into their workflows. Copied steps meant every change to token minting or engine invocation had to be made in every
onboarded repository. The action is also how the engine arrives: it runs from a checkout of this repository at the
pinned ref, so the engine and its prompts always come from one version, and the one `uses:` line is something
Renovate already knows how to bump.

A reusable workflow was the alternative. Its extra reach — `runs-on`, `permissions`, `concurrency`, the job
condition — is exactly what stays with the governed repository: the runner is the operator's choice, and the
triggers are theirs per ADR 0003. Callers pin an exact release rather than a moving major tag, so an upgrade of the
autopilot is itself a bot pull request the autopilot triages under the repository's own policy.
