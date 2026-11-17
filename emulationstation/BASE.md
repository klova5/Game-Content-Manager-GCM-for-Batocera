# EmulationStation changes

GCM runs as its own build of Batocera's EmulationStation (`batocera-emulationstation`).
`gcm-es.patch` holds **all** of GCM's ES changes:

| File | Change |
|---|---|
| `es-app/src/guis/GuiGcmNative.h` | New: the GCM GUI (same file as `es-app/src/guis/GuiGcmNative.h` here) |
| `es-app/src/guis/GuiMenu.cpp` | Adds UPDATES and DLC to Advanced Game Options for supported systems |
| `es-app/src/guis/GuiGameOptions.cpp` | Shows Advanced Game Options for PS3 and PS4 |

## Bases

| Batocera build | ES commit | Status |
|---|---|---|
| 44-dev-d430acf21a (2026-07-13) | `cb0061588a8cbccbd47268283539cb0b95360074` | v3.6-preview builds |
| 44 (2026-11-16, batocera.linux `3ac8c39892`) | `04b2a9e6cee66e1b89434b8a2cfa23d952875e53` | patch applies cleanly; build pending |

Apply after Batocera's own ES patches (`package/batocera/emulationstation/batocera-emulationstation/*.patch`),
from the ES source root:

```bash
patch -p1 < gcm-es.patch
```

For a Batocera buildroot build, copy it into
`package/batocera/emulationstation/batocera-emulationstation/900-gcm.patch` and build
`batocera-emulationstation`.
