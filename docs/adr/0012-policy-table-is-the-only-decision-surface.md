# The policy table is the only thing the engine reads

Risk axes are recorded in the configuration file as documentation — they explain to a human why the policy table
looks the way it does, and they are the input onboarding reasoned from. The engine itself reads only the policy
table: update class to verdict, plus limits. It does not derive verdicts from the axes at run time.

Two places deciding the same thing is how a tool acquires a rules interpreter, an evaluation order and a class of bug
where the file says one thing and the behaviour is another. A human editing the table gets exactly what they typed.
