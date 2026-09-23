# Tokens come from octo-sts, not from repository secrets

Follows ADR 0014. The App stays the identity; what changes is where its private key lives. A personal account has no
account-level secrets, so minting the token in the workflow put a copy of the key in every governed repository:
onboarding carried a secrets step, rotation touched every repository, and write access to any one of them was enough
to take the key and mint tokens for all of them.

A self-hosted octo-sts holds the key and exchanges the workflow's GitHub OIDC token for an installation token. One
trust policy in the account's `.github` repository governs every repository, with `caller_repository_only` narrowing
each token to the repository whose workflow asked and `workflow_ref` limiting the askers to the pr-autopilot
workflows. With the App installed on all repositories, onboarding writes the workflows and nothing else.

The cost is a service to run: when it is down, every run fails. Runs are idempotent and the scheduled sweep retries,
so that is accepted. The public `octo-sts.dev` was rejected because it acts as its own App, with write access to
administration, contents and workflows wherever it is installed. `caller_repository_only` and an OIDC issuer
allowlist are not upstream yet, so the instance runs a fork until they land.

The action is unchanged: it takes the token like any other. The App-key and token paths stay for operators without
octo-sts.
