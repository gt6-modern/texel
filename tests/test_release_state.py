import json
import unittest
from pathlib import Path

from tools.release_state import ReleaseStateError, decide, parse_included_response


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "release-http"


class IncludedResponseTest(unittest.TestCase):
    def test_parses_real_200_fixture(self):
        response = parse_included_response((FIXTURES / "release-200.txt").read_bytes())
        self.assertEqual(200, response.status)
        self.assertEqual("application/json; charset=utf-8", response.headers["content-type"])
        self.assertEqual("v0.1.0", json.loads(response.body)["tag_name"])

    def test_parses_real_404_fixture(self):
        response = parse_included_response((FIXTURES / "release-404.txt").read_bytes())
        self.assertEqual(404, response.status)
        self.assertEqual("404", json.loads(response.body)["status"])

    def test_rejects_missing_status_line(self):
        with self.assertRaises(ReleaseStateError):
            parse_included_response(b"Content-Type: application/json\n\n{}")

    def test_rejects_truncated_header(self):
        with self.assertRaises(ReleaseStateError):
            parse_included_response(b"HTTP/2.0 200 OK\nContent-Type\n\n{}")

    def test_rejects_missing_header_body_separator(self):
        with self.assertRaises(ReleaseStateError):
            parse_included_response(b"HTTP/2.0 200 OK\nContent-Type: application/json")

    def test_rejects_empty_body(self):
        with self.assertRaises(ReleaseStateError):
            parse_included_response(b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n")

    def test_crlf_and_lf_responses_are_equivalent(self):
        raw = (FIXTURES / "release-404.txt").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(parse_included_response(raw),
                         parse_included_response(raw.replace(b"\n", b"\r\n")))

    def test_rejects_ambiguous_or_invalid_headers(self):
        for headers in [b"X-RateLimit-Remaining: 0\nx-ratelimit-remaining: 42",
                        b"Bad Name: value", b"\xff: value"]:
            with self.subTest(headers=headers), self.assertRaises(ReleaseStateError):
                parse_included_response(b"HTTP/2.0 200 OK\n" + headers + b"\n\n{}")

    def test_rejects_empty_or_whitespace_response(self):
        for raw in [b"", b" ", b"\r\n", b"\n\n"]:
            with self.subTest(raw=raw), self.assertRaises(ReleaseStateError):
                parse_included_response(raw)

    def test_rejects_duplicate_json_state_fields(self):
        response = parse_included_response(
            b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n"
            b'{"id":1,"tag_name":"v0.2.0","draft":false,"draft":true,"assets":[]}')
        with self.assertRaisesRegex(ReleaseStateError, "duplicate"):
            decide(response, "v0.2.0", "a" * 40, "a" * 40)

    def test_identifies_primary_rate_limit_from_headers(self):
        raw = (b"HTTP/2.0 403 Forbidden\nX-RateLimit-Remaining: 0\n"
               b"X-RateLimit-Reset: 1795188354\nContent-Type: application/json\n\n"
               b'{"message":"API rate limit exceeded"}')
        response = parse_included_response(raw)
        with self.assertRaisesRegex(ReleaseStateError, "rate limit"):
            decide(response, "v0.1.0", "a" * 40, "a" * 40)

    def test_identifies_secondary_rate_limit(self):
        response = parse_included_response(
            b"HTTP/2.0 403 Forbidden\nRetry-After: 60\n"
            b"Content-Type: application/json\n\n"
            b'{"message":"You have exceeded a secondary rate limit."}')
        with self.assertRaisesRegex(ReleaseStateError, "secondary rate limit"):
            decide(response, "v0.1.0", "a" * 40, "a" * 40)


class ReleaseDecisionTest(unittest.TestCase):
    def test_missing_creates_uploads_verifies_then_publishes(self):
        response = parse_included_response(
            b"HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n"
            b'{"message":"Not Found","status":"404"}')
        self.assertEqual(
            ["create-draft", "upload", "verify", "publish"],
            decide(response, "v0.2.0", "a" * 40, "a" * 40)["actions"],
        )

    def test_draft_is_deleted_and_rebuilt(self):
        body = {"id": 123, "tag_name": "v0.2.0", "draft": True, "assets": []}
        response = parse_included_response(
            b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n" +
            json.dumps(body).encode("ascii"))
        self.assertEqual(
            ["delete-draft", "create-draft", "upload", "verify", "publish"],
            decide(response, "v0.2.0", "a" * 40, "a" * 40)["actions"],
        )

    def test_published_is_a_noop(self):
        body = {"id": 123, "tag_name": "v0.2.0", "draft": False, "assets": []}
        response = parse_included_response(
            b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n" +
            json.dumps(body).encode("ascii"))
        self.assertEqual([], decide(response, "v0.2.0", "a" * 40, "a" * 40)["actions"])

    def test_rejects_other_http_status(self):
        response = parse_included_response(
            b"HTTP/2.0 403 Forbidden\nContent-Type: application/json\n\n"
            b'{"message":"Resource not accessible"}')
        with self.assertRaises(ReleaseStateError):
            decide(response, "v0.2.0", "a" * 40, "a" * 40)

    def test_rejects_malformed_json_and_tag_mismatch(self):
        malformed = parse_included_response(
            b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n{")
        with self.assertRaises(ReleaseStateError):
            decide(malformed, "v0.2.0", "a" * 40, "a" * 40)
        mismatch = parse_included_response(
            b"HTTP/2.0 200 OK\nContent-Type: application/json\n\n"
            b'{"tag_name":"v0.1.0","draft":false,"assets":[]}')
        with self.assertRaises(ReleaseStateError):
            decide(mismatch, "v0.2.0", "a" * 40, "a" * 40)

    def test_rejects_moved_tag_for_every_release_state(self):
        missing = parse_included_response(
            b"HTTP/2.0 404 Not Found\nContent-Type: application/json\n\n"
            b'{"message":"Not Found","status":"404"}')
        with self.assertRaisesRegex(ReleaseStateError, "commit"):
            decide(missing, "v0.2.0", "a" * 40, "b" * 40)


if __name__ == "__main__":
    unittest.main()
