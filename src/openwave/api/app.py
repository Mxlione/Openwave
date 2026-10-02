"""The OpenWave HTTP API.

One FastAPI application, versioned under ``/api/v1``. Every response shape is a Pydantic model,
so the OpenAPI schema is generated from the same definitions the code uses and the Angular client
is generated from that -- which is what stops the interface drifting away from the backend.

**Why scans are jobs rather than requests.** An FM scan takes a few seconds and a television scan
up to a minute. An HTTP request that blocks for a minute times out in proxies, cannot report
progress, and leaves a client with nothing to show. So ``POST /scans`` starts a scan and returns
an identifier; the result is collected with ``GET /scans/{id}`` and watched over a WebSocket.

**Why the version is in the path.** The Angular client is generated from this schema, so a
breaking change has to be something a client can detect rather than something it discovers at
run time. A new prefix lets both versions be served while clients catch up.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, StreamingResponse

from openwave import __version__
from openwave.api.models import (
    Band,
    Capabilities,
    DeviceSummary,
    ErrorResponse,
    ScanDetail,
    ScanRequest,
    ScanSummary,
)
from openwave.api.scans import ScanBusyError, ScanManager, ScanNotFoundError
from openwave.api.spectrum import (
    DEFAULT_BINS,
    DEFAULT_FRAME_RATE,
    DEFAULT_RANGE_DB,
    FRAME_HEADER_SIZE,
    SpectrumStreamer,
)
from openwave.api.streams import AudioStreamer, StreamBusyError
from openwave.media.libvlc import libvlc_version, playback_available
from openwave.radio.station import Station
from openwave.sdr.device import SdrDevice
from openwave.sdr.device_manager import (
    list_devices,
    list_dvb_devices,
    open_device,
    open_dvb_device,
)
from openwave.sdr.errors import DeviceError
from openwave.sdr.rtl_sdr import rtlsdr_available
from openwave.tv.channel import Channel

#: Prefix every route sits behind.
API_PREFIX: Final = "/api/v1"

#: Drivers that have never been run against the hardware they target.
#:
#: Surfaced in the device listing so a client can say so rather than implying a reading is
#: trustworthy. The maintainer owns no receiver, so these are written from vendor interfaces
#: and unverified.
UNVALIDATED_DRIVERS: Final = frozenset({"rtlsdr", "soapysdr", "linuxdvb", "linuxdvb2"})


def create_app(
    *,
    device_spec: str = "mock",
    tuner_spec: str = "mockdvb",
    device_factory: Callable[[str], SdrDevice] | None = None,
    tuner_factory: Callable[[str], Any] | None = None,
) -> FastAPI:
    """Build the API.

    Args:
        device_spec: Default receiver, used when a request does not name one.
        tuner_spec: Default DVB tuner.
        device_factory: How to build a receiver from a specification. Replaceable so that a
            test, or the demonstration mode, can supply a populated simulator.
        tuner_factory: The same for tuners.
    """
    open_receiver = device_factory or open_device
    open_tuner = tuner_factory or open_dvb_device

    manager = ScanManager(
        device_spec=device_spec,
        tuner_spec=tuner_spec,
        device_factory=open_receiver,
        tuner_factory=open_tuner,
    )
    streamer = AudioStreamer(device_factory=lambda: open_receiver(device_spec))
    spectrum = SpectrumStreamer(device_factory=lambda: open_receiver(device_spec))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Release the receiver when the server stops.

        Without this, a scan or a stream in progress keeps the device claimed after the process
        is asked to exit, and the next run cannot open it.
        """
        try:
            yield
        finally:
            streamer.stop()
            manager.shutdown(wait=False)

    app = FastAPI(
        title="OpenWave",
        version=__version__,
        summary="Scan, detect and play publicly broadcast radio and TV services.",
        description=(
            "Every scan is a background job: start one with POST /api/v1/scans, then collect "
            "its result or watch its progress. Levels are in dBFS rather than dBm, because a "
            "consumer receiver has no calibrated power reference."
        ),
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.state.streamer = streamer
    app.state.spectrum = spectrum
    app.state.device_spec = device_spec
    app.state.tuner_spec = tuner_spec

    _register_error_handlers(app)
    _register_routes(app, manager=manager, streamer=streamer, spectrum=spectrum)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Turn the project's own exceptions into responses a client can act on.

    The common failures here are things a person can fix -- a dongle not plugged in, a driver
    not installed -- so the message is meant to be shown rather than logged.
    """

    @app.exception_handler(DeviceError)
    async def device_error(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(error=type(error).__name__, detail=str(error)).model_dump(),
        )

    @app.exception_handler(ScanNotFoundError)
    async def scan_not_found(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(error="ScanNotFound", detail=str(error).strip("'")).model_dump(),
        )

    @app.exception_handler(ScanBusyError)
    async def scan_busy(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(error="ScanBusy", detail=str(error)).model_dump(),
        )

    @app.exception_handler(StreamBusyError)
    async def stream_busy(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(error="StreamBusy", detail=str(error)).model_dump(),
        )


def _register_routes(
    app: FastAPI,
    *,
    manager: ScanManager,
    streamer: AudioStreamer,
    spectrum: SpectrumStreamer,
) -> None:
    """Attach every route to the application."""

    @app.get(f"{API_PREFIX}/health", response_model=Capabilities, tags=["system"])
    async def health() -> Capabilities:
        """What this installation can do.

        Worth asking before offering a button that cannot work: playback needs libVLC, and a
        scan needs a receiver.
        """
        return Capabilities(
            version=__version__,
            playback=playback_available(),
            libvlc_version=libvlc_version(),
            rtlsdr=rtlsdr_available(),
            receivers=len(list_devices()),
            tuners=len(list_dvb_devices()),
        )

    @app.get(f"{API_PREFIX}/devices", response_model=list[DeviceSummary], tags=["system"])
    async def devices() -> list[DeviceSummary]:
        """Every receiver and tuner that can currently be opened."""
        summaries = [
            DeviceSummary(
                spec=f"{info.driver}:{info.index}",
                driver=info.driver,
                index=info.index,
                label=info.label,
                serial=info.serial,
                kind="receiver",
                validated_on_hardware=info.driver not in UNVALIDATED_DRIVERS,
            )
            for info in list_devices()
        ]
        summaries.extend(
            DeviceSummary(
                spec=f"{info.driver}:{info.index}",
                driver=info.driver,
                index=info.index,
                label=info.label,
                serial=info.serial,
                kind="tuner",
                validated_on_hardware=info.driver not in UNVALIDATED_DRIVERS,
            )
            for info in list_dvb_devices()
        )
        return summaries

    @app.post(
        f"{API_PREFIX}/scans",
        response_model=ScanSummary,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["scans"],
    )
    async def start_scan(request: ScanRequest) -> ScanSummary:
        """Start a scan.

        Returns at once with an identifier. The scan itself runs in the background, because an
        FM sweep takes seconds and a television sweep up to a minute.
        """
        return manager.start(request).summary()

    @app.get(f"{API_PREFIX}/scans", response_model=list[ScanSummary], tags=["scans"])
    async def list_scans() -> list[ScanSummary]:
        """Every remembered scan, newest first."""
        return manager.summaries()

    @app.get(f"{API_PREFIX}/scans/{{scan_id}}", response_model=ScanDetail, tags=["scans"])
    async def scan_detail(scan_id: str) -> ScanDetail:
        """One scan, with its results once it has finished."""
        return manager.detail(scan_id)

    @app.delete(f"{API_PREFIX}/scans/{{scan_id}}", response_model=ScanSummary, tags=["scans"])
    async def cancel_scan(scan_id: str) -> ScanSummary:
        """Ask a scan to stop.

        A scan in progress finishes the segment it is on rather than being killed: a receiver
        abandoned mid-read leaves a mess for the next caller.
        """
        return manager.cancel(scan_id).summary()

    @app.get(f"{API_PREFIX}/stations", response_model=list[Station], tags=["results"])
    async def stations() -> list[Station]:
        """The stations found by the most recent completed FM scan.

        Empty until one has run, which is different from a scan that found nothing -- the scan
        list says which.
        """
        job = manager.latest(Band.FM)
        if job is None or job.fm_result is None:
            return []
        return list(job.fm_result.stations)

    @app.get(f"{API_PREFIX}/channels", response_model=list[Channel], tags=["results"])
    async def channels() -> list[Channel]:
        """The services found by the most recent completed television scan."""
        job = manager.latest(Band.TV)
        if job is None or job.tv_result is None:
            return []
        return list(job.tv_result.channels)

    @app.get(f"{API_PREFIX}/streams/fm/{{freq_hz}}", tags=["streams"])
    async def stream_fm(freq_hz: float, seconds: float | None = None) -> StreamingResponse:
        """Play an FM station as a WAV stream.

        The frontend does not decode radio: it points a player at this URL. Anything that
        understands WAV over HTTP can play it, including libVLC and a browser.

        Args:
            freq_hz: The station to tune.
            seconds: Stop after this much audio, giving a finite clip. Without it the stream
                runs until the client goes away, which is what live listening wants.
        """
        if freq_hz <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"frequency must be positive, got {freq_hz}",
            )
        if seconds is not None and seconds <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"seconds must be positive, got {seconds}",
            )
        # A frequency outside the FM band plan is not refused: plans differ by country, and a
        # receiver can tune outside them. The header below says what was asked for, so a client
        # can tell that it got the station it wanted.
        return StreamingResponse(
            streamer.stream(freq_hz, max_seconds=seconds),
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store",
                "X-OpenWave-Frequency-Hz": str(freq_hz),
            },
        )

    @app.get(f"{API_PREFIX}/streams", tags=["streams"])
    async def stream_status() -> dict[str, object]:
        """What is being streamed, if anything."""
        return {
            "frequency_hz": streamer.frequency_hz,
            "listeners": streamer.listener_count,
        }

    @app.websocket(f"{API_PREFIX}/ws/spectrum")
    async def spectrum_feed(
        websocket: WebSocket,
        freq_hz: float = 98_000_000.0,
        sample_rate_hz: float = 2_400_000.0,
        reference_dbfs: float | None = None,
    ) -> None:
        """Push a live spectrum as binary frames.

        Each frame carries its own scale, so a client needs no prior agreement about what the
        bytes mean. See :mod:`openwave.api.spectrum` for the layout.

        The frames are produced in a worker thread, because reading a receiver and taking an
        FFT is blocking work that would otherwise stall the event loop and every other client
        with it.
        """
        await websocket.accept()
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def produce() -> None:
            """Read frames in a thread and hand each one to the event loop."""
            try:
                for frame in spectrum.frames(
                    freq_hz,
                    sample_rate_hz=sample_rate_hz,
                    reference_dbfs=reference_dbfs,
                ):
                    if stop.is_set():
                        return
                    asyncio.run_coroutine_threadsafe(websocket.send_bytes(frame), loop).result(
                        timeout=5.0
                    )
            except Exception:  # noqa: BLE001 - the socket closing is the normal ending
                return

        worker = threading.Thread(target=produce, name="openwave-spectrum", daemon=True)
        worker.start()
        try:
            # Reading is how a disconnect is noticed: nothing is expected from the client.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()

    @app.get(f"{API_PREFIX}/spectrum", tags=["streams"])
    async def spectrum_status() -> dict[str, object]:
        """How the spectrum feed is configured, and whether anybody is watching."""
        return {
            "viewers": spectrum.viewers,
            "bins": DEFAULT_BINS,
            "frame_rate": DEFAULT_FRAME_RATE,
            "range_db": DEFAULT_RANGE_DB,
            "frame_header_bytes": FRAME_HEADER_SIZE,
        }

    @app.websocket(f"{API_PREFIX}/ws/scans/{{scan_id}}")
    async def scan_progress(websocket: WebSocket, scan_id: str) -> None:
        """Push a scan's progress as it happens.

        The alternative is a client polling every few hundred milliseconds, which is a lot of
        requests to learn that a sweep has moved on by one channel.
        """
        await websocket.accept()
        loop = asyncio.get_running_loop()
        updates: asyncio.Queue[ScanSummary] = asyncio.Queue()

        try:
            summary = manager.summary(scan_id)
        except ScanNotFoundError as error:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(error)[:120])
            return

        def on_change(update: ScanSummary) -> None:
            # Called from the scanning thread, so the hand-off to the event loop is explicit.
            loop.call_soon_threadsafe(updates.put_nowait, update)

        stop_listening = manager.listen(scan_id, on_change)
        try:
            await websocket.send_json(summary.model_dump(mode="json"))
            while not summary.state.is_finished:
                summary = await updates.get()
                await websocket.send_json(summary.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            stop_listening()
