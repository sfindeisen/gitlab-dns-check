# gitlab-dns-check

An apex domain served by GitLab Pages needs A and AAAA records holding
GitLab's literal addresses. If GitLab ever moves Pages, those records go
stale without notice. `gitlab_dns_check.py` compares the addresses of each
listed domain with those of `gitlab-org.gitlab.io` (every `*.gitlab.io` name
resolves to the current Pages addresses) and:

- logs a single `OK` line when all domains match;
- logs a large `!!!` warning and mails `root` when any domain differs or a
  lookup fails. A failed lookup counts as a failure, so the check never
  reports an unverifiable state as fine.

It needs Python 3.8 or later (standard library only) and a local mail
transfer agent providing `/usr/sbin/sendmail`.

## Installation

The commands assume the repository is in `~/gitlab-dns-check`.

    mkdir -p ~/.config/gitlab-dns-check
    cp ~/gitlab-dns-check/domains.example ~/.config/gitlab-dns-check/domains
    ~/gitlab-dns-check/gitlab_dns_check.py

The last command should print a single `OK` line.

The domain list has one domain per line; `#` starts a comment.

To test the alert path, check a domain that is not on GitLab Pages. This
prints the warning and mails `root`:

    ~/gitlab-dns-check/gitlab_dns_check.py <(echo example.com)

## Running hourly from cron

Run `crontab -e` and add:

    47 * * * * $HOME/gitlab-dns-check/gitlab_dns_check.py >> $HOME/gitlab-dns-check/log/cron_$(date +\%F).log 2>&1

The `log/` directory is part of the repository, so the redirection works
right after cloning. A mismatch mails `root` on every run until it is fixed.

## Tests

    cd ~/gitlab-dns-check && python3 -m unittest
