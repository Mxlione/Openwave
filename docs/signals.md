# Signals

The radio engineering behind OpenWave, and the places where the obvious approach is wrong. Most
of what follows was found by measuring against the simulated receivers rather than by reasoning
about it, which is the argument for having them.

## Why the FM scan measures channel power, not spectral peaks

The obvious way to find FM stations is to compute a spectrum and look for peaks. It does not
work.

Wideband FM with 75 kHz deviation has a modulation index around 75 for a 1 kHz tone. At that
index the carrier is almost entirely suppressed, and the energy piles up at the two extremes of
the deviation, where the instantaneous frequency dwells longest. A station at 98.0 MHz therefore
produces spectral peaks near 97.93 and 98.07 MHz and little at 98.0 MHz. Peak picking finds the
skirts of the signal and reports a station that is not there, twice, while missing the one that
is.

What works is integrating power across the channel:

1. Walk the 100 kHz raster across the band.
2. For each candidate channel, integrate the power spectral density over the 200 kHz channel
   bandwidth.
3. Compare that with the noise floor.
4. Keep the strongest, suppressing everything within a channel bandwidth of it as its spill.

Measured against the simulator: a station at 98.0 MHz comes out 57.4 dB above the noise, its
neighbours at 97.9 and 98.1 MHz come out at 54.4 dB, and an empty channel at 98.5 MHz comes out
at −0.1 dB.

## Why the noise floor is the lower quartile, not the median

The median is the obvious robust estimator, and it is right for a sparsely occupied band. But a
single 2.4 MHz window of the FM band can hold several active channels, and once more than half a
window is occupied the median stops measuring noise and starts measuring stations.

Measured on a window two thirds occupied: the lower quartile is within 0.2 dB of the true floor,
the median is 43 dB out, and the upper quartile is so far out that the scan finds nothing at all.

The cost is that the quartile reads a decibel or two below the true floor everywhere — a uniform
optimism a detection threshold absorbs.

## Why suppression is strongest-first

Having found which channels are above the threshold, the question is which of them are stations
and which are a neighbour's spill. The obvious answer is to keep local maxima: a channel
survives if nothing stronger sits within a channel bandwidth.

That is wrong when the stronger neighbour is itself about to be discarded. Measured on a band
with stations every 300 kHz: the real station at 98.0 MHz lost to the spill channel at 98.2 MHz,
which was then itself dropped in favour of the real station at 98.3 MHz. One transmitter
eliminated by another that did not exist.

Accepting in descending order of strength, and suppressing only against detections already
accepted, finds all of them.

## Why the demodulator samples at 480 kHz, not 240 kHz

The stereo multiplex reaches 60 kHz, so Nyquist says 120 kHz and 240 kHz looks generous. It is
not, because the number to size against is not the multiplex but the *modulated signal carrying
it*. By Carson's rule a stereo transmission at full deviation occupies 2 × (75 + 60) = 270 kHz.

Decimating to 240 kHz clips the outer FM sidebands, and the clipping puts distortion products
across the recovered multiplex. Measured: a mono station modulated by a 1 kHz tone at full
deviation showed 62.8 dB of energy at 19 kHz and was confidently reported as stereo. At 480 kHz
the same signal shows 3.6 dB, against 63.8 dB for a real pilot.

## Why filter lengths are derived, not chosen

A low-pass at 15 kHz built with an arbitrary 129 taps has a transition region about 6 kHz wide at
480 kHz — which puts the 19 kHz stereo pilot inside it. The filter meant to remove the pilot
barely touched it, and the "mono" output carried it at full strength as a whistle at the edge of
hearing.

Filter lengths now come from the Kaiser design formula: state the transition width and the
attenuation wanted, and the length follows. The same filter designed properly rejects the pilot
by over 100 dB.

## Why RDS phase is recovered from the data, not the pilot

The RDS subcarrier is sent with its carrier suppressed, so it arrives rotated by an unknown
angle. The textbook fix is to lock onto the 19 kHz stereo pilot, whose third harmonic the
standard ties the subcarrier to.

That works only for stereo stations, and a mono station carries RDS perfectly well. So the phase
is recovered from the data instead: the data is real, so its samples lie along a line through the
origin; squaring them removes the sign and the average of the squares points along twice the
angle. Half of that argument is the angle, give or take a half turn — which the differential
encoding was there to absorb anyway. Verified on a mono transmission with no pilot at all.

## Why RDS synchronisation is acquired on clean blocks only

An RDS block's check word repairs a burst of up to five consecutive wrong bits, which is the
shape reception errors actually take. That means roughly a third of all possible syndromes map to
some correctable error.

So a decoder willing to repair while *searching* for block boundaries will repair noise into four
plausible blocks in a row and report a group nobody sent. Measured on pure noise before the fix:
22 groups, every one invented, with the station's identity different in each.

Synchronisation is therefore acquired only on four consecutive undamaged blocks, and repair is
allowed only once the boundary is established.

## Why the blocks of a continuous demodulation overlap

Live listening demodulates in blocks of about a tenth of a second. Filters have an edge
transient, so each block is demodulated together with the tail of the one before it and only the
newest stretch of output is emitted.

Measured as the ratio between the worst sample-to-sample step at a block join and the typical
step elsewhere: with no overlap, 8.3 — an audible tick nine times a second. With overlap, 1.5,
which is the signal's own roughness.

How much output to discard cannot be computed from a ratio. The chain decimates by an integer
factor, the differentiator consumes one sample, and the resampler rounds up, so
`round(overlap × audio_rate / input_rate)` comes out a sample short and leaves a repeat at every
join. The listener measures how much audio one block alone produces, and from then on emits
exactly that much.

## Why the output level comes from the deviation

A demodulator produces hertz of deviation, a number in the tens of thousands, and playback wants
values between −1 and 1. Scaling each block by its own loudest sample fills the range best — and
makes the volume breathe in time with the blocks. The level comes from the deviation instead,
which is a constant of FM broadcasting rather than a property of this block.

## Why MPEG's CRC-32 is not the familiar one

PSI tables end with a CRC-32 that uses the same polynomial as the one in zip files, but feeds the
bits in the other order, starts from all ones, and does not invert the result. Using
`zlib.crc32` rejects every real transmission.

## Why a transport stream parser treats every length as hostile

A transport stream arrives off the air, from whoever is transmitting, through a demodulator that
may have failed to repair it. An adaptation field can claim to run past the end of its packet, a
section pointer can point outside its payload, and a section can claim to be longer than the
standard permits. A parser that believes any of them can be made to read past the end of a
buffer or to allocate without bound. All three are checked.

## Why the simulated receivers are not a compromise

Every finding above came from measuring against a synthesised signal whose content was known in
advance. None would have been caught by code review: each is a case where the obvious
implementation looks correct and produces confidently wrong numbers.

What simulation cannot do is also worth stating. A synthetic signal has no multipath, no
adjacent-channel splatter, no tuner nonlinearity and no drifting oscillator. Code that passes
against it can still fail on a rooftop antenna — which is what [hardware
reports](hardware.md) are for.
