# An agent can only make a verdict more conservative

Verdicts are ordered from permissive to conservative (`merge`, `gate`, `wait`, `repair`, `escalate`, `hold`). Any
agent output — a review finding, a repair attempt's report — is combined with the code's verdict by taking the more
conservative of the two, never by replacing it. No agent output can turn a hold, a gate or an escalation into a
merge; only a human, by changing labels, can move a verdict in the permissive direction.

This is the invariant that makes it safe to let a language model near an unattended merge pipeline. It bounds the
worst case of a bad model call to "a PR that could have merged did not", which costs a day of staleness, and it
removes prompt injection from release notes as a path to a merge.

## Addendum: open to revision

An agent review that can relax a verdict — for example, clearing a major bump after reading its release notes and
the call sites — is a candidate for later, once enough escalations show a recurring shape a model reliably judges
and a manager override cannot express. Until then the invariant holds unchanged. Relaxing it needs its own decision
record, which has to say how prompt injection through release notes stops being a path to a merge, and it cannot
make a hold or an Atlantis gate permissive.
