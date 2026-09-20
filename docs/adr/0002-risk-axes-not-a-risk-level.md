# Repository risk is several axes, never one level

Policy reads named orthogonal properties of a repository — how releases happen, whether main deploys to
production, how much test confidence exists, how critical the project is, whether an external plan gate applies —
instead of a single `low | medium | high` dial. A single dial hides *why* a merge is risky and collapses cases
that need different actions: thin test coverage calls for writing tests, an Atlantis gate calls for running a plan,
and a release-please setup calls for neither. A derived tier may appear in reports for human convenience, but it is
never a configuration field and policy never reads it.
