"""Serving a station's audio over HTTP.

The frontend does not decode radio. It asks for a URL, and whatever plays it -- libVLC, a
browser's audio element, VLC on another machine -- pulls the audio from here. So the stream is a
plain WAV of unknown length over HTTP, which everything understands and nothing has to be
configured for.

**Why one listener at a time.** A receiver can only be tuned to one station, so a second request
for a different frequency would retune the receiver underneath the first listener. The second
request is refused with a clear reason rather than silently stealing the radio.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Final

from openwave.media.wav import WavFormat, wav_header
from openwave.radio.listener import FmListener, ListenSettings
from openwave.sdr.device import SdrDevice

#: Bytes sent per chunk. About a tenth of a second of 48 kHz stereo audio.
CHUNK_BYTES: Final = 1 << 14

#: How long to wait for audio before giving up on a stream, in seconds.
#:
#: A listener that has stopped producing -- because the receiver was unplugged, say -- must not
#: leave an HTTP response open for ever.
STREAM_TIMEOUT_S: Final = 5.0


class StreamBusyError(RuntimeError):
    """Something is already listening on a different frequency."""


class AudioStreamer:
    """Keeps at most one FM station streaming over HTTP.

    Example::

        streamer = AudioStreamer(device_factory=lambda: open_device("rtlsdr"))
        for chunk in streamer.stream(98_000_000):
            ...
    """

    def __init__(
        self,
        *,
        device_factory: Callable[[], SdrDevice],
        settings: ListenSettings | None = None,
    ) -> None:
        self._device_factory = device_factory
        self._settings = settings or ListenSettings()
        self._lock = threading.Lock()
        self._listener: FmListener | None = None
        self._freq_hz: float | None = None
        self._listeners = 0

    @property
    def frequency_hz(self) -> float | None:
        """What is being streamed, if anything."""
        with self._lock:
            return self._freq_hz

    @property
    def listener_count(self) -> int:
        """How many clients are pulling the stream."""
        with self._lock:
            return self._listeners

    def stream(self, freq_hz: float, *, max_seconds: float | None = None) -> Iterator[bytes]:
        """Yield a WAV stream of the station at ``freq_hz``.

        Several clients may share one frequency, because they can all read from the same
        buffer. A request for a different frequency is refused.

        Args:
            freq_hz: The station to tune.
            max_seconds: Stop after this much audio, giving a finite clip with a correct
                length in its header. ``None`` streams until the client goes away, which is
                what live listening wants.

        Raises:
            StreamBusyError: if a different frequency is already being streamed.
            DeviceError: if the receiver cannot be opened or tuned.
        """
        if max_seconds is not None and max_seconds <= 0:
            raise ValueError(f"max_seconds must be positive, got {max_seconds}")

        listener = self._acquire(freq_hz)
        try:
            fmt = WavFormat(sample_rate_hz=listener.audio_rate_hz, channels=2)
            wanted = None if max_seconds is None else round(max_seconds * fmt.byte_rate)
            # A finite clip can state its length, so a player knows how long it is; a live
            # stream cannot, because the broadcast has not finished.
            yield wav_header(fmt, data_bytes=wanted)

            sent = 0
            while wanted is None or sent < wanted:
                size = CHUNK_BYTES if wanted is None else min(CHUNK_BYTES, wanted - sent)
                chunk = listener.stream.read(size, timeout_s=STREAM_TIMEOUT_S)
                if not chunk:
                    break
                sent += len(chunk)
                yield chunk
        finally:
            self._release()

    def _acquire(self, freq_hz: float) -> FmListener:
        """Start or join the listener for a frequency."""
        with self._lock:
            current = self._freq_hz
            if self._listener is not None and current != freq_hz:
                raise StreamBusyError(
                    f"already streaming {(current or 0.0) / 1e6:.1f} MHz to "
                    f"{self._listeners} listener(s). A receiver can only be tuned to one "
                    "station, so stop that stream before starting another."
                )

            if self._listener is None:
                device = self._device_factory()
                listener = FmListener(device, freq_hz, settings=self._settings)
                listener.start()
                self._listener = listener
                self._freq_hz = freq_hz

            self._listeners += 1
            return self._listener

    def _release(self) -> None:
        """Let go of the listener, stopping it when nobody is left."""
        with self._lock:
            self._listeners = max(0, self._listeners - 1)
            if self._listeners or self._listener is None:
                return
            listener = self._listener
            self._listener = None
            self._freq_hz = None
        listener.stop()

    def stop(self) -> None:
        """Stop streaming, whatever is listening."""
        with self._lock:
            listener = self._listener
            self._listener = None
            self._freq_hz = None
            self._listeners = 0
        if listener is not None:
            listener.stop()
