# Repairs land on the bot's branch by default

A fix for a failing dependency update is pushed to the bot's own pull request branch, because the fix and the bump
usually only make sense together. The alternative — landing the compatibility fix as its own pull request against
the main branch and waiting for the bot to rebase — doubles the latency and the number of things that can go wrong,
and deciding which fixes are independently valuable is a judgement the engine cannot make. A `repair.strategy` key
selects the side-pull-request behaviour per repository without a code change.

Known consequence to verify before building: Renovate treats a branch it did not write last as modified and changes
how it maintains it, which may leave the branch un-rebased after a repair.
