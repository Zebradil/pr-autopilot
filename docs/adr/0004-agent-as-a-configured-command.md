# The agent is a configured command, not a harness integration

The engine invokes whatever agent the operator configured (`claude -p`, `opencode run`, `cursor-agent`, …) as a
subprocess, passing a prompt rendered from a shared Markdown source. Harness-specific assets — a Claude skill, an
OpenCode agent file, a Cursor rule — are optional conveniences layered on top, never the mechanism. This is what
keeps unattended runs identical across harnesses and keeps the project from carrying one integration per vendor.
