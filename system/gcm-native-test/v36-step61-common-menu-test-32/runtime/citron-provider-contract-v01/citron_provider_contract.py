import json
import re
import sys
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIR = ROOT / "citron-inventory-adapter-v01"

sys.path.insert(0, str(ADAPTER_DIR))
from citron_inventory_adapter import build_report

class InventoryProvider(Protocol):
    def read_snapshot(self, selected_game: str) -> dict:
        ...

class SyntheticFixtureProvider:
    def __init__(self, fixture_path: Path):
        self.fixture_path = Path(fixture_path)

    def read_snapshot(self, selected_game: str) -> dict:
        if self.fixture_path.is_symlink():
            raise ValueError("Symlink fixture rejected")

        data = json.loads(
            self.fixture_path.read_text(encoding="utf-8")
        )

        if data.get("source") != "SYNTHETIC":
            raise ValueError("Non-synthetic input rejected")

        if data.get("selected_game", "").upper() != selected_game:
            raise ValueError("Fixture game mismatch")

        return data

class LiveCitronProvider:
    def read_snapshot(self, selected_game: str) -> dict:
        raise PermissionError(
            "Live Citron metadata access is disabled"
        )

def read_game_inventory(provider, selected_game: str):
    if not isinstance(selected_game, str):
        raise ValueError("Invalid game title ID")

    selected_game = selected_game.upper()

    if not re.fullmatch(r"[0-9A-F]{16}", selected_game):
        raise ValueError("Invalid game title ID")

    # Explicitly reject live and unknown providers.
    if type(provider) is not SyntheticFixtureProvider:
        raise PermissionError(
            "Only the synthetic provider is authorized"
        )

    snapshot = provider.read_snapshot(selected_game)
    report = build_report(snapshot)

    return {
        "schema_version": 1,
        "mode": "SYNTHETIC_TEST_ONLY",
        "selected_game": selected_game,
        "inventory": report,
        "evidence": {
            "registered_metadata_verified": False,
            "nand_inspected": False,
            "installation_verified": False,
            "activation_verified": False,
        },
        "capabilities": {
            "install": False,
            "delete": False,
            "enable": False,
            "activate": False,
            "modify_nand": False,
        },
    }
