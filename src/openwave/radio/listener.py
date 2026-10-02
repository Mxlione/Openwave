"""Listening to one FM station, continuously.

A scan is a batch job: read some samples, answer a question, stop. Listening is not. Samples
have to keep arriving, be demodulated, and reach the player without a gap, for as long as
somebody is listening.

:class:`FmListener` runs that as a producer thread feeding a
:class:`~openwave.media.stream.ByteStream`, which a player drains at its own pace. Two details
make the difference between audio and audio with a tick in it every tenth of a second:

**A fixed output level.** Scaling each block by its own loudest sample makes the gain jump from
block to block, which sounds like the volume breathing. The level comes from the deviation
instead, which is a constant of FM broadcasting rather than a property of this block.

**Overlap between blocks, with the discard count taken exactly.** Filters have an edge
transient, so each block is demodulated together with the tail of the one before it, and only
the newest stretch of output is emitted. Demodulating the blocks independently leaves a
sample-to-sample jump at every join eight times larger than the signal's own roughness -- an
audible tick nine times a second. With the overlap in place the joins are indistinguishable
from the rest of the audio. See :data:`DEFAULT_OVERLAP_SAMPLES` for the measurements.

How much output to discard cannot be worked out from a ratio. The chain decimates by an integer
factor, the differentiator consumes one sample, and the resampler rounds up, so
``round(overlap * audio_rate / input_rate)`` comes out a sample short, which leaves a repeated
sample at every join. Instead the listener measures how much audio one block alone produces, on
the first block, and from then on emits exactly that much from the end of each result. That is
exact by construction and does not duplicate the demodulator's internal factors, so it stays
correct if they change.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Final

import numpy as np

from openwave.core.scanner import DEFAULT_SETTLING_SAMPLES
from openwave.core.units import format_frequency
from openwave.media.stream import ByteStream, StreamClosedError
from openwave.media.wav import pcm_bytes
from openwave.radio.constants import FM_DEEMPHASIS_TAU_S, FM_MAX_DEVIATION_HZ
from openwave.radio.fm_demodulator import AUDIO_SAMPLE_RATE_HZ, demodulate
from openwave.radio.station_detector import DC_AVOIDANCE_OFFSET_HZ
from openwave.sdr.device import IqSamples, SdrDevice
from openwave.sdr.errors import DeviceError

#: Samples read from the receiver per block.
#:
#: At 2.4 MS/s this is about 110 ms of audio per block. Smaller blocks mean lower latency and
#: more overhead; larger ones mean the buffer has to be deeper to avoid running dry.
DEFAULT_BLOCK_SAMPLES: Final = 1 << 18

#: Samples carried from one block into the next, to cover the filters' edge transients.
#:
#: Chosen by measurement rather than derived from the filter lengths, because the filters use
#: ``filtfilt``, whose odd-symmetric padding already makes the edge transient small; the overlap
#: only has to cover what is left. Measured on a 1 kHz tone, as the ratio between the worst
#: sample-to-sample step at a block join and the typical step elsewhere:
#:
#: ===========  ======
#: Overlap      Ratio
#: ===========  ======
#: 0            8.3
#: 500          1.9
#: 4096         1.6
#: **8192**     **1.5**
#: 16384        1.9
#: 32768        4.0
#: 65536        1.8
#: ===========  ======
#:
#: A ratio of 1.5 is the signal's own roughness: the largest step a 1 kHz tone can produce is
#: about 1.5 times its median step, so at that point the joins are indistinguishable from the
#: rest of the audio. Zero overlap is a real, audible tick nine times a second.
#:
#: The 32768 result is an outlier that nothing in the reasoning above predicts, and it is why
#: this value is measured rather than argued for. Understanding it is worth an issue.
DEFAULT_OVERLAP_SAMPLES: Final = 1 << 13


@dataclass(frozen=True, slots=True)
class ListenSettings:
    """How to run continuous demodulation.

    Attributes:
        block_samples: Samples read from the receiver per block.
        overlap_samples: Samples carried from one block into the next. Zero demodulates each
            block independently, which is measurably worse at the joins.
        audio_rate_hz: Rate to deliver audio at.
        settling_samples: Samples discarded after tuning, which real hardware needs.
        stereo_threshold_db: Pilot level required before the difference channel is decoded.
        deemphasis_tau_s: De-emphasis time constant. 50 µs in Europe, 75 µs in the Americas.
        reference_deviation_hz: Deviation treated as full scale when setting the output level.
        dc_avoidance_offset_hz: How far from the centre of the window to place the station, to
            keep it off the tuner's own DC spike.
    """

    block_samples: int = DEFAULT_BLOCK_SAMPLES
    overlap_samples: int = DEFAULT_OVERLAP_SAMPLES
    audio_rate_hz: int = int(AUDIO_SAMPLE_RATE_HZ)
    settling_samples: int = DEFAULT_SETTLING_SAMPLES
    stereo_threshold_db: float = 10.0
    deemphasis_tau_s: float = FM_DEEMPHASIS_TAU_S
    reference_deviation_hz: float = FM_MAX_DEVIATION_HZ
    dc_avoidance_offset_hz: float = DC_AVOIDANCE_OFFSET_HZ

    def __post_init__(self) -> None:
        if self.block_samples <= 0:
            raise ValueError(f"block_samples must be positive, got {self.block_samples}")
        if self.overlap_samples < 0:
            raise ValueError(f"overlap_samples must not be negative, got {self.overlap_samples}")
        if self.overlap_samples >= self.block_samples:
            raise ValueError(
                f"overlap_samples ({self.overlap_samples}) must be smaller than block_samples "
                f"({self.block_samples}), or every block would be mostly overlap"
            )
        if self.audio_rate_hz <= 0:
            raise ValueError(f"audio_rate_hz must be positive, got {self.audio_rate_hz}")


class FmListener:
    """Keeps a byte stream fed with the audio of one FM station.

    Example::

        listener = FmListener(device, freq_hz=98_000_000)
        with listener:
            player = LibVlcPlayer(listener.stream, sample_rate_hz=listener.audio_rate_hz)
            player.play()
            ...
            player.stop()

    The receiver is opened if necessary and left as it was found. Starting twice is harmless.

    Args:
        device: The receiver to listen with.
        freq_hz: Station to tune.
        settings: How to run the demodulation.
        stream: Where to write audio. One is created if not given.
    """

    def __init__(
        self,
        device: SdrDevice,
        freq_hz: float,
        *,
        settings: ListenSettings | None = None,
        stream: ByteStream | None = None,
    ) -> None:
        self._device = device
        self._freq_hz = freq_hz
        self._settings = settings or ListenSettings()
        self._stream = stream if stream is not None else ByteStream()

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._opened_device = False
        self._offset_hz = 0.0
        self._tail: IqSamples = np.zeros(0, dtype=np.complex64)
        self._audio_per_block: int | None = None
        self._is_stereo: bool | None = None
        self._blocks_produced = 0
        self._error: BaseException | None = None

    # -- State --------------------------------------------------------------------------------

    @property
    def stream(self) -> ByteStream:
        """Where the audio is written."""
        return self._stream

    @property
    def freq_hz(self) -> float:
        """The station being listened to."""
        return self._freq_hz

    @property
    def audio_rate_hz(self) -> int:
        """Rate of the audio in the stream."""
        return self._settings.audio_rate_hz

    @property
    def is_running(self) -> bool:
        """Whether the producer thread is alive."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def is_stereo(self) -> bool | None:
        """Whether the last block decoded was stereo, or ``None`` before the first one."""
        return self._is_stereo

    @property
    def blocks_produced(self) -> int:
        """How many blocks have been demodulated."""
        return self._blocks_produced

    @property
    def error(self) -> BaseException | None:
        """Why the producer stopped, if it stopped because of a failure.

        A producer thread that dies silently leaves a player waiting for audio that will never
        come, so the reason is kept for the caller to report.
        """
        return self._error

    # -- Control ------------------------------------------------------------------------------

    def start(self) -> None:
        """Tune the station and start producing audio.

        Raises:
            DeviceError: if the receiver cannot be opened or tuned.
        """
        with self._lock:
            if self._thread is not None:
                return
            self._prepare_device()
            self._stop.clear()
            self._error = None
            self._thread = threading.Thread(
                target=self._produce, name=f"openwave-listen-{self._freq_hz / 1e6:.1f}", daemon=True
            )
            self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Stop producing and close the stream. Stopping twice is harmless."""
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        # Closing the stream releases a producer blocked waiting for room in it, which is what
        # stops a stop command from hanging behind a player that is not draining.
        self._stream.close()
        if thread is not None:
            thread.join(timeout=timeout_s)
        if self._opened_device:
            self._device.close()
            self._opened_device = False

    def __enter__(self) -> FmListener:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # -- The producer -------------------------------------------------------------------------

    def _prepare_device(self) -> None:
        """Open the receiver if needed, choose a rate, and tune off-centre."""
        if not self._device.is_open:
            self._device.open()
            self._opened_device = True

        if self._device.sample_rate_hz <= 0:
            rates = self._device.supported_sample_rates_hz
            self._device.set_sample_rate(2_400_000.0 if rates is None else max(rates))

        offset_hz = self._settings.dc_avoidance_offset_hz
        center_freq_hz = self._freq_hz - offset_hz
        if not self._device.tuning_range.contains(center_freq_hz):
            center_freq_hz = self._freq_hz + offset_hz
            offset_hz = -offset_hz
        self._device.set_center_freq(center_freq_hz)
        self._offset_hz = offset_hz

        if self._settings.settling_samples:
            self._device.read_samples(self._settings.settling_samples)
        self._tail = np.zeros(0, dtype=np.complex64)
        self._audio_per_block = None

    def _produce(self) -> None:
        """Read, demodulate and write, until told to stop."""
        try:
            while not self._stop.is_set():
                block = self._device.read_samples(self._settings.block_samples)
                audio_bytes = self._demodulate_block(block)
                if audio_bytes:
                    self._stream.write(audio_bytes)
                self._blocks_produced += 1
        except StreamClosedError:
            # The expected way to end: stop() closed the stream underneath us.
            pass
        except (DeviceError, ValueError) as error:
            self._error = error
            self._stream.close()
        except Exception as error:  # noqa: BLE001 - a silent producer death strands the player
            self._error = error
            self._stream.close()

    def _demodulate_block(self, block: IqSamples) -> bytes:
        """Demodulate one block, joined to the tail of the previous one.

        Only the newest stretch of audio is emitted: the part covering the overlap is where the
        filters were still settling. The amount to emit is what one block alone produced on the
        first pass, which is exact -- see the module docstring for why computing it from a ratio
        is not.
        """
        combined = np.concatenate([self._tail, block]) if self._tail.size else block

        audio = demodulate(
            combined,
            sample_rate_hz=self._device.sample_rate_hz,
            offset_hz=self._offset_hz,
            audio_rate_hz=self._settings.audio_rate_hz,
            stereo_threshold_db=self._settings.stereo_threshold_db,
            deemphasis_tau_s=self._settings.deemphasis_tau_s,
            normalise="deviation",
            reference_deviation_hz=self._settings.reference_deviation_hz,
        )
        self._is_stereo = audio.is_stereo

        if self._audio_per_block is None:
            # The first block carries no tail, so all of its audio is good, and its length is
            # the measurement every later block is trimmed to.
            self._audio_per_block = audio.left.size
            emit_from = 0
        else:
            emit_from = max(0, audio.left.size - self._audio_per_block)

        if self._settings.overlap_samples:
            self._tail = block[-self._settings.overlap_samples :].copy()

        return pcm_bytes(audio.left[emit_from:], audio.right[emit_from:])

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({format_frequency(self._freq_hz)}, "
            f"running={self.is_running}, blocks={self._blocks_produced})"
        )
