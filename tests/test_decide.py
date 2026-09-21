"""Verdict tests: the lookup that decides whether a bot pull request merges."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pr_autopilot import (  # noqa: E402
    IGNORE, Facts, GhError, Label, Operator, Policy, Result, Upgrade, decide, main, resolve_policy, worst,
)

# Keeps the developer's own operator file out of every main() call below.
os.environ["PR_AUTOPILOT_CONFIG"] = os.path.join(tempfile.gettempdir(), "pr-autopilot-tests-absent.toml")

POLICY = Policy(table={"patch": "merge", "minor": "merge", "digest": "merge",
                       "lockfile": "merge", "major": "escalate"})


def pr(**kw) -> Facts:
    base = dict(
        repo="o/r", number=1, title="chore(deps): update x", author="renovate[bot]",
        url="", head_ref="renovate/x", state="OPEN", is_draft=False,
        mergeable="MERGEABLE", merge_state="CLEAN", labels=frozenset(),
        upgrades=(Upgrade("x", "patch", "1.0.0", "1.0.1"),),
        checks_failing=(), checks_pending=(), checks_total=3, state_comment={},
    )
    return Facts(**{**base, **kw})


class TestDecide(unittest.TestCase):
    def verdict(self, facts, policy=POLICY):
        return decide(facts, policy)[0]

    def test_green_patch_merges(self):
        self.assertEqual(self.verdict(pr()), "merge")

    def test_major_escalates_even_when_green(self):
        self.assertEqual(self.verdict(pr(upgrades=(Upgrade("x", "major", "1", "2"),))), "escalate")

    def test_group_takes_the_most_conservative_member(self):
        group = pr(upgrades=(Upgrade("a", "patch"), Upgrade("b", "major"), Upgrade("c", "minor")))
        self.assertEqual(self.verdict(group), "escalate")

    def test_unknown_class_is_not_mergeable(self):
        self.assertEqual(self.verdict(pr(upgrades=(Upgrade("x", "unknown"),))), "escalate")

    def test_no_upgrades_parsed_is_unknown(self):
        self.assertEqual(self.verdict(pr(upgrades=())), "escalate")

    def test_failing_checks_route_to_repair(self):
        self.assertEqual(self.verdict(pr(checks_failing=("build",))), "repair")

    def test_failing_checks_on_a_major_still_escalate(self):
        facts = pr(upgrades=(Upgrade("x", "major"),), checks_failing=("build",))
        self.assertEqual(self.verdict(facts), "escalate")

    def test_pending_checks_wait(self):
        self.assertEqual(self.verdict(pr(checks_pending=("build",))), "wait")

    def test_conflicting_routes_to_repair(self):
        self.assertEqual(self.verdict(pr(mergeable="CONFLICTING")), "repair")

    def test_unknown_mergeability_waits(self):
        self.assertEqual(self.verdict(pr(mergeable="UNKNOWN")), "wait")

    def test_hold_label_wins_over_everything(self):
        self.assertEqual(self.verdict(pr(labels=frozenset({Label.HOLD}))), "hold")

    def test_disabled_policy_holds(self):
        self.assertEqual(self.verdict(pr(), Policy(enabled=False)), "hold")

    def test_escalated_pr_is_left_alone(self):
        facts = pr(labels=frozenset({Label.ESCALATED}), checks_failing=("build",))
        self.assertEqual(decide(facts, POLICY), ("escalate", "already escalated, waiting for a human"))

    def test_human_clearance_merges_a_major(self):
        facts = pr(upgrades=(Upgrade("x", "major"),), labels=frozenset({Label.REVIEWED_OK}))
        self.assertEqual(self.verdict(facts), "merge")

    def test_human_clearance_does_not_merge_a_red_pr(self):
        facts = pr(labels=frozenset({Label.REVIEWED_OK}), checks_failing=("build",))
        self.assertEqual(self.verdict(facts), "repair")

    def test_attempt_cap_escalates(self):
        self.assertEqual(self.verdict(pr(state_comment={"attempts": 2}, checks_failing=("b",))), "escalate")

    def test_live_lease_makes_a_second_sweep_wait(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.assertEqual(self.verdict(pr(state_comment={"lease_until": soon}, checks_failing=("b",))), "wait")

    def test_expired_lease_is_ignored(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertEqual(self.verdict(pr(state_comment={"lease_until": past}, checks_failing=("b",))), "repair")

    def test_human_pr_is_ignored(self):
        self.assertEqual(self.verdict(pr(author="Zebradil")), IGNORE)

    def test_closed_pr_is_ignored(self):
        self.assertEqual(self.verdict(pr(state="CLOSED")), IGNORE)

    def test_draft_waits(self):
        self.assertEqual(self.verdict(pr(is_draft=True)), "wait")

    def test_missing_class_in_table_defaults_to_escalate(self):
        self.assertEqual(self.verdict(pr(), Policy(table={"minor": "merge"})), "escalate")


class TestNoChecks(unittest.TestCase):
    def test_a_pr_with_no_checks_is_not_green(self):
        self.assertEqual(decide(pr(checks_total=0), POLICY), ("escalate", "no checks ran"))

    def test_repository_can_opt_in_to_merging_without_checks(self):
        policy = Policy(table=POLICY.table, allow_without_checks=True)
        self.assertEqual(decide(pr(checks_total=0), policy)[0], "merge")


class TestOrdering(unittest.TestCase):
    def test_worst_picks_the_conservative_one(self):
        self.assertEqual(worst("merge", "escalate"), "escalate")
        self.assertEqual(worst("wait", "merge"), "wait")
        self.assertEqual(worst("escalate", "hold"), "hold")

    def test_agent_can_never_loosen(self):
        for agent_says in ("merge", "gate", "wait", "repair", "escalate", "hold"):
            self.assertEqual(worst("escalate", agent_says), max(("escalate", agent_says),
                             key=["merge", "gate", "wait", "repair", "escalate", "hold"].index))


class TestPolicyFile(unittest.TestCase):
    def test_rejects_unknown_verdict(self):
        with self.assertRaises(ValueError):
            Policy.from_toml(b'[policy]\npatch = "yolo"\n')

    def test_rejects_unknown_class(self):
        with self.assertRaises(ValueError):
            Policy.from_toml(b'[policy]\npacth = "merge"\n')

    def test_reads_limits_and_strategy(self):
        p = Policy.from_toml(b'[policy]\npatch="merge"\n[limits]\nmax_merges=2\n[repair]\nstrategy="side-pr"\n')
        self.assertEqual((p.max_merges, p.repair_strategy, p.for_class("patch")), (2, "side-pr", "merge"))


OPERATOR_TOML = b"""
bots = ["acme-renovate"]
[presets.infra.policy]
patch = "merge"
major = "escalate"
[presets.strict.policy]
patch = "escalate"
[repos."o/preset"]
preset = "infra"
[repos."o/tuned"]
policy = "tuned.toml"
[repos."o/inrepo"]
"""


class TestOperatorFile(unittest.TestCase):
    """Presets and per-repo entries let a repository be swept before it carries a policy file."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.toml")
        with open(self.path, "wb") as fh:
            fh.write(OPERATOR_TOML)
        with open(os.path.join(self.dir.name, "tuned.toml"), "wb") as fh:
            fh.write(b'bots = ["own-bot"]\n[policy]\nminor = "merge"\n')
        self.op = Operator.load(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_preset_inherits_operator_bots(self):
        p = self.op.preset("infra")
        self.assertEqual((p.bots, p.for_class("patch"), p.for_class("minor")),
                         (("acme-renovate",), "merge", "escalate"))

    def test_unknown_preset_names_the_known_ones(self):
        with self.assertRaisesRegex(ValueError, "infra.*strict"):
            self.op.preset("nope")

    def test_repo_entries_resolve_without_github(self):
        with mock.patch("pr_autopilot.gh_json", side_effect=AssertionError("no IO expected")):
            self.assertEqual(resolve_policy("o/preset", self.op, None, None).for_class("patch"), "merge")
            tuned = resolve_policy("o/tuned", self.op, None, None)
        self.assertEqual((tuned.bots, tuned.for_class("minor")), (("own-bot",), "merge"))

    def test_cli_preset_beats_repo_entry_and_bare_entry_reads_repo(self):
        self.assertEqual(resolve_policy("o/preset", self.op, None, "strict").for_class("patch"), "escalate")
        with mock.patch("pr_autopilot.fetch_policy", return_value=POLICY) as fetch:
            resolve_policy("o/inrepo", self.op, None, None)
        fetch.assert_called_once_with("o/inrepo", ("acme-renovate",))

    def test_legacy_repo_list(self):
        with open(self.path, "wb") as fh:
            fh.write(b'repos = ["a/b", "c/d"]\n')
        self.assertEqual(list(Operator.load(self.path).repos), ["a/b", "c/d"])

    def test_malformed_repo_entries_are_rejected(self):
        for body in (b'repos = {"a/b" = "infra"}\n',
                     b'[repos."a/b"]\npresets = "infra"\n',
                     b'[repos."a/b"]\npreset = "infra"\npolicy = "x.toml"\n'):
            with self.subTest(body=body):
                with open(self.path, "wb") as fh:
                    fh.write(body)
                with self.assertRaisesRegex(ValueError, "a/b"):
                    Operator.load(self.path)

    def test_fleet_sweeps_every_repo_in_the_env_operator_file(self):
        with mock.patch.dict(os.environ, {"PR_AUTOPILOT_CONFIG": self.path}), \
             mock.patch("pr_autopilot.shutil.which", return_value="/usr/bin/gh"), \
             mock.patch("pr_autopilot.fetch_policy", return_value=POLICY), \
             mock.patch("pr_autopilot.list_bot_prs", return_value=[]), \
             mock.patch("pr_autopilot.sweep_repo", return_value=[]) as sweep, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["sweep", "--fleet", "--dry-run"]), 0)
        self.assertEqual([c.args[0] for c in sweep.call_args_list], ["o/preset", "o/tuned", "o/inrepo"])

    def test_unknown_cli_preset_fails_before_sweeping(self):
        with mock.patch.dict(os.environ, {"PR_AUTOPILOT_CONFIG": self.path}), \
             mock.patch("pr_autopilot.shutil.which", return_value="/usr/bin/gh"), \
             mock.patch("pr_autopilot.sweep_repo") as sweep, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["sweep", "--repo", "o/r", "--preset", "nope"]), 1)
        sweep.assert_not_called()
        self.assertIn("infra", err.getvalue())


class TestExitCode(unittest.TestCase):
    """The exit code says whether the sweep ran, not what it decided."""

    def sweep(self, *outcomes: str) -> int:
        results = [Result("o/r", i, "t", "merge", "why", o) for i, o in enumerate(outcomes)]
        with mock.patch("pr_autopilot.shutil.which", return_value="/usr/bin/gh"), \
             mock.patch("pr_autopilot.fetch_policy", return_value=POLICY), \
             mock.patch("pr_autopilot.list_bot_prs", return_value=list(range(len(outcomes)))), \
             mock.patch("pr_autopilot.sweep_repo", return_value=results), \
             contextlib.redirect_stdout(io.StringIO()):
            return main(["sweep", "--repo", "o/r"])

    def test_a_lone_unmerged_pr_is_not_a_failed_sweep(self):
        # The regression: keying the exit code off one result's outcome turned a repository
        # with a single pull request waiting on checks into a red scheduled run every night.
        for outcome in ("no action", "would merge", "escalated", "deferred"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.sweep(outcome), 0)

    def test_merges_and_escalations_alike_exit_zero(self):
        self.assertEqual(self.sweep("merged"), 0)
        self.assertEqual(self.sweep("merged", "escalated"), 0)


class TestBrokenSweep(unittest.TestCase):
    """A sweep that could not run must never read as a sweep that found nothing to do."""

    def run_main(self, **patches) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch("pr_autopilot.shutil.which", return_value="/usr/bin/gh"), \
             contextlib.ExitStack() as stack, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            for target, kw in patches.items():
                stack.enter_context(mock.patch(f"pr_autopilot.{target}", **kw))
            return main(["sweep", "--repo", "o/r"]), out.getvalue()

    def test_an_unauthenticated_gh_fails_the_sweep(self):
        # The regression: an empty GH_TOKEN made the policy fetch fail, which was filed under
        # "no usable policy; skipping", and the run went green having triaged nothing.
        code, out = self.run_main(
            gh_json=dict(side_effect=GhError("gh: To use GitHub CLI in a GitHub Actions workflow, "
                                             "set the GH_TOKEN environment variable.")))
        self.assertEqual(code, 1)
        self.assertNotIn("no bot pull requests", out)

    def test_a_repository_without_a_policy_is_still_skipped(self):
        code, out = self.run_main(gh_json=dict(side_effect=GhError("gh: Not Found (HTTP 404)")))
        self.assertEqual(code, 0)
        self.assertIn("no bot pull requests", out)

    def test_a_failure_while_listing_pull_requests_fails_the_sweep(self):
        code, _ = self.run_main(fetch_policy=dict(return_value=POLICY),
                                list_bot_prs=dict(side_effect=GhError("HTTP 502")))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
