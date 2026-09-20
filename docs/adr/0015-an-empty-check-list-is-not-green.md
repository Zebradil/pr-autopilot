# A pull request with no checks is not green

A pull request whose check list is empty reaches `escalate`, not `merge`. Repositories with no CI exist, and a
naive "nothing is failing" test merges everything in them silently — the same class of mistake as `all()` over an
empty list returning true. A repository that genuinely wants updates merged without evidence sets
`allow_without_checks = true` in its policy, which is a sentence an operator has to write on purpose and a reviewer
can see.

This follows from ADR 0008: checks are the contract. No checks means no contract.
