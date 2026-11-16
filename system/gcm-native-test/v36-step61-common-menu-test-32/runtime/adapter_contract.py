"""
GCM v3.6 emulator adapter interface.

Development-only contract. No installation functions are
implemented or executed by this module.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Literal

ContentKind = Literal["update", "dlc"]

@dataclass(frozen=True)
class GameIdentity:
    system: str
    emulator: str
    title_id: str
    game_path: Path

@dataclass(frozen=True)
class ContentCandidate:
    source_path: Path
    kind: ContentKind
    title_id: str
    version: str | None = None

@dataclass(frozen=True)
class VerifiedContent:
    candidate: ContentCandidate
    sha256: str
    size_bytes: int

@dataclass(frozen=True)
class InstallationStatus:
    installed: bool
    active: bool | None
    location: Path | None
    version: str | None = None

class EmulatorAdapter(Protocol):
    emulator_id: str

    def identify_game(
        self, game_path: Path
    ) -> GameIdentity | None:
        ...

    def inspect_candidate(
        self, source_path: Path
    ) -> ContentCandidate | None:
        ...

    def verify_compatibility(
        self,
        game: GameIdentity,
        candidate: ContentCandidate
    ) -> VerifiedContent:
        ...

    def install_verified(
        self,
        game: GameIdentity,
        package: VerifiedContent
    ) -> InstallationStatus:
        ...

    def installation_status(
        self,
        game: GameIdentity,
        kind: ContentKind
    ) -> list[InstallationStatus]:
        ...
