"""Parser tests against recorded payloads from real bot pull requests.

Fixtures are captured with `gh pr view <n> --repo <r> --json ...`, never written by hand: the point
is to be right about what GitHub and the bots actually emit. Regenerate the expectations with
`python3 tests/test_fixtures.py --update` and read the diff before committing it.
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pr_autopilot import Policy, decide, facts_from_json  # noqa: E402

FIXTURES = HERE / "fixtures"
EXPECTED = HERE / "expected.json"

POLICY = Policy(table={"patch": "merge", "minor": "merge", "digest": "merge", "pin": "merge",
                       "lockfile": "merge", "major": "escalate"})


def summarise(path: Path) -> dict:
    facts = facts_from_json("o/r", json.loads(path.read_text()))
    verdict, reason = decide(facts, POLICY)
    return {
        "author": facts.author,
        "upgrades": [[u.name, u.update_class, u.current, u.new] for u in facts.upgrades],
        "checks": [len(facts.checks_failing), len(facts.checks_pending), facts.checks_total],
        "verdict": verdict,
        "reason": reason,
    }


class TestFixtures(unittest.TestCase):
    def test_every_fixture_matches(self):
        expected = json.loads(EXPECTED.read_text())
        actual = {p.name: summarise(p) for p in sorted(FIXTURES.glob("*.json"))}
        self.assertEqual(set(expected), set(actual), "fixture set changed; rerun with --update")
        for name in sorted(expected):
            with self.subTest(fixture=name):
                self.assertEqual(expected[name], actual[name])


if __name__ == "__main__":
    if "--update" in sys.argv:
        EXPECTED.write_text(json.dumps(
            {p.name: summarise(p) for p in sorted(FIXTURES.glob("*.json"))}, indent=2) + "\n")
        print(f"wrote {EXPECTED}")
    else:
        unittest.main()
