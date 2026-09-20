# Onboarding is where model reasoning is spent

Understanding a repository — how it releases, whether main reaches production, whether its tests are worth trusting,
which checks are missing — is a one-time reasoning job that deserves a capable model, and gets one. Onboarding detects
what is detectable, reasons about the rest, and delivers a pull request carrying the policy file, the labels, the bot
metadata template and the check improvements it recommends, with those improvements as separate commits the operator
can drop.

Run interactively, onboarding proposes and the operator adjusts and approves before the pull request exists. Run
unattended, it opens the pull request directly — a pull request, never a merge. Steady-state sweeps inherit the
result and stay dumb, which is what keeps the recurring cost proportional to the number of repositories rather than
to the number of pull requests.
