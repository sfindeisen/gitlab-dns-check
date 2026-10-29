import io
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import gitlab_dns_check as g

GITLAB = {"A": {"35.185.44.232"}, "AAAA": {"2600:1901:0:7b8a::"}}


def fake_resolve(table):
    def resolve(host):
        if isinstance(table[host], Exception):
            raise table[host]
        return table[host]
    return resolve


class ReadDomainsTest(unittest.TestCase):
    def test_skips_comments_and_blank_lines(self):
        with tempfile.NamedTemporaryFile("w") as f:
            f.write("# header\neisenbits.com\n\n  mimalumni.pl  # club\n")
            f.flush()
            self.assertEqual(g.read_domains(f.name),
                             ["eisenbits.com", "mimalumni.pl"])


class ResolveTest(unittest.TestCase):
    def test_missing_records_give_empty_set(self):
        def getaddrinfo(host, port, family, type):
            if family == socket.AF_INET6:
                raise socket.gaierror(socket.EAI_NONAME, "no records")
            return [(family, type, 6, "", ("35.185.44.232", 0))]
        with mock.patch("socket.getaddrinfo", getaddrinfo):
            self.assertEqual(g.resolve("example.com"),
                             {"A": {"35.185.44.232"}, "AAAA": set()})

    def test_failed_lookup_raises(self):
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        with mock.patch("socket.getaddrinfo", side_effect=error):
            with self.assertRaises(socket.gaierror):
                g.resolve("example.com")


class CheckTest(unittest.TestCase):
    def check(self, table, domains=("example.com",)):
        table = {g.REFERENCE_HOST: GITLAB, **table}
        with mock.patch.object(g, "resolve", fake_resolve(table)):
            return g.check(domains)

    def test_matching_domain(self):
        self.assertEqual(self.check({"example.com": GITLAB}),
                         {"example.com": None})

    def test_changed_address(self):
        stale = {"A": {"35.185.44.1"}, "AAAA": GITLAB["AAAA"]}
        self.assertEqual(self.check({"example.com": stale}),
                         {"example.com": "A 35.185.44.1 (GitLab Pages: 35.185.44.232)"})

    def test_all_wrong_records_in_one_problem(self):
        empty = {"A": set(), "AAAA": set()}
        self.assertEqual(self.check({"example.com": empty})["example.com"],
                         "A none (GitLab Pages: 35.185.44.232); "
                         "AAAA none (GitLab Pages: 2600:1901:0:7b8a::)")

    def test_domain_lookup_failure(self):
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        problem = self.check({"example.com": error})["example.com"]
        self.assertIn("lookup failed", problem)

    def test_every_domain_reported_in_order(self):
        stale = {"A": {"35.185.44.1"}, "AAAA": GITLAB["AAAA"]}
        result = self.check({"stale.example": stale, "example.com": GITLAB},
                            domains=["stale.example", "example.com"])
        self.assertEqual(list(result), ["stale.example", "example.com"])
        self.assertIn("A 35.185.44.1", result["stale.example"])
        self.assertIsNone(result["example.com"])

    def test_invalid_name_does_not_stop_later_domains(self):
        # The socket module rejects malformed names with UnicodeError.
        error = UnicodeError("label empty or too long")
        result = self.check({"a..b.pl": error, "example.com": GITLAB},
                            domains=["a..b.pl", "example.com"])
        self.assertIn("lookup failed", result["a..b.pl"])
        self.assertIsNone(result["example.com"])

    def test_reference_lookup_failure_fails_every_domain(self):
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        result = self.check({g.REFERENCE_HOST: error},
                            domains=["a.example", "b.example"])
        self.assertEqual(list(result), ["a.example", "b.example"])
        for problem in result.values():
            self.assertIn(f"{g.REFERENCE_HOST}: lookup failed", problem)

    def test_reference_without_address(self):
        empty = {"A": set(), "AAAA": set()}
        problem = self.check({g.REFERENCE_HOST: empty})["example.com"]
        self.assertIn(f"{g.REFERENCE_HOST}: no A record", problem)


class SendMailTest(unittest.TestCase):
    def test_pipes_message_to_sendmail(self):
        with mock.patch("subprocess.run") as run:
            g.send_mail("Subject", "Body\n")
        self.assertEqual(run.call_args.args[0][0], g.SENDMAIL)
        self.assertIn("To: root", run.call_args.kwargs["input"].decode())


class MainTest(unittest.TestCase):
    def run_main(self, results):
        log = io.StringIO()
        with mock.patch.object(g, "read_domains", return_value=list(results)), \
             mock.patch.object(g, "check", return_value=results), \
             mock.patch.object(g, "send_mail") as send_mail, \
             mock.patch("sys.argv", ["gitlab_dns_check.py"]), \
             redirect_stdout(log):
            status = g.main()
        # Drop the timestamp that starts every per-domain line.
        lines = [line.split(" ", 1)[-1] for line in log.getvalue().splitlines()]
        return status, lines, send_mail

    def test_ok_line_per_domain_and_no_mail(self):
        status, lines, send_mail = self.run_main({"a.example": None,
                                                  "b.example": None})
        self.assertEqual(status, 0)
        self.assertEqual(lines, ["OK a.example", "OK b.example"])
        send_mail.assert_not_called()

    def test_error_line_per_failed_domain_warns_and_mails(self):
        status, lines, send_mail = self.run_main({"a.example": None,
                                                  "b.example": "A broken"})
        self.assertEqual(status, 1)
        self.assertEqual(lines[:2], ["OK a.example", "ERROR b.example: A broken"])
        self.assertIn("WARNING: GitLab Pages DNS check FAILED", "\n".join(lines[2:]))
        send_mail.assert_called_once()
        body = send_mail.call_args.args[1]
        self.assertTrue(body.endswith(" ERROR b.example: A broken\n"))
        self.assertNotIn("a.example", body)


if __name__ == "__main__":
    unittest.main()
