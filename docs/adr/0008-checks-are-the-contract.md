# Repository checks are the contract; onboarding invests in them

In steady state the engine's only evidence about whether an update is safe is the repository's check results. It does
not read release notes, reason about migrations, or interpret tool-specific bot comments when it can avoid it.
Anything the autopilot needs to know should therefore be made into a check *at onboarding*: a plan that must be empty
becomes a job that fails on a non-empty plan, thin coverage becomes tests worth having anyway.

The consequence is deliberate: a repository whose checks cannot detect a breaking update is a repository the
autopilot should not merge into unattended, and the honest fix is to improve the checks rather than to buy a model
call per pull request forever. Policy can buy an advisory review for such repositories as an interim measure, but
that is a stopgap with a recurring bill, not the design.
