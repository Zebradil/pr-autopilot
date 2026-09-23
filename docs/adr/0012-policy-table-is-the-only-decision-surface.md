# The policy table is the only thing the engine reads

Risk axes are recorded in the configuration file as documentation — they explain to a human why the policy table
looks the way it does, and they are the input onboarding reasoned from. The engine itself reads only the policy
table: update class to verdict, plus limits. It does not derive verdicts from the axes at run time.

Two places deciding the same thing is how a tool acquires a rules interpreter, an evaluation order and a class of bug
where the file says one thing and the behaviour is another. A human editing the table gets exactly what they typed.

## Addendum: manager overrides

Checks prove more for some ecosystems than others: a major bump of a GitHub Action that CI itself runs is
exercised by that CI, a major bump of a library usually is not. A repository mixing both needs a different verdict
per ecosystem, so `[policy]` may carry one sub-table per manager, keyed by the name the bot reported. The lookup
stays a lookup — the manager's entry for the class, else the base entry — with no precedence rules, matchers or
evaluation order. Matching on package names, paths or dependency types was left to the bot's own configuration.
