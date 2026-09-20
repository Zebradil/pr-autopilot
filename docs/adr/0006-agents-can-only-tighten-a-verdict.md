# An agent can only make a verdict more conservative

Verdicts are ordered from permissive to conservative (`merge`, `gate`, `wait`, `repair`, `escalate`, `hold`). Any
agent output — a review finding, a repair attempt's report — is combined with the code's verdict by taking the more
conservative of the two, never by replacing it. No agent output can turn a hold, a gate or an escalation into a
merge; only a human, by changing labels, can move a verdict in the permissive direction.

This is the invariant that makes it safe to let a language model near an unattended merge pipeline. It bounds the
worst case of a bad model call to "a PR that could have merged did not", which costs a day of staleness, and it
removes prompt injection from release notes as a path to a merge.
