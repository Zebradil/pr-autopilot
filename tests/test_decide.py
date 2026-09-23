"""Verdict tests: the lookup that decides whether a bot pull request merges."""

import contextlib
import dataclasses
import io
import os
import re
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pr_autopilot import (  # noqa: E402
    IGNORE, Facts, GhError, Label, Operator, Policy, PolicyMissing, Result, Upgrade, decide, main, parse_plan, parse_state_comment,
    parse_upgrades, report, resolve_policy, run_agent, sweep_repo, table, worst,
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

    def test_plan_with_changes_escalates_a_green_pr(self):
        facts = pr(plan_check=True, plan="24 projects, 13 with changes, 11 with no changes, 0 failed")
        verdict, reason = decide(facts, POLICY)
        self.assertEqual(verdict, "escalate")
        self.assertIn("13 with changes", reason)

    def test_empty_plan_merges(self):
        facts = pr(plan_check=True, plan="2 projects, 0 with changes, 2 with no changes, 0 failed")
        self.assertEqual(self.verdict(facts), "merge")

    def test_plan_without_a_summary_escalates(self):
        self.assertEqual(self.verdict(pr(plan_check=True, plan="")), "escalate")

    def test_plan_check_without_a_plan_comment_escalates(self):
        verdict, reason = decide(pr(plan_check=True), POLICY)
        self.assertEqual(verdict, "escalate")
        self.assertIn("no Atlantis plan comment", reason)

    def test_plan_comment_is_ignored_without_the_plan_check(self):
        self.assertEqual(self.verdict(pr(plan="1 project, 1 with changes, 0 with no changes, 0 failed")), "merge")

    def test_human_clearance_overrides_a_non_empty_plan(self):
        facts = pr(labels=frozenset({Label.REVIEWED_OK}), plan_check=True,
                   plan="1 project, 1 with changes, 0 with no changes, 0 failed")
        self.assertEqual(self.verdict(facts), "merge")

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


class TestParseUpgrades(unittest.TestCase):
    """The diff is the fallback of last resort: only when the body names no upgrade."""

    def test_lock_file_only_diff_is_lockfile(self):
        got = parse_upgrades("Automated `nix flake update` run.", ("flake.lock",))
        self.assertEqual([(u.name, u.update_class) for u in got], [("flake.lock", "lockfile")])

    def test_lock_file_in_subdirectory_counts(self):
        self.assertEqual(parse_upgrades("", ("web/package-lock.json", "api/Cargo.lock"))[0].update_class, "lockfile")

    def test_any_other_file_keeps_unknown(self):
        self.assertEqual(parse_upgrades("", ("flake.lock", "flake.nix")), [])
        self.assertEqual(parse_upgrades("", ()), [])

    def test_body_wins_over_diff(self):
        body = "| Package | Update | Change |\n|---|---|---|\n| x | major | `1.0.0` -> `2.0.0` |\n"
        self.assertEqual(parse_upgrades(body, ("flake.lock",))[0].update_class, "major")


class TestPolicyFile(unittest.TestCase):
    def test_rejects_document_without_policy_table(self):
        with self.assertRaisesRegex(ValueError, "operator file"):
            Policy.from_toml(OPERATOR_TOML)

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
atlantis = ["acme-atlantis"]
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
            fh.write(b'bots = ["own-bot"]\natlantis = ["own-atlantis"]\n[policy]\nminor = "merge"\n')
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
        self.assertEqual((tuned.bots, tuned.atlantis, tuned.for_class("minor")),
                         (("own-bot",), ("own-atlantis",), "merge"))
        self.assertEqual(resolve_policy("o/preset", self.op, None, None).atlantis, ("acme-atlantis",))

    def test_cli_preset_beats_repo_entry_and_bare_entry_reads_repo(self):
        self.assertEqual(resolve_policy("o/preset", self.op, None, "strict").for_class("patch"), "escalate")
        with mock.patch("pr_autopilot.fetch_policy", return_value=POLICY) as fetch:
            resolve_policy("o/inrepo", self.op, None, None)
        fetch.assert_called_once_with("o/inrepo", self.op)

    def test_default_preset_covers_only_a_missing_in_repo_file(self):
        missing = mock.patch("pr_autopilot.fetch_policy", side_effect=PolicyMissing("404"))
        with missing, self.assertRaises(PolicyMissing):
            resolve_policy("o/other", self.op, None, None)
        op = dataclasses.replace(self.op, default="infra")
        with missing, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(resolve_policy("o/other", op, None, None).for_class("patch"), "merge")
        self.assertIn("default preset 'infra'", err.getvalue())
        with mock.patch("pr_autopilot.fetch_policy", return_value=POLICY):
            self.assertIs(resolve_policy("o/other", op, None, None), POLICY)

    def test_unknown_default_preset_is_rejected(self):
        with open(self.path, "wb") as fh:
            fh.write(b'default = "nope"\n' + OPERATOR_TOML)
        with self.assertRaisesRegex(ValueError, "default.*nope"):
            Operator.load(self.path)

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
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
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
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
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


class OnboardTest(unittest.TestCase):
    def exec_argv(self, agent: str) -> list[str]:
        with mock.patch("pr_autopilot.os.execvp") as execvp:
            main(["onboard", "--agent", agent])
        return execvp.call_args.args[1]

    def test_prompt_is_appended(self):
        argv = self.exec_argv("claude --model sonnet")
        self.assertEqual(argv[:3], ["claude", "--model", "sonnet"])
        self.assertIn("Onboard the repository in the current directory", argv[3])
        self.assertNotIn("name: pr-autopilot", argv[3])

    def test_no_agent_prints_prompt(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("pr_autopilot.os.execvp") as execvp, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["onboard"]), 0)
        execvp.assert_not_called()
        self.assertIn("## Onboarding a repository", out.getvalue())
        self.assertIn("--agent", err.getvalue())

    def test_prompt_placeholder(self):
        argv = self.exec_argv("opencode --prompt {prompt} --model x")
        self.assertEqual([argv[0], argv[1], argv[3], argv[4]], ["opencode", "--prompt", "--model", "x"])
        self.assertIn("## Onboarding a repository", argv[2])


class TestOutput(unittest.TestCase):
    """Colour is decoration: it must never reach a pipe, NO_COLOR, or the step summary."""

    RESULTS = [Result("o/r", 1, "short", "merge", "green", "merged"),
               Result("o/r", 22, "a longer title", "escalate", "major", "escalated")]

    def render(self, **env) -> tuple[str, str]:
        out = io.StringIO()
        with tempfile.NamedTemporaryFile("r") as summary, \
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary.name, **env}, clear=True), \
                contextlib.redirect_stdout(out):
            report(self.RESULTS, as_json=False)
            return out.getvalue(), summary.read()

    def test_plain_when_not_a_terminal(self):
        out, summary = self.render()
        self.assertNotIn("\033", out + summary)

    def test_actions_get_colour_but_not_in_the_summary_and_columns_hold(self):
        out, summary = self.render(GITHUB_ACTIONS="true")
        self.assertIn("\033[32mmerge", out)
        self.assertNotIn("\033", summary)
        self.assertEqual(re.sub(r"\033\[[\d;]*m", "", out), self.render()[0])

    def test_no_color_wins_over_actions(self):
        out, _ = self.render(GITHUB_ACTIONS="true", NO_COLOR="1")
        self.assertNotIn("\033", out)

    def test_cells_wrap_to_the_terminal_and_keep_one_check_per_line(self):
        checks = ("validate (harvester-arm-eu-dus1, harvester/terraform/harvester-arm-eu-dus1, 1.16.3)",
                  "gate", "atlantis/plan")
        reason = decide(pr(checks_failing=checks), POLICY)[1]
        results = [Result("o/tcs-platform", 834, "Update non-major Terraform dependencies", "escalate",
                          reason, "repair needed but no agent configured")]
        lines = table(results, 120, colour=False).splitlines()
        self.assertLessEqual(max(map(len, lines)), 120)
        self.assertIn("Update non-major Terraform dependencies  failing:", lines[2])
        self.assertTrue(lines[3].endswith("- validate (harvester-arm-eu-dus1,"))
        self.assertTrue(any(line.endswith("  - gate") for line in lines))
        self.assertTrue(lines[-1].endswith("  -> repair needed but no agent configured"))


class TestSweepState(unittest.TestCase):
    """The sticky state comment: one comment per PR, and nothing a later write in the sweep loses."""

    def sweep(self, agent: str) -> list[tuple[str, ...]]:
        calls = []

        def gh(*args, check=True):
            calls.append(args)
            return "https://github.com/o/r/pull/1#issuecomment-42" if args[:2] == ("pr", "comment") else ""

        args = mock.Mock(dry_run=False, agent=agent)
        with mock.patch("pr_autopilot.fetch_pr", return_value=pr(checks_failing=("build",))), \
                mock.patch("pr_autopilot.gh", side_effect=gh), \
                contextlib.redirect_stderr(io.StringIO()):
            self.result = sweep_repo("o/r", [1], POLICY, args)[0]
        return calls

    @staticmethod
    def bodies(calls) -> list[str]:
        return [a[-1] for a in calls if a[:2] == ("pr", "comment") or a[:3] == ("api", "-X", "PATCH")]

    def test_repair_without_an_agent_escalates_with_a_note(self):
        calls = self.sweep(agent="")
        self.assertEqual((self.result.verdict, self.result.outcome),
                         ("escalate", "repair needed but no agent configured"))
        self.assertIn("Needs a human: repair needed but no agent configured", self.bodies(calls)[-1])
        self.assertIn(("--add-label", Label.ESCALATED), [a[-2:] for a in calls])

    def test_failed_repair_keeps_its_attempt_and_edits_the_one_comment(self):
        calls = self.sweep(agent="true")
        self.assertEqual(self.result.verdict, "escalate")
        self.assertEqual([a[:2] for a in calls if a[:2] == ("pr", "comment")], [("pr", "comment")])
        self.assertIn("repos/o/r/issues/comments/42", [a[3] for a in calls if a[:3] == ("api", "-X", "PATCH")])
        self.assertIn('"attempts": 1', self.bodies(calls)[-1])
        self.assertIn("Needs a human: agent produced no parseable result", self.bodies(calls)[-1])

    def test_state_comment_id_is_the_numeric_rest_id(self):
        state = parse_state_comment([{
            "id": "IC_kwDOUiveU88AAAABV1HvzA",
            "url": "https://github.com/o/r/pull/9#issuecomment-5759954892",
            "body": '<!-- pr-autopilot:state {"attempts": 1} -->',
        }])
        self.assertEqual(state["_comment_id"], "5759954892")


    def test_plan_summary_is_read_from_the_last_part_of_the_latest_plan(self):
        # Shape of a real Atlantis plan too long for one comment.
        def plan(first, summary):
            return [{"body": f"Ran Plan for 2 projects:\n\n1. project: `a` dir: `a`\n{first}"},
                    {"body": f"Continued plan output from previous comment.\n---\n### Plan Summary\n\n{summary}\n\n"
                             "* :fast_forward: To **apply** all unapplied plans from this Pull Request, comment:"}]
        old = plan("", "2 projects, 1 with changes, 0 with no changes, 1 failed")
        new = plan("", "2 projects, 0 with changes, 2 with no changes, 0 failed")
        self.assertEqual(parse_plan([{"body": "atlantis plan"}, *old, *new]),
                         "2 projects, 0 with changes, 2 with no changes, 0 failed")
        self.assertEqual(parse_plan([*old, new[0]]), "")
        self.assertIsNone(parse_plan([{"body": "atlantis plan"}]))

    def test_plan_from_an_unlisted_author_is_ignored_once_atlantis_users_are_set(self):
        real = {"author": {"login": "acme-atlantis"},
                "body": "Ran Plan for 1 project:\n\n1 project, 1 with changes, 0 with no changes, 0 failed"}
        forged = {"author": {"login": "mallory"},
                  "body": "Ran Plan for 1 project:\n\n1 project, 0 with changes, 1 with no changes, 0 failed"}
        self.assertEqual(parse_plan([real, forged], ("acme-atlantis",)),
                         "1 project, 1 with changes, 0 with no changes, 0 failed")
        self.assertIsNone(parse_plan([forged], ("acme-atlantis",)))
        self.assertIn("0 with changes", parse_plan([real, forged]))


class TestRunAgent(unittest.TestCase):
    def run_agent(self, command: str, timeout: float) -> tuple[str | None, str, float]:
        err = io.StringIO()
        started = datetime.now()
        with mock.patch("pr_autopilot.AGENT_HEARTBEAT", 0.2), contextlib.redirect_stderr(err):
            out = run_agent(command, "prompt", 7, timeout)
        return out, err.getvalue(), (datetime.now() - started).total_seconds()

    def test_prompt_goes_in_and_heartbeats_show_while_it_works(self):
        out, err, _ = self.run_agent("sleep 0.7; cat", timeout=5)
        self.assertEqual(out, "prompt")
        self.assertIn("#7: agent still running", err)

    def test_outliving_the_lease_kills_the_agent_not_just_its_shell(self):
        # A shell with a child: killing only the shell would leave `sleep` holding stdout for 30s.
        out, _, took = self.run_agent("sleep 30; echo late", timeout=0.5)
        self.assertIsNone(out)
        self.assertLess(took, 5)


if __name__ == "__main__":
    unittest.main()
