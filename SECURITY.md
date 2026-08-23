# Security

## Reporting a vulnerability

Please report security issues privately, through
[GitHub's private vulnerability reporting](https://github.com/Nappuccino-tlg/linkly/security/advisories/new),
rather than opening a public issue.

Include what you did, what happened, and what you expected. A request that reproduces it
is worth more than a description of it. You will get an acknowledgement within a few days.

## What this project defends against

A URL shortener is an open redirector with a database attached, so a few of these are
load-bearing rather than decorative.

| Concern | Where it is handled |
|---|---|
| Redirects into a private network, including cloud metadata at `169.254.169.254` | [`app/urlguard.py`](app/urlguard.py) |
| `http://apple.com@evil.example` — a URL that reads as one host and resolves to another | [`app/urlguard.py`](app/urlguard.py) |
| Vanity codes shadowing real routes such as `/docs` | [`app/shortcode.py`](app/shortcode.py) |
| Password guessing | Per-IP and per-email throttling in [`app/routers/auth.py`](app/routers/auth.py) |
| A forged `X-Forwarded-For` buying unlimited quota | [`app/deps.py`](app/deps.py), off unless `TRUSTED_PROXY_HOPS` says otherwise |
| Forgeable tokens, or reversible visitor hashes, from a shipped default | Startup check in [`app/config.py`](app/config.py) when `ENVIRONMENT=production` |
| Storing visitor IP addresses | Never stored; a salted SHA-256 is, and only to count. IPv4 is small, so the salt is treated as a secret |
| A stored `target_url` being sniffed as HTML | `X-Content-Type-Options: nosniff` on every response |

## What it deliberately does not defend against

Knowing the edges matters as much as the guards, so these are stated rather than implied:

- **A hostname that resolves somewhere private.** `urlguard` refuses IP literals and
  obvious internal suffixes without doing any DNS lookup — deliberately, so the guard
  itself cannot be turned into a way to make the server issue outbound requests. Catching
  `evil.example` when it resolves to `127.0.0.1` needs resolve-then-pin at redirect time.
- **Phishing through a legitimate-looking target.** Any shortener can point at anything a
  browser will load. There is no reputation feed here.
- **Bursts across a rate-limit window boundary.** The limiter is a fixed window, so a
  burst straddling the boundary can pass up to twice the limit. See
  [`app/ratelimit.py`](app/ratelimit.py) for why a sliding window is not worth it at this
  size.
- **Enumeration of short codes.** Codes are 7 random base62 characters and are not secret.
  A short link is not an access-control mechanism.

## Supported versions

`main` is the supported version. This is a personal project, not a product with a release
train behind it.
