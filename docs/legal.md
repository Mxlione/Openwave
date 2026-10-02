# Scope of use and legal considerations

OpenWave is a receiver. It listens; it never transmits.

This document explains what the project is for, what it deliberately will not do, and what you
should check before using it. **It is not legal advice.** Radio regulation is national, and the
rules where you are may differ from the rules anywhere else.

## What OpenWave is for

Receiving **broadcasts that are transmitted in the clear, for free, to the general public**:

- FM radio in the 87.5–108 MHz broadcast band
- Free-to-air digital terrestrial television (DVB-T / DVB-T2)
- The data carried alongside those broadcasts for the public: RDS station names and RadioText,
  DVB service information tables

These signals are sent out specifically so that anyone with a receiver can pick them up. Measuring
them, listing them and playing them is what the transmitters exist for.

## What OpenWave will not do

The following are out of scope, and contributions implementing them will be declined regardless of
how good the code is:

- **Breaking conditional access or encryption.** No CAM emulation, no key sharing, no card
  sharing, no descrambling of pay-TV or any scrambled service. OpenWave reports a service as
  `scrambled` and stops there.
- **Circumventing paid services** in any form.
- **Decoding private or protected communications.** Not emergency services, not encrypted
  trunked radio, not anything a reasonable person would understand as private.
- **Transmitting.** OpenWave has no transmit path and will not gain one. Transmitting without a
  licence is illegal almost everywhere and can interfere with services people depend on.

## What you should check before using it

Reception law varies far more than people expect:

- **Most countries** allow anyone to receive public broadcast radio and television freely. This is
  the normal case, and it is what OpenWave targets.
- **Some countries** restrict receiving, recording or disclosing transmissions that are not
  intended for the general public — even when the signal is unencrypted and trivially receivable.
  Owning a wideband receiver can itself be regulated.
- **A few countries** require a licence or registration to own certain receiving equipment, or a
  broadcast-reception licence fee to watch television.

A wideband SDR can tune far outside the broadcast bands. OpenWave restricts its own scanning to
broadcast plans, but the hardware does not know that. **Where you point it is your
responsibility.**

If you are unsure, your national telecommunications regulator is the authority to ask.

## Recording and redistribution

Receiving a broadcast and redistributing it are different things. A programme you can lawfully
listen to is still covered by copyright and by the broadcaster's rights. OpenWave plays streams
locally; it is not a tool for rebroadcasting, and using it that way is on you.

## Sharing IQ captures

Contributors are encouraged to share short IQ recordings of the broadcast bands so that others can
develop and test without hardware. When you do:

- Record **broadcast bands only**, never anything outside them.
- Keep captures short — a few seconds is enough for a detection test.
- Say where and when it was recorded, the centre frequency and the sample rate.
- Do not include anything that identifies a private individual.

## Interference

Receiving is passive, but cheap SDR dongles do radiate a little, and a badly placed one can
interfere with nearby equipment. If something in your home stops working properly when the dongle
is plugged in, that is the cause — move it, shield it, or use a better cable.

## Reporting a scope problem

If you believe something in OpenWave crosses one of the lines above, open an issue, or report it
privately through
[GitHub Security Advisories](https://github.com/Mxlione/Openwave/security/advisories/new) if you
would rather not discuss it in public. It will be taken seriously.
