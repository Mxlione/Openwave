# Announcement drafts

Three versions of the same thing, for three audiences. Nothing here has been posted anywhere —
posting is yours to do, from your own account, when you are ready.

One thing worth keeping in all of them: **say that the hardware drivers are unverified.** It is
the most interesting thing about the project and the reason somebody should bother replying. A
post that hides it gets a round of stars and no reports; a post that leads with it gets people
who own dongles.

---

## r/RTLSDR

> **Title:** I wrote an FM and DVB-T scanner in Python and have never owned an SDR. It needs
> somebody who has.

I have spent the last while building OpenWave: it scans the FM band, finds the stations actually
transmitting, decodes RDS for their names and RadioText, demodulates stereo, scans DVB-T
multiplexes and reads their tables to list services and channel numbers. There is an HTTP API, a
web interface with a live spectrum and waterfall, and playback through libVLC.

I do not own a receiver. Not one.

So the whole thing is built against simulated receivers that synthesise real signals: FM with
proper stereo multiplexes, an RDS encoder with correct check words, MPEG transport streams with
correct CRCs. The noise is reproducible from a seed and a position, so any failure comes back
identically.

That approach found six bugs that all look fine in code review:

- Peak-picking found 97.967 MHz for a station at 98.0, because wideband FM suppresses its own
  carrier. Channel-power integration fixed it.
- A median noise floor was 43 dB wrong at 67 % band occupancy. The lower quartile is 0.2 dB out.
- Non-maximum suppression dropped a real station in favour of the spill from its neighbour.
- The multiplex sample rate was too low by Carson's rule, and the clipping made a mono station
  read as stereo.
- A 129-tap low-pass left the 19 kHz pilot completely untouched.
- The RDS sync fabricated 22 groups out of pure noise, because about a third of random syndromes
  map to a correctable burst.

**The RTL-SDR driver has never run on an RTL-SDR, and the Linux DVB driver has never run on a
tuner.** Both are labelled as unverified everywhere they appear, including in the API's device
list. The DVB one worries me most: it lays out kernel structures byte by byte from Python, and a
field in the wrong place does not raise an error, it tunes somewhere else.

If you have a dongle and ten minutes, there are two issues waiting with exactly what to run and
what to report. A crash is a result. "It tuned 8 MHz low" would be the most useful sentence
anybody could send me.

MIT. Python, numpy/scipy, FastAPI, Angular.

<link>

---

## r/opensource · r/Python

> **Title:** OpenWave — a radio scanner built entirely against simulated hardware, and what that
> caught

OpenWave scans FM and DVB-T, decodes RDS, demodulates stereo, and serves it all over an HTTP API
with an Angular interface.

The constraint it was built under is the interesting part: I had no receiver, so simulated ones
had to be good enough to develop against. That meant synthesising signals properly — real stereo
multiplexes, RDS with correct check words, transport streams with correct CRCs — and making the
noise reproducible from a seed and a sample position, so one read of 4096 samples is
bit-identical to two reads of 2048.

What I did not expect was how much that caught. Six substantive DSP bugs, every one of which
reads as correct code: a peak finder that found the wrong frequency because wideband FM
suppresses its own carrier, a median noise floor that was 43 dB wrong on a busy band, a filter
that was 129 taps short of doing anything, and an RDS sync that happily produced 22 groups from
pure noise.

1200-odd tests, around 89 % coverage, and the parts that have never touched hardware say so in
the README, in the docs, and in the API response.

MIT, contributions welcome, and there is a list of starter issues with the files and the
acceptance criteria already written down.

<link>

---

## Hacker News

> **Title:** Show HN: OpenWave – an SDR scanner written without ever owning an SDR

Lead with the constraint, keep it short, and let the comments ask. Suggested text:

I built an FM and DVB-T scanner — band scanning, RDS decoding, stereo demodulation, DVB-T
service discovery, an HTTP API, and a web interface with a live spectrum — without owning a
receiver.

Everything is developed against simulated receivers that synthesise real signals, with
reproducible noise addressable by sample position. That turned out to catch six real DSP bugs
that all look correct in review: peak-picking on a wideband FM carrier finds the deviation edge
rather than the centre; a median noise floor is 43 dB wrong at high occupancy; an RDS
synchroniser will fabricate groups from noise because a third of random syndromes map to a
correctable error burst.

The RTL-SDR and Linux DVB drivers have never been run on hardware, and everything says so,
including the API. If you own a dongle I would rather have a crash report than a star.

<link>

---

## Where else

- **Mastodon / Bluesky**, hashtags `#rtlsdr #sdr #radio #opensource`. Short form: the title line
  of the Hacker News post plus the link.
- **The RTL-SDR Blog** takes submissions and covers projects like this. Their contact form.
- **r/amateurradio** only if you frame it as broadcast reception, since it is a receive-only
  project and not an amateur one.

## Before posting

- Set the repository topics (see `.github/TOPICS.txt`).
- Run the bootstrap workflow so the starter issues exist before anybody arrives to read them.
- Check the CI badge is green on the front page.
- Decide whether you can reply for a day or two. A Show HN with no answers in the thread does
  worse than no Show HN.
