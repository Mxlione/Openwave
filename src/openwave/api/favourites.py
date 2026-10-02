"""Remembering the stations and channels somebody cares about.

Favourites are kept by the server rather than in the browser, which is a deliberate choice. A
receiver is a thing in a room: the same OpenWave is reached from a laptop, a phone and a
television, and a list of favourites held in one browser's storage is invisible to the other two.
Keeping it with the receiver is what makes it the same list everywhere.

They are stored as a JSON file, written whole and atomically. A database would be more machinery
than a list of a few dozen frequencies deserves, and a partly-written file would lose the lot --
so the new version is written beside the old one and then moved into place, which on a POSIX
filesystem either happens or does not.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

#: Where favourites are kept when no other location is given.
#:
#: Under the user's data directory rather than beside the code, so that an installed copy and a
#: checkout share one list and neither writes into its own package.
DEFAULT_PATH: Final = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "openwave"
    / "favourites.json"
)

#: Most favourites to keep.
#:
#: A limit exists so that a misbehaving client cannot grow the file without bound. It is far
#: more than anybody will tune to by choice.
MAX_FAVOURITES: Final = 500


class FavouriteKind(StrEnum):
    """What kind of thing has been marked as a favourite."""

    STATION = "station"
    CHANNEL = "channel"


class Favourite(BaseModel):
    """One remembered station or channel."""

    model_config = ConfigDict(frozen=True)

    kind: FavouriteKind
    name: str = Field(min_length=1, max_length=64, description="What to call it in a list")
    freq_hz: float = Field(
        gt=0.0,
        description=(
            "For a station, its carrier; for a channel, the frequency of the multiplex carrying it."
        ),
    )
    service_id: int | None = Field(
        default=None,
        ge=0,
        le=0xFFFF,
        description="Which service within a multiplex, for a television channel",
    )
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When it was added"
    )

    @property
    def key(self) -> tuple[str, int, int]:
        """What makes this favourite the same as another.

        A television channel is identified by its multiplex and its service, because several
        services share a frequency. A radio station is identified by its frequency alone.

        The frequency is rounded to the nearest kilohertz: a receiver reports where it was
        tuned, so the same station found by two scans can differ by a few hertz, and a list
        that then held it twice would be a bug nobody could explain.
        """
        return (self.kind.value, round(self.freq_hz / 1000.0), self.service_id or 0)


class FavouriteStore:
    """Keeps the list of favourites, and writes it where it will survive a restart.

    Example::

        store = FavouriteStore()
        store.add(Favourite(kind=FavouriteKind.STATION, name="CITY FM", freq_hz=101.7e6))
        for favourite in store.all():
            ...

    Args:
        path: Where to keep the file. ``None`` uses :data:`DEFAULT_PATH`.
        autosave: Whether to write after every change. Off for a test that does not want a
            file.
    """

    def __init__(self, path: Path | None = None, *, autosave: bool = True) -> None:
        self._path = path if path is not None else DEFAULT_PATH
        self._autosave = autosave
        self._lock = threading.Lock()
        self._favourites: dict[tuple[str, int, int], Favourite] = {}
        self._load()

    @property
    def path(self) -> Path:
        """Where the list is kept."""
        return self._path

    def all(self, kind: FavouriteKind | None = None) -> list[Favourite]:
        """Every favourite, or only those of one kind, in frequency order."""
        with self._lock:
            favourites = list(self._favourites.values())
        if kind is not None:
            favourites = [item for item in favourites if item.kind is kind]
        return sorted(favourites, key=lambda item: (item.freq_hz, item.service_id or 0))

    def add(self, favourite: Favourite) -> Favourite:
        """Remember a station or channel.

        Adding something already remembered replaces it, which is how a renamed station keeps
        its place rather than appearing twice.

        Raises:
            ValueError: if the list is full.
        """
        with self._lock:
            key = favourite.key
            if key not in self._favourites and len(self._favourites) >= MAX_FAVOURITES:
                raise ValueError(
                    f"the favourites list holds at most {MAX_FAVOURITES} entries; "
                    "remove one before adding another"
                )
            self._favourites[key] = favourite
        self._save_if_wanted()
        return favourite

    def remove(self, favourite: Favourite) -> bool:
        """Forget a station or channel. Returns whether it was there."""
        with self._lock:
            removed = self._favourites.pop(favourite.key, None) is not None
        if removed:
            self._save_if_wanted()
        return removed

    def contains(self, favourite: Favourite) -> bool:
        """Whether something is already remembered."""
        with self._lock:
            return favourite.key in self._favourites

    def clear(self) -> None:
        """Forget everything."""
        with self._lock:
            self._favourites.clear()
        self._save_if_wanted()

    # -- Storage ------------------------------------------------------------------------------

    def _load(self) -> None:
        """Read the file, treating it as untrusted.

        A favourites file is editable by hand and survives upgrades, so it can easily contain
        something this version does not expect. A bad entry is skipped rather than allowed to
        stop the server starting: losing one favourite is a nuisance, and refusing to start is
        a fault.
        """
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return

        for entry in raw:
            try:
                favourite = Favourite.model_validate(entry)
            except Exception:  # noqa: BLE001 - one bad entry must not stop the rest loading
                continue
            self._favourites[favourite.key] = favourite

    def save(self) -> None:
        """Write the list out.

        Written to a temporary file beside the real one and then moved into place, so that a
        crash partway through leaves the previous list rather than half of the new one.
        """
        with self._lock:
            favourites = sorted(
                self._favourites.values(), key=lambda item: (item.freq_hz, item.service_id or 0)
            )
        payload = json.dumps(
            [favourite.model_dump(mode="json") for favourite in favourites], indent=2
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                # Flushed to the device before the rename, or a crash immediately afterwards
                # can leave the new name pointing at an empty file.
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _save_if_wanted(self) -> None:
        """Write the list out, unless the store was told not to."""
        if self._autosave:
            self.save()
