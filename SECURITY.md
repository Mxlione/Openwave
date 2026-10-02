# Security Policy

## Supported versions

OpenWave is pre-alpha. No version is released yet, so only the `main` branch receives fixes.

| Version | Supported |
|---|---|
| `main` | ✅ |
| anything else | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it through GitHub's private channel:
[Security → Report a vulnerability](https://github.com/Mxlione/Openwave/security/advisories/new).

Please include:

- what the problem is and what an attacker could do with it
- the steps or input needed to reproduce it
- the affected file or module, if you know it
- your environment (OS, Python version, receiver if relevant)

You can expect a first reply within **7 days**. Since this is a volunteer project, a fix may take
longer; you will be kept informed, and credited in the advisory unless you ask otherwise.

## What counts as a vulnerability here

OpenWave parses data that comes from the air — IQ samples, RDS groups, MPEG-TS sections — all of
which are untrusted input controlled by whoever is transmitting. Parsing bugs matter:

- memory or resource exhaustion from a malformed transport stream or RDS group
- command injection through station or service names reaching FFmpeg, libVLC or a shell
- path traversal through a name used to build a filename
- the local API binding to a public interface or exposing the filesystem

## What does not

- Receiving a broadcast you are not licensed to receive in your country — that is a legal question,
  not a vulnerability. See [docs/legal.md](docs/legal.md).
- Crashes caused by hardware that is absent, busy or unsupported — open a normal issue.
