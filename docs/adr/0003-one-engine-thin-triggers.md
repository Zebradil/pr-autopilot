# One engine, thin triggers

Manual runs, scheduled sweeps and reactive runs on `pull_request` events all shell out to the same command-line
engine with different arguments. GitHub Actions workflows, cron entries and agent harnesses hold no logic of their
own. Expressing the reactive path in workflow YAML and the scheduled path in a script would duplicate the policy
evaluation in a place that cannot be tested locally, and the two copies would drift.
