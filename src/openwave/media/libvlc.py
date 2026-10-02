"""Playback through libVLC.

libVLC pulls audio rather than being pushed it: it asks for the next bytes when its output needs
them. That suits live radio, because it makes libVLC's clock the one that matters and leaves the
demodulator to keep the buffer fed.

The connection is made with ``libvlc_media_new_callbacks``, which hands libVLC four functions --
open, read, seek, close -- in place of a filename. OpenWave supplies a WAV header followed by the
demodulated audio, and libVLC never knows it is not reading a file.

Install the optional extra to use it::

    pip install "openwave[media]"

The extra installs the Python bindings. libVLC itself comes from the system: ``vlc`` or
``libvlc-dev`` on Debian and Ubuntu, ``vlc-devel`` on Fedora.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Final

from openwave.media.stream import ByteStream
from openwave.media.wav import WavFormat, wav_header

#: Bytes libVLC is given per read callback at most. It asks for what it wants; this is a cap.
MAX_READ_BYTES: Final = 1 << 16

#: How long a read callback waits for audio before reporting the end of the stream, in seconds.
#:
#: A read callback must not wait indefinitely. libVLC calls it from its own thread, and
#: ``stop()`` waits for that thread, so a callback blocked forever on an empty buffer turns a
#: stop command into a deadlock -- which is exactly what happens when a producer dies without
#: closing its stream. Waiting a few seconds rides out an underrun; beyond that, something has
#: gone wrong upstream and ending playback is the honest outcome.
DEFAULT_READ_TIMEOUT_S: Final = 5.0

#: How long each individual wait inside a read callback lasts, in seconds.
#:
#: Short, so that ``stop()`` is noticed promptly rather than after the full timeout.
_READ_POLL_S: Final = 0.1

#: Prototypes of the four callbacks ``libvlc_media_new_callbacks`` takes.
#:
#: These are declared here rather than taken from ``vlc.MediaOpenCb`` and friends because, in
#: python-vlc 3.0.21, those names are bound to plain ``c_void_p`` subclasses at run time -- the
#: real ``CFUNCTYPE`` declarations sit in a block the module never executes. Passing a function
#: to them fails with "cannot be converted to pointer". The prototypes below match the C
#: signatures in ``libvlc_media.h``, and :func:`_as_vlc_callback` converts an instance into
#: something the bindings will accept.
_OpenCallback: Final = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_uint64),
)
_ReadCallback: Final = ctypes.CFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_char),
    ctypes.c_size_t,
)
_SeekCallback: Final = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint64)
_CloseCallback: Final = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

#: Size reported for a stream whose length is not known. The field is an unsigned 64-bit value.
_UNKNOWN_STREAM_SIZE: Final = 0xFFFFFFFFFFFFFFFF


def _as_vlc_callback(callback: Any, vlc_type: Any) -> Any:
    """Wrap a ctypes function pointer in the type python-vlc expects.

    The bindings declare the callback parameters as ``c_void_p`` subclasses, and ctypes will not
    accept a plain ``c_void_p`` where a subclass is declared. Constructing the subclass from the
    function pointer's address satisfies both.
    """
    address = ctypes.cast(callback, ctypes.c_void_p).value
    if address is None:
        raise PlaybackError("could not take the address of a playback callback")
    return vlc_type(address)


class PlaybackUnavailableError(RuntimeError):
    """libVLC or its Python bindings are not available.

    Mirrors :class:`~openwave.sdr.errors.DriverUnavailableError`: a caller handling "cannot
    play" should not also have to handle ``ImportError``.
    """


class PlaybackError(RuntimeError):
    """libVLC refused to play the stream."""


def _import_vlc() -> Any:
    """Import ``vlc``, turning a missing dependency into :class:`PlaybackUnavailableError`."""
    try:
        import vlc
    except (ImportError, OSError) as error:
        # OSError as well as ImportError: the bindings import fine and then fail to find
        # libvlc.so, which is a different problem with the same remedy.
        raise PlaybackUnavailableError(
            "playback needs the python-vlc package and libVLC itself. Install the extra with: "
            'pip install "openwave[media]"  -- and libVLC from your package manager '
            "(libvlc-dev on Debian or Ubuntu, vlc-devel on Fedora)."
        ) from error
    return vlc


def playback_available() -> bool:
    """Whether audio can be played. Does not open an audio device."""
    try:
        _import_vlc()
    except PlaybackUnavailableError:
        return False
    return True


def libvlc_version() -> str | None:
    """The libVLC version in use, or ``None`` if it is not available."""
    try:
        vlc = _import_vlc()
    except PlaybackUnavailableError:
        return None
    version: bytes = vlc.libvlc_get_version()
    return version.decode(errors="replace")


class LibVlcPlayer:
    """Plays a live audio stream through libVLC.

    The stream is presented to libVLC as a WAV file of unknown length, so playback continues
    until the stream is closed.

    **libVLC needs a continuous supply to start.** It reads greedily while opening the stream
    and only moves to ``Playing`` once it has enough. Measured behaviour: given half a second of
    audio and then nothing, with the stream left open, it consumes every byte and then sits in
    ``Opening`` indefinitely, waiting for more. Closing the stream instead takes it straight to
    ``Playing`` and then ``Ended``. Live listening satisfies this naturally, since the
    demodulator keeps producing; a caller feeding a short finite burst should close the stream
    when it is done.

    Example::

        stream = ByteStream()
        player = LibVlcPlayer(stream, sample_rate_hz=48_000)
        player.play()
        ...                     # a producer thread writes audio into the stream
        player.stop()

    Args:
        stream: Where the audio comes from. The player writes nothing to it.
        sample_rate_hz: Rate of the audio in the stream.
        channels: 1 for mono, 2 for stereo.
        audio_output: libVLC output module to use. ``"dummy"`` decodes without making a sound,
            which is what a test wants on a machine with no audio device.
        read_timeout_s: How long to wait for audio before reporting the end of the stream. See
            :data:`DEFAULT_READ_TIMEOUT_S`.
        extra_args: Further arguments for the libVLC instance.
    """

    def __init__(
        self,
        stream: ByteStream,
        *,
        sample_rate_hz: int,
        channels: int = 2,
        audio_output: str | None = None,
        read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self._vlc = _import_vlc()
        self._stream = stream
        self._format = WavFormat(sample_rate_hz=sample_rate_hz, channels=channels)
        self._header = wav_header(self._format)
        self._header_sent = 0
        self._lock = threading.Lock()
        if read_timeout_s <= 0:
            raise ValueError(f"read timeout must be positive, got {read_timeout_s}")
        self._read_timeout_s = read_timeout_s
        # Set before libVLC is told to stop, so a read callback waiting on an empty buffer
        # gives up instead of holding the thread that stop() is about to join.
        self._stopping = threading.Event()

        arguments = ["--quiet", "--no-video", *extra_args]
        if audio_output:
            arguments.append(f"--aout={audio_output}")
        self._instance = self._vlc.Instance(*arguments)
        if self._instance is None:
            raise PlaybackError(
                "libVLC refused to start. Running 'vlc --version' usually says why."
            )

        # These references are held for the life of the player because libVLC keeps the raw
        # pointers and calls them from its own threads. Letting Python collect them would leave
        # libVLC calling freed memory, which takes the whole process down.
        self._open_cb = _OpenCallback(self._on_open)
        self._read_cb = _ReadCallback(self._on_read)
        self._seek_cb = _SeekCallback(self._on_seek)
        self._close_cb = _CloseCallback(self._on_close)

        self._media: Any = None
        self._player: Any = None

    # -- Control ------------------------------------------------------------------------------

    def play(self) -> None:
        """Start playing. Returns as soon as libVLC has accepted the stream."""
        with self._lock:
            if self._player is not None:
                return
            self._stopping.clear()
            self._header_sent = 0
            self._media = self._instance.media_new_callbacks(
                _as_vlc_callback(self._open_cb, self._vlc.MediaOpenCb),
                _as_vlc_callback(self._read_cb, self._vlc.MediaReadCb),
                _as_vlc_callback(self._seek_cb, self._vlc.MediaSeekCb),
                _as_vlc_callback(self._close_cb, self._vlc.MediaCloseCb),
                None,
            )
            if self._media is None:
                raise PlaybackError("libVLC would not accept the stream")
            self._player = self._media.player_new_from_media()
            if self._player is None:
                raise PlaybackError("libVLC would not create a player for the stream")
            if self._player.play() == -1:
                raise PlaybackError(
                    "libVLC could not start playback. With no audio device available, "
                    "construct the player with audio_output='dummy'."
                )

    def stop(self) -> None:
        """Stop playing and release libVLC's resources. Stopping twice is harmless.

        The read callback is released first. Without that, stopping a player whose producer has
        gone quiet without closing its stream would wait forever: libVLC's thread would be
        inside the callback, waiting for audio, and ``stop()`` would be waiting for the thread.
        """
        self._stopping.set()
        with self._lock:
            if self._player is not None:
                self._player.stop()
                self._player.release()
                self._player = None
            if self._media is not None:
                self._media.release()
                self._media = None

    @property
    def is_playing(self) -> bool:
        """Whether libVLC is currently producing audio."""
        with self._lock:
            return self._player is not None and bool(self._player.is_playing())

    @property
    def state(self) -> str:
        """libVLC's own description of what it is doing, for diagnostics."""
        with self._lock:
            if self._player is None:
                return "stopped"
            return str(self._player.get_state())

    def __enter__(self) -> LibVlcPlayer:
        self.play()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # -- The callbacks libVLC calls -----------------------------------------------------------

    def _on_open(self, _opaque: Any, data_pointer: Any, size_pointer: Any) -> int:
        """Tell libVLC the stream is ready and of unknown length.

        Runs on a libVLC thread, so it must not raise: an exception crossing a ctypes callback
        boundary terminates the process instead of propagating.
        """
        try:
            data_pointer[0] = None
            size_pointer[0] = _UNKNOWN_STREAM_SIZE
            self._header_sent = 0
            return 0
        except Exception:  # noqa: BLE001 - raising here would kill the process
            return -1

    def _on_read(self, _opaque: Any, buffer: Any, length: int) -> int:
        """Fill libVLC's buffer, header first.

        Returns the number of bytes written, or 0 at the end of the stream. Runs on a libVLC
        thread, so it must never raise, for the same reason as :meth:`_on_open`.
        """
        try:
            wanted = min(int(length), MAX_READ_BYTES)
            if wanted <= 0:
                return 0

            chunk = self._next_bytes(wanted)
            if not chunk:
                return 0
            ctypes.memmove(buffer, chunk, len(chunk))
            return len(chunk)
        except Exception:  # noqa: BLE001 - raising here would kill the process, not the read
            return 0

    def _next_bytes(self, wanted: int) -> bytes:
        """The next bytes libVLC should see: the WAV header, then the audio.

        Waits in short steps rather than one long one, so that :meth:`stop` takes effect
        promptly. Returns empty when the stream has ended, when the player is stopping, or when
        nothing has arrived for :attr:`_read_timeout_s`.
        """
        if self._header_sent < len(self._header):
            chunk = self._header[self._header_sent : self._header_sent + wanted]
            self._header_sent += len(chunk)
            return chunk

        deadline = time.monotonic() + self._read_timeout_s
        while not self._stopping.is_set():
            chunk = self._stream.read(wanted, timeout_s=_READ_POLL_S)
            if chunk:
                return chunk
            if self._stream.closed:
                return b""
            if time.monotonic() >= deadline:
                return b""
        return b""

    def _on_seek(self, _opaque: Any, _offset: int) -> int:
        """Refuse seeking. Live radio has no past to seek into."""
        return -1

    def _on_close(self, _opaque: Any) -> None:
        """Nothing to release: the stream belongs to whoever created it."""
        return None
