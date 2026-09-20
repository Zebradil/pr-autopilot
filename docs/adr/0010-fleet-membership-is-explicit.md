# Fleet membership is explicit

A scheduled sweep acts only on repositories listed in the operator's configuration. Discovery by repository topic or
by the presence of an in-repo policy file is deliberately deferred: with implicit membership, a repository can start
merging pull requests unattended because someone added a topic, and the blast radius of a mistake should require an
edit to the operator's own configuration. A discovery helper that prints candidates to paste is fine, and implicit
membership may be revisited once the tool has run for a while.
