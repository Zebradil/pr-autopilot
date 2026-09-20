# Code owns the verdict, the agent only repairs

Every verdict on a bot PR — merge, hold, escalate, dispatch a fix — is produced by deterministic code reading
policy and PR facts. The AI agent is invoked afterwards, with a narrow mandate (fix this PR, investigate and
comment), and can never merge or override a verdict. The alternative, letting an agent read the PR and decide,
costs a model call per PR on work that is a lookup table, and makes every merge a hallucination risk. Accepted
as the starting point; edge cases that genuinely need judgement will be collected and argued individually
rather than by loosening this rule up front.
