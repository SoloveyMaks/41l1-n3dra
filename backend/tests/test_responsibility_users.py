"""Focused identity/review checks with synthetic provider metadata, no network.

Run: /tmp/nedra-venv/bin/python backend/tests/test_responsibility_users.py
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.responsibility.history import _identity, _person, _review_decisions, _safe_provider_url


HEAD = "a" * 40
MERGED = "2026-10-25T03:00:00Z"


def review(state, hour=1, login="reviewer", head=HEAD):
    return {"user": {"login": login, "type": "User"}, "state": state,
            "submitted_at": f"2026-10-25T{hour:02}:00:00Z", "commit_id": head}


def decisions(events, **kwargs):
    return _review_decisions(events, head_sha=kwargs.get("head", HEAD),
                             merged_at=kwargs.get("merged", MERGED),
                             incomplete=kwargs.get("incomplete", False))


class IdentityChecks(unittest.TestCase):
    def test_provider_account_constructs_profile_and_drops_email(self):
        person = _person({"login": "gpshead", "type": "User", "email": "private@example.org"},
                         "Gregory P. Smith", provider="github")
        self.assertEqual(person.profileUrl, "https://github.com/gpshead")
        self.assertEqual(person.displayName, "Gregory P. Smith")
        self.assertFalse(person.isBot)
        self.assertNotIn("email", person.model_dump_json())

    def test_unlinked_git_name_never_becomes_login(self):
        for provider in ("github", "gitlab"):
            with self.subTest(provider=provider):
                person = _identity(None, "Full Name", provider=provider)
                self.assertEqual(person.displayName, "Full Name")
                self.assertIsNone(person.login)
                self.assertIsNone(person.profileUrl)
        self.assertIsNone(_identity(None, provider="github"))

    def test_gitlab_username_is_separate_from_author_name(self):
        person = _person({"username": "real.user", "name": "Full Name"}, provider="gitlab")
        self.assertEqual(person.login, "real.user")
        self.assertEqual(person.profileUrl, "https://gitlab.com/real.user")
        self.assertIsNone(_person({"author_name": "Full Name"}, provider="gitlab").login)

    def test_bot_is_established_from_account_type_or_login_suffix(self):
        for account in ({"login": "web-flow", "type": "Bot"}, {"login": "github-actions[bot]"}):
            self.assertTrue(_person(account, provider="github").isBot)
        self.assertIsNone(_person(None, "Release Bot", provider="github").isBot)

    def test_untrusted_profile_url_cannot_override_account_link(self):
        for url in ("https://evil.example/gpshead", "https://github.com@evil.example/gpshead",
                    "https://github.com/gpshead?next=https://evil.example", "javascript:alert(1)"):
            with self.subTest(url=url):
                person = _person({"login": "gpshead", "html_url": url}, provider="github")
                self.assertEqual(person.profileUrl, "https://github.com/gpshead")

    def test_malformed_logins_cannot_become_profile_paths(self):
        for login in ("Full Name", "../evil", "person?next=evil", "user#frag", "user@host", "user/path", "", "[bot]"):
            with self.subTest(login=login):
                person = _person({"login": login}, provider="github")
                self.assertIsNone(person.login)
                self.assertIsNone(person.profileUrl)

    def test_provider_urls_reject_foreign_hosts_and_extra_parts(self):
        for url in ("https://evil.example/user", "http://github.com/user", "https://github.com:443/user",
                    "https://x@github.com/user", "https://github.com/user?next=elsewhere",
                    "https://github.com/user#frag", "https://gitlab.com/user", "//github.com/user"):
            with self.subTest(url=url):
                self.assertIsNone(_safe_provider_url(url, "github"))
        self.assertEqual(_safe_provider_url("https://github.com/gpshead", "github"), "https://github.com/gpshead")


class ReviewChecks(unittest.TestCase):
    def test_comment_preserves_approval_and_profile(self):
        rows, truncated, _ = decisions([review("APPROVED"), review("COMMENTED", 2)])
        self.assertFalse(truncated)
        self.assertEqual(rows[0].state, "APPROVED")
        self.assertEqual(rows[0].opinionState, "COMMENTED")
        self.assertTrue(rows[0].appliesToMerge)
        self.assertEqual(rows[0].reviewer.profileUrl, "https://github.com/reviewer")

    def test_dismissal_and_changes_request_revoke_approval(self):
        for state in ("DISMISSED", "CHANGES_REQUESTED"):
            rows, _, _ = decisions([review("APPROVED"), review(state, 2), review("COMMENTED", 3)])
            self.assertEqual(rows[0].state, state)

    def test_incomplete_wrong_head_and_unmerged_do_not_prove_approval(self):
        for kwargs in ({"incomplete": True}, {"head": "b" * 40}, {"merged": None}):
            rows, _, _ = decisions([review("APPROVED")], **kwargs)
            self.assertEqual(rows[0].state, "APPROVED")
            self.assertFalse(rows[0].appliesToMerge)
            self.assertTrue(rows[0].limitation)

    def test_after_merge_event_does_not_overwrite_decision(self):
        rows, _, _ = decisions([review("APPROVED"), review("DISMISSED", 4)])
        self.assertEqual(rows[0].state, "APPROVED")
        self.assertTrue(rows[0].appliesToMerge)

    def test_multiple_reviewers_are_independent_and_bounded(self):
        rows, truncated, _ = decisions([review("APPROVED", login=f"user{i}") for i in range(21)])
        self.assertEqual(len(rows), 20)
        self.assertTrue(truncated)
        self.assertTrue(all(row.appliesToMerge for row in rows))

    def test_comment_alone_does_not_approve(self):
        rows, _, _ = decisions([review("COMMENTED")])
        self.assertEqual(rows[0].state, "NONE")
        self.assertFalse(rows[0].appliesToMerge)


if __name__ == "__main__":
    unittest.main(verbosity=2)
