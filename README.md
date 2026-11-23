# GCM – Game Content Manager for Batocera (v3.6 developer preview)

GCM adds a **Game Content Manager** to Batocera's game menu (Advanced Game Options) and main menu. It finds,
verifies, installs, enables and uninstalls **updates and DLC** for each game, from your internal
storage and external drives, without touching your original files.

> **Developer preview.** This is the code as it runs on the author's machine, packaged so others can
> try it and read it. Expect rough edges, and keep backups of anything you care about.

## Supported

| | |
|---|---|
| Batocera | **44** stable, x86_64 (Xorg) and x86-64-v3 (Wayland/LabWC) |
| Nintendo Switch | Ryujinx, Citron, and emulators sharing Citron's storage such as Eden (updates and DLC) |
| Nintendo Switch 2 | Lichii (updates and DLC for NUP games) |
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

## Changes in developer preview 5

Release `GCM-v3.6-preview-5`, for Batocera 44 stable.

- **Switch 2 (Lichii):** updates and DLC for games stored as `.nup` files, on the same Detected / Available
  pages as the other consoles. GCM writes them into the game file with its own NUP tool
  (`system/add-ons/game-content-manager/switch2/gcm-nup.py`): the new file is written beside the old one,
  every package is checked and every copied byte compared, and only then does it replace the original.
  Anything taken out of a game is saved to GCM's Switch 2 store first (`/userdata/system/game-content/switch2`);
  **REMOVE GCM COPY** deletes a store copy of an item that is in the game.
- **Emulator scanner** (at every boot): finds new emulators and add-on consoles and decides whether GCM can
  manage them. Emulators that share a supported emulator's storage (e.g. Eden linked to Citron's folder) are
  handled automatically by that emulator's backend.
- **Content inspector** (at every boot): identifies update and DLC files by their contents (Switch and
  Switch 2 packages, PS4 / PS3 packages, Wii WADs, Xbox 360 packages, unpacked game folders), on every
  console GCM manages and on connected drives.
- **GAME CONTENT MANAGER** in the main menu: what the scanner and inspector found, what needs work before GCM
  can manage it, **RESCAN NOW**, and a manual **copy as-is** for files GCM cannot identify (with a warning;
  GCM cannot confirm such a file works).
- **Backup drive:** a connected drive with `roms/switch_update` (e.g. a backup HDD) is detected for Switch
  updates and DLC; when it is unplugged GCM uses the internal folder again.
- **Batocera x86-64-v3 (Wayland) image:** the permanent supervisor starts the GCM frontend inside the stock
  frontend's Wayland session (`WAYLAND_DISPLAY`, SDL/Qt Wayland settings) instead of forcing an Xorg display
  and cookie. On the x86_64 (Xorg) image nothing changes. Covered by `tests/test_supervisor_wayland.py`.
- A Citron content folder on the internal drive that is missing is created again, so it no longer blanks
  every Switch game's UPDATES and DLC.

## Changes in developer preview 4

Release `GCM-v3.6-preview-4` (5.7 MB), for Batocera 44 stable.


- **Frontend rebuilt on Batocera v44 stable's EmulationStation** (commit `04b2a9e`, with Batocera's own v44
  patches and build options). GCM's frontend now matches Batocera's: the v44 menu options, the power-button
  shutdown through EmulationStation, PulseAudio sound, text-to-speech and the x86_64-only options
  (e.g. *Hide EmulationStation when running a game*). Earlier previews used the July 2026 source.
- How to build it: [`emulationstation/BUILD.md`](emulationstation/BUILD.md).

## Changes in developer preview 3

Release `GCM-v3.6-preview-3` (5.4 MB). Tested on the Batocera v44 beta of Nov 16 2026.

- **Batocera v44 beta (61TI):** GCM falls back to whatever frontend Batocera ships, so a Batocera update no
  longer stops GCM from starting (it only refuses if something is mounted over the standard frontend).
  GCM finds the X display's cookie itself (Batocera 44 moved it to `/userdata/system/.serverauth.<n>`).
- **Citron (61TJ):** an update installed for both Ryujinx and Citron (two byte-identical copies, proven by
  their verified SHA-256) counts as one; Citron's own install copy is the active one. Before, the game's
  UPDATES and DLC showed UNKNOWN ("multiple indistinguishable active update registrations").
- **PS4 (61TJ):** while GCM checks a game's update and DLC files the menu says `ASSESSING` (was
  `UNASSESSED`), `CHECK FAILED` if the check fails, and `NOT RECOGNISED` for DLC folders it cannot
  identify. The value updates by itself when the check finishes (frontend change in `GuiGcmNative.h` and
  `gcm-emulationstation.patch`).

## Install

Download `gcm-v3.6-preview-5-batocera44-x86_64.zip` from this repository's
[Releases](https://github.com/klova5/Game-Content-Manager-GCM-for-Batocera/releases), copy it to
`/userdata/system` on the Batocera device, then from Batocera's terminal (or an SSH session on the device):

```bash
cd /userdata/system
unzip gcm-v3.6-preview-5-batocera44-x86_64.zip      # or: python3 -m zipfile -e gcm-v3.6-preview-5-batocera44-x86_64.zip .
cd gcm-v3.6-preview
bash install.sh --check     # only checks the device; writes nothing
bash install.sh --start     # installs and switches to GCM v3.6 (and at every boot)
```

`bash install.sh` without `--start` installs without switching; it prints the command to switch later.
Updating an earlier preview: `bash install.sh --replace --start` (the BUA app does this for you).

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
- `/userdata/system/logs/gcm-emulator-scan.txt` – what the emulator scanner found
- `/userdata/system/logs/gcm-content-inspection.txt` – what the content inspector found
- `/userdata/system/logs/gcm-uninstall-cleanup.log` – what uninstall cleanup deleted

## Known limitations

- Built for Batocera 44 on 64-bit PCs (the x86_64 and x86-64-v3 images); other versions and
  architectures are refused by the installer. On x86-64-v3 the supervisor change above is needed
  (not yet in a release zip).
- Folder names still carry development history (`/userdata/system/gcm-native-test/...`): hundreds of
  files locate and verify each other by exact path and checksum, so they are kept as they are for now.
- Citron loads every update/DLC file in its folders: an update or DLC that is *your own file* in one of
  Citron's folders cannot be uninstalled from GCM (GCM never deletes originals); remove or move the file.
- Switch 2: changing a game rewrites its `.nup` file (about 1-2 minutes for 30 GB) and needs free space for one
  copy of it next to the game. Lichii must be closed.
- A console the scanner reports as needing work still needs a GCM backend before its updates and DLC can be
  managed.

## Licence

GCM's own code is licensed under the **GNU General Public License v3.0** ([LICENSE](LICENSE)).
The GCM frontend in the release zip is a build of Batocera's EmulationStation and keeps its own licence
(see [NOTICE.md](NOTICE.md)).

## Repository layout

| Folder | Contents |
|---|---|
| `system/` | The GCM tools exactly as installed under `/userdata/system` (Python; the menu calls them) |
| `emulationstation/` | GCM's menu (`es-app/src/guis/GuiGcmNative.h`), its patch to Batocera's EmulationStation, and `BUILD.md` |
| `installer/` | `install.sh`, `uninstall.sh` and `gcm_install.py`, as shipped in the release zip |
| `tests/` | Offline fixtures: `bash tests/run_tests.sh` (Python 3; g++ for the frontend test; the `zstandard` module for the NUP tests) |
| `tools/` | `repin.py`: after editing files in `system/`, updates every checksum that pins them |

The release zip also contains the prebuilt GCM frontend (Batocera EmulationStation with GCM's menu),
built for Batocera 44 x86_64. How it is built from Batocera's EmulationStation, GCM's header and
[`gcm-emulationstation.patch`](emulationstation/gcm-emulationstation.patch): [`emulationstation/BUILD.md`](emulationstation/BUILD.md). See [NOTICE.md](NOTICE.md) for its licence.

Tools find and verify each other by exact path and SHA-256: editing a file in `system/` makes the tools
that pin it refuse to run until their pins are updated. `tools/repin.py` does that:
`python3 tools/repin.py system <relative/path>=<edited copy> ...`
