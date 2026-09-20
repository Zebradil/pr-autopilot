# The engine is single-file Python with no runtime dependencies

The engine is one Python 3 file using only the standard library, shelling out to `gh` for GitHub access. Bash with
`jq` (the shape of the prototype this replaces) does not carry version comparison, a policy table and a config
schema without becoming unreadable; a compiled binary would need a release pipeline before the tool is worth one.
Zero dependencies means any runner with `python3` and `gh` can run a sweep, and policy evaluation stays unit-testable
without touching GitHub.

Open: interactive onboarding may want a prompting library that the standard library cannot supply. If so, the
dependency is confined to the onboarding path, and the unattended sweep path stays dependency-free.
