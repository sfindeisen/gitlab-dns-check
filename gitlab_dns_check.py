#!/usr/bin/env python3
"""Check that domains with hardcoded GitLab Pages addresses still match GitLab.

An apex domain served by GitLab Pages needs A and AAAA records holding
GitLab's literal addresses, which go stale silently if GitLab moves Pages.
This compares each listed domain with a *.gitlab.io name, which GitLab keeps
up to date, logs one line when all match and mails root when any does not.
"""

import argparse
import datetime
import os
import socket
import subprocess
import sys
from email.message import EmailMessage

# Every *.gitlab.io name resolves to the current GitLab Pages addresses.
REFERENCE_HOST = "gitlab-org.gitlab.io"
DEFAULT_DOMAINS_FILE = os.path.expanduser("~/.config/gitlab-dns-check/domains")
MAIL_TO = "root"
# Absolute path, because cron's default PATH does not include /usr/sbin.
SENDMAIL = "/usr/sbin/sendmail"
FAMILIES = {"A": socket.AF_INET, "AAAA": socket.AF_INET6}
# Lookup errors meaning "no such records", as opposed to a failed lookup.
NO_RECORDS = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}


def read_domains(path):
    """Return the domains listed in path, one per line; '#' starts a comment."""
    with open(path, encoding="utf-8") as f:
        lines = (line.split("#", 1)[0].strip() for line in f)
        return [line for line in lines if line]


def resolve(host):
    """Return {"A": addresses, "AAAA": addresses} for host."""
    result = {}
    for rtype, family in FAMILIES.items():
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
        except socket.gaierror as e:
            if e.errno not in NO_RECORDS:
                raise
            infos = []
        result[rtype] = {info[4][0] for info in infos}
    return result


def fmt(addresses):
    return " ".join(sorted(addresses)) or "none"


def check(domains):
    """Return a list of problems, empty when every domain matches GitLab."""
    try:
        expected = resolve(REFERENCE_HOST)
    except OSError as e:
        return [f"{REFERENCE_HOST}: lookup failed: {e}"]
    if not expected["A"]:
        return [f"{REFERENCE_HOST}: no A record, cannot tell GitLab's address"]
    problems = []
    for domain in domains:
        try:
            actual = resolve(domain)
        except OSError as e:
            problems.append(f"{domain}: lookup failed: {e}")
            continue
        for rtype, addresses in expected.items():
            if actual[rtype] != addresses:
                problems.append(f"{domain} {rtype}: {fmt(actual[rtype])} "
                                f"(GitLab Pages: {fmt(addresses)})")
    return problems


def send_mail(subject, body):
    msg = EmailMessage()
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    subprocess.run([SENDMAIL, "-oi", "-t"], input=msg.as_bytes(), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("domains_file", nargs="?", default=DEFAULT_DOMAINS_FILE,
                        help=f"one domain per line (default: {DEFAULT_DOMAINS_FILE})")
    args = parser.parse_args()
    domains = read_domains(args.domains_file)
    problems = check(domains)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if not problems:
        print(f"{now} OK: {' '.join(domains)} match {REFERENCE_HOST}")
        return 0
    banner = "!" * 72
    lines = [f"{now} WARNING: GitLab Pages DNS check FAILED", *problems]
    # Flush so the warning reaches the log even if sending mail fails below.
    print(banner, *("!!! " + line for line in lines), banner, sep="\n", flush=True)
    send_mail(f"GitLab Pages DNS check FAILED on {socket.gethostname()}",
              "\n".join(problems) + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
