# PR fixtures

Real `gh pr view` payloads, captured 2026-09-20 from open bot PRs in the Zebradil org.

Capture command (fields are load-bearing — keep the list in sync):

```bash
gh pr view <number> --repo <owner/repo> \
  --json number,title,author,body,labels,state,isDraft,mergeable,mergeStateStatus,statusCheckRollup,headRefName,url,comments \
  > tests/fixtures/<slug>.json
```

| File | Repo | PR | Why interesting |
|---|---|---|---|
| `know-mcp-28-rust-major-group.json` | Zebradil/know-mcp | #28 | Renovate grouped **major** — 4-column table (`Package \| Type \| Update \| Change`), 3 rows, failing checks |
| `know-mcp-25-rust-minor-group.json` | Zebradil/know-mcp | #25 | Renovate grouped minor/patch, failing checks plus a `StatusContext` entry and a bot comment on the PR |
| `know-mcp-29-lockfile-maintenance.json` | Zebradil/know-mcp | #29 | Lock file maintenance — 2-column table (`Update \| Change`), no package name, all checks green |
| `know-mcp-27-docker-major.json` | Zebradil/know-mcp | #27 | Docker tag major — 3-column table (`Package \| Update \| Change`), mixed pass/fail checks |
| `kasha-49-single-crate-patch.json` | Zebradil/kasha | #49 | Single crate patch, green checks, `mergeStateStatus: BLOCKED` (green but not mergeable) |
| `hugo-theme-zeta-97-security-npm.json` | Zebradil/hugo-theme-zeta | #97 | `[SECURITY]` title suffix, merge-confidence table (`Package \| Change \| Age \| Confidence`), **zero** status checks |
| `rustotpony-73-dependabot-rust.json` | Zebradil/rustotpony | #73 | **Dependabot** single update — body has **no markdown table** at all, HTML `<details>` blocks, `[//]: #` automerge markers |
| `renovate-regex-manager-validator-action-36-dependabot-group.json` | Zebradil/renovate-regex-manager-validator-action | #36 | Dependabot **grouped** — different table (`Package \| From \| To`), 22 checks, failing, title says 17 updates while body says 16 |
| `hugo-theme-zeta-83-dependabot-conflicting.json` | Zebradil/hugo-theme-zeta | #83 | `mergeable: CONFLICTING` / `mergeStateStatus: DIRTY`, no checks |
| `airgradient-exporter-56-action-major.json` | Zebradil/airgradient-exporter | #56 | GitHub Action major bump, all green, 5 checks |
| `powerline-taskwarrior-107-python-major.json` | Zebradil/powerline-taskwarrior | #107 | Python dep major; green `CheckRun`s plus a failing `StatusContext` (`renovate/artifacts`) — overall not mergeable |
| `tree-sitter-test_highlights-1-onboarding.json` | Zebradil/tree-sitter-test_highlights | #1 | Renovate onboarding PR — no dependency table, `<!--renovate-config-hash:...-->` marker instead of `renovate-debug` |

## Shapes a parser must handle

- Renovate table header varies with update kind and with whether merge-confidence badges are on:
  `| Package | Type | Update | Change |`, `| Package | Update | Change |`, `| Update | Change |`,
  `| Package | Change | [Age](...) | [Confidence](...) |`.
- The `Change` cell has two forms: `` `=9.3.1` → `=11.1.0` `` (plain) and `[`7.26.0` → `7.29.6`](https://renovatebot.com/diffs/...)` (merge-confidence repos).
- Renovate separator row is `|---|---|---|` (no spaces); Dependabot's is `| --- | --- | --- |` (spaces).
- Dependabot single-package PRs have no table — only `Bumps [name](url) from X to Y.`
- `statusCheckRollup` mixes two types: `CheckRun` (`status`/`conclusion`/`name`) and `StatusContext` (`state`/`context`, **no** `status` or `conclusion`).
- `author` is `{"is_bot": true, "login": "app/renovate"}`, but `comments[].author` is `{"login": "renovate"}` with no `is_bot`.
- `mergeable` can be `UNKNOWN` while GitHub computes it; re-reading later yields the real value.
