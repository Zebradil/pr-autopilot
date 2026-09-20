"""Verdict tests: the lookup that decides whether a bot pull request merges."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pr_autopilot import IGNORE, Facts, Label, Policy, Upgrade, decide, worst  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
