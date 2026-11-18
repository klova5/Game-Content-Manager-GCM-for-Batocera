# GCM – Game Content Manager for Batocera (v3.6 developer preview)

GCM adds a **Game Content Manager** to Batocera's game menu (Advanced Game Options). It finds,
verifies, installs, enables and uninstalls **updates and DLC** for each game, from your internal
storage and external drives, without touching your original files.

> **Developer preview.** This is the code as it runs on the author's machine, packaged so others can
> try it and read it. Expect rough edges, and keep backups of anything you care about.

## Supported

| | |
|---|---|
| Batocera | **44**, x86_64 (tested on the July 2026 dev build and the Nov 16 2026 beta) |
| Nintendo Switch | Ryujinx, Citron (updates and DLC) |
| PlayStation 4 | shadPS4 (updates and DLC) |
| Other systems | RPCS3, Cemu, Vita3K and others: update management (varies by system) |

## What it does

- **Detected / Available lists** per game: content found on your drives, and content GCM has verified.
- **Verify** checks each package's integrity once; unchanged files are never read again (proof reuse).
- **Install / Enable / Apply Changes** registers verified content with the emulator.
- **Uninstall** removes the content from the emulator and deletes GCM's own copies; your original
  packages on internal and external drives are never deleted. Uninstalled items return to Detected.
- **Copy to GCM** keeps a GCM-managed copy of a Switch update or DLC (optional; Switch registers in place).
- Works with exFAT/FAT external drives (stable file identity even though those drives have no file numbers).

## Changes since v3.6-preview-2

These are in the source here and will be in the next release zip. All are running on the author's device except the self-updating `ASSESSING` value, which needs the frontend rebuild.

- **Batocera v44 beta (61TI):** GCM falls back to whatever frontend Batocera ships, so a Batocera update no
  longer stops GCM from starting (it only refuses if something is mounted over the standard frontend).
  GCM finds the X display's cookie itself (Batocera 44 moved it to `/userdata/system/.serverauth.<n>`).
- **Citron (61TJ):** an update installed for both Ryujinx and Citron (two byte-identical copies, proven by
  their verified SHA-256) counts as one; Citron's own install copy is the active one. Before, the game's
  UPDATES and DLC showed UNKNOWN ("multiple indistinguishable active update registrations").
- **PS4 (61TJ):** while GCM checks a game's update and DLC files the menu says `ASSESSING` (was
  `UNASSESSED`), `CHECK FAILED` if the check fails, and `NOT RECOGNISED` for DLC folders it cannot
  identify. The frontend change that makes `ASSESSING` update by itself is in `GuiGcmNative.h` and
  `GuiMenu-GCM.md`; it needs a frontend rebuild and is not in a release yet.

## Install

Download `gcm-v3.6-preview-2-batocera44-x86_64.zip` from this repository's
[Releases](https://github.com/klova5/Game-Content-Manager-GCM-for-Batocera/releases), copy it to
`/userdata/system` on the Batocera device, then from Batocera's terminal (or an SSH session on the device):

```bash
cd /userdata/system
unzip gcm-v3.6-preview-2-batocera44-x86_64.zip      # or: python3 -m zipfile -e gcm-v3.6-preview-2-batocera44-x86_64.zip .
cd gcm-v3.6-preview
bash install.sh --check     # only checks the device; writes nothing
bash install.sh --start     # installs and switches to GCM v3.6 (and at every boot)
```

`bash install.sh` without `--start` installs without switching; it prints the command to switch later.

## Switching frontends

GCM runs as its own build of Batocera's EmulationStation. The standard frontend stays installed.

```bash
python3 /userdata/system/add-ons/game-content-manager/v36-permanent/gcm-frontend-switch.py v36   # GCM
python3 /userdata/system/add-ons/game-content-manager/v36-permanent/gcm-frontend-switch.py v34   # standard
python3 /userdata/system/add-ons/game-content-manager/v36-permanent/gcm-frontend-switch.py status
```

## Uninstall

```bash
bash uninstall.sh           # back to the standard frontend; removes GCM's program files
bash uninstall.sh --purge   # also removes GCM's state and GCM-managed copies in /userdata/system/game-content
```

Games, saves and emulator settings are never removed.

## Logs

- `/userdata/system/logs/gcm-v36-debug.log` – errors caught in GCM menus, and crash details
- `/userdata/system/logs/gcm-v36-permanent.log` – frontend supervisor
- `/userdata/system/logs/gcm-uninstall-cleanup.log` – what uninstall cleanup deleted

## Known limitations

- Built for Batocera 44 x86_64 only; other versions are refused by the installer.
- Folder names still carry development history (`/userdata/system/gcm-native-test/...`): hundreds of
  files locate and verify each other by exact path and checksum, so they are kept as they are for now.
- Citron loads every update/DLC file in its folders: an update or DLC that is *your own file* in one of
  Citron's folders cannot be uninstalled from GCM (GCM never deletes originals); remove or move the file.

## Licence

GCM's own code is licensed under the **GNU General Public License v3.0** ([LICENSE](LICENSE)).
The GCM frontend in the release zip is a build of Batocera's EmulationStation and keeps its own licence
(see [NOTICE.md](NOTICE.md)).

## Repository layout

| Folder | Contents |
|---|---|
| `system/` | The GCM tools exactly as installed under `/userdata/system` (Python; the menu calls them) |
| `emulationstation/` | GCM's menu: `es-app/src/guis/GuiGcmNative.h`, compiled into Batocera's EmulationStation |
| `installer/` | `install.sh`, `uninstall.sh` and `gcm_install.py`, as shipped in the release zip |
| `tests/` | Offline fixtures: `bash tests/run_tests.sh` (Python 3; g++ for the frontend test) |
| `tools/` | `repin.py`: after editing files in `system/`, updates every checksum that pins them |

The release zip also contains the prebuilt GCM frontend (Batocera EmulationStation with GCM's menu),
built for Batocera 44 x86_64. Besides `GuiGcmNative.h`, the build adds one block to Batocera's `GuiMenu.cpp`:
see [`GuiMenu-GCM.md`](emulationstation/es-app/src/guis/GuiMenu-GCM.md). See [NOTICE.md](NOTICE.md) for its licence.

Tools find and verify each other by exact path and SHA-256: editing a file in `system/` makes the tools
that pin it refuse to run until their pins are updated. `tools/repin.py` does that:
`python3 tools/repin.py system <relative/path>=<edited copy> ...`
