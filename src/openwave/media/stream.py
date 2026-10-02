"""A byte buffer between a producer and a player.

Live radio has a producer and a consumer running at their own pace in different threads. The
demodulator produces audio in blocks, as fast as samples arrive from the receiver. libVLC pulls
bytes when its own output needs them. :class:`ByteStream` sits between the two.

It is bounded on purpose. An unbounded buffer hides the real problem -- a producer faster than
the consumer -- by growing until memory runs out, and in the meantime adds latency, which for
live radio means the audio drifts further and further behind the broadcast. A bounded buffer
makes the producer wait instead, which is the correct behaviour: a receiver that cannot be read
fast enough should be throttled, not queued.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Final

#: Default capacity, in bytes.
#:
#: About two seconds of 48 kHz stereo 16-bit audio: enough to ride out a slow block of
#: demodulation, short enough that the delay behind the broadcast stays unnoticeable.
DEFAULT_CAPACITY_BYTES: Final = 384_000


class StreamClosedError(RuntimeError):
    """The stream was closed while an operation was waiting on it."""


class ByteStream:
    """A bounded, thread-safe queue of bytes.

    One producer writes, one consumer reads. Both block when they cannot proceed, and both are
    released when the stream is closed, so neither thread can be left waiting for the other
    after shutdown -- which is the failure that turns a stop command into a hung process.

    Example::

        stream = ByteStream()
        stream.write(b"audio")
        assert stream.read(5) == b"audio"
        stream.close()
    """

    def __init__(self, capacity_bytes: int = DEFAULT_CAPACITY_BYTES) -> None:
        if capacity_bytes <= 0:
            raise ValueError(f"capacity must be positive, got {capacity_bytes}")
        self._capacity = capacity_bytes
        self._chunks: deque[bytes] = deque()
        self._pending = 0
        self._closed = False
        self._dropped = 0
        self._condition = threading.Condition()

    # -- State --------------------------------------------------------------------------------

    @property
    def capacity_bytes(self) -> int:
        """How many bytes the buffer holds before a write has to wait."""
        return self._capacity

    @property
    def pending(self) -> int:
        """Bytes currently buffered."""
        with self._condition:
            return self._pending

    @property
    def closed(self) -> bool:
        """Whether the stream has been closed."""
        with self._condition:
            return self._closed

    @property
    def dropped(self) -> int:
        """Bytes discarded because the consumer could not keep up.

        Only non-zero when something was written with ``block=False``. Worth reporting: dropped
        audio is an audible gap, and a user hearing one deserves to know it was not the radio.
        """
        with self._condition:
            return self._dropped

    # -- Producer -----------------------------------------------------------------------------

    def write(self, data: bytes, *, block: bool = True, timeout_s: float | None = None) -> int:
        """Add bytes to the buffer.

        Args:
            data: The bytes to add.
            block: Whether to wait for room. With ``False``, as much as fits is written and the
                rest is counted in :attr:`dropped`.
            timeout_s: How long to wait for room, or ``None`` to wait indefinitely.

        Returns:
            How many bytes were written.

        Raises:
            StreamClosedError: if the stream is closed, or is closed while waiting.
        """
        if not data:
            return 0
        with self._condition:
            if self._closed:
                raise StreamClosedError("cannot write to a closed stream")

            if not block:
                room = self._capacity - self._pending
                if room <= 0:
                    self._dropped += len(data)
                    return 0
                written = data[:room]
                if len(written) < len(data):
                    self._dropped += len(data) - len(written)
                self._append(written)
                return len(written)

            deadline_passed = not self._condition.wait_for(
                lambda: self._closed or self._pending + len(data) <= self._capacity,
                timeout=timeout_s,
            )
            if self._closed:
                raise StreamClosedError("the stream was closed while waiting for room")
            if deadline_passed:
                return 0
            self._append(data)
            return len(data)

    def _append(self, data: bytes) -> None:
        """Add to the buffer and wake the consumer. The lock must already be held."""
        self._chunks.append(data)
        self._pending += len(data)
        self._condition.notify_all()

    # -- Consumer -----------------------------------------------------------------------------

    def read(self, size: int, *, timeout_s: float | None = None) -> bytes:
        """Take up to ``size`` bytes from the buffer.

        Blocks until at least one byte is available. Returns fewer bytes than asked for rather
        than waiting for the full amount, because a player starved of audio would rather have
        some of it now than all of it late. Returns empty only when the stream is closed and
        drained, which is how a consumer learns to stop.
        """
        if size <= 0:
            raise ValueError(f"read size must be positive, got {size}")

        with self._condition:
            self._condition.wait_for(lambda: self._pending > 0 or self._closed, timeout=timeout_s)
            if self._pending == 0:
                return b""

            taken: list[bytes] = []
            remaining = size
            while remaining > 0 and self._chunks:
                chunk = self._chunks[0]
                if len(chunk) <= remaining:
                    taken.append(chunk)
                    self._chunks.popleft()
                    remaining -= len(chunk)
                else:
                    taken.append(chunk[:remaining])
                    self._chunks[0] = chunk[remaining:]
                    remaining = 0

            data = b"".join(taken)
            self._pending -= len(data)
            self._condition.notify_all()
            return data

    # -- Shutdown -----------------------------------------------------------------------------

    def close(self) -> None:
        """Close the stream, releasing anything waiting on it.

        Buffered bytes stay readable, so a consumer finishes what was already produced rather
        than cutting off mid-word. Closing twice is harmless.
        """
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def clear(self) -> None:
        """Discard everything buffered, to resynchronise after a retune."""
        with self._condition:
            self._chunks.clear()
            self._pending = 0
            self._condition.notify_all()

    def __enter__(self) -> ByteStream:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(pending={self._pending}, "
            f"capacity={self._capacity}, closed={self._closed})"
        )
