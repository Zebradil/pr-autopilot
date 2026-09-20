# The engine owns GitHub state; the agent only edits code

The dispatched agent receives a rendered prompt and may read the repository, change code and push to a branch.
Everything that changes GitHub state — approving, merging, labelling, writing the sticky comment — is done by the
engine after the agent exits. The agent's only output is one JSON line (`outcome`, `summary`, optional
`verdict_floor`), and anything unparseable is treated as `needs-human`.

Failing conservative on unparseable output and keeping every state-changing call in one place is what makes the
"agents can only tighten a verdict" invariant enforceable rather than aspirational, and it lets the agent be swapped
for another harness without re-establishing trust in it.
