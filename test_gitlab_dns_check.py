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
        self.assertEqual(self.check({"example.com": GITLAB}), [])

    def test_changed_address(self):
        stale = {"A": {"35.185.44.1"}, "AAAA": GITLAB["AAAA"]}
        self.assertEqual(self.check({"example.com": stale}),
                         ["example.com A: 35.185.44.1 (GitLab Pages: 35.185.44.232)"])

    def test_domain_lookup_failure(self):
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        [problem] = self.check({"example.com": error})
        self.assertIn("example.com: lookup failed", problem)

    def test_invalid_name_does_not_stop_later_domains(self):
        # The socket module rejects malformed names with UnicodeError.
        error = UnicodeError("label empty or too long")
        stale = {"A": {"35.185.44.1"}, "AAAA": GITLAB["AAAA"]}
        invalid, later = self.check({"a..b.pl": error, "example.com": stale},
                                    domains=["a..b.pl", "example.com"])
        self.assertIn("a..b.pl: lookup failed", invalid)
        self.assertIn("example.com A: 35.185.44.1", later)

    def test_reference_lookup_failure(self):
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        [problem] = self.check({g.REFERENCE_HOST: error, "example.com": GITLAB})
        self.assertIn(f"{g.REFERENCE_HOST}: lookup failed", problem)

    def test_reference_without_address(self):
        empty = {"A": set(), "AAAA": set()}
        [problem] = self.check({g.REFERENCE_HOST: empty, "example.com": empty})
        self.assertIn(f"{g.REFERENCE_HOST}: no A record", problem)


class SendMailTest(unittest.TestCase):
    def test_pipes_message_to_sendmail(self):
        with mock.patch("subprocess.run") as run:
            g.send_mail("Subject", "Body\n")
        self.assertEqual(run.call_args.args[0][0], g.SENDMAIL)
        self.assertIn("To: root", run.call_args.kwargs["input"].decode())


class MainTest(unittest.TestCase):
    def run_main(self, problems):
        log = io.StringIO()
        with mock.patch.object(g, "read_domains", return_value=["example.com"]), \
             mock.patch.object(g, "check", return_value=problems), \
             mock.patch.object(g, "send_mail") as send_mail, \
             mock.patch("sys.argv", ["gitlab_dns_check.py"]), \
             redirect_stdout(log):
            status = g.main()
        return status, log.getvalue(), send_mail

    def test_ok_logs_one_line_and_sends_no_mail(self):
        status, log, send_mail = self.run_main([])
        self.assertEqual(status, 0)
        self.assertEqual(len(log.splitlines()), 1)
        self.assertIn(" OK: example.com match ", log)
        send_mail.assert_not_called()

    def test_failure_warns_and_mails(self):
        status, log, send_mail = self.run_main(["example.com A: broken"])
        self.assertEqual(status, 1)
        self.assertIn("WARNING: GitLab Pages DNS check FAILED", log)
        self.assertIn("!!! example.com A: broken", log)
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.args[1], "example.com A: broken\n")


if __name__ == "__main__":
    unittest.main()
