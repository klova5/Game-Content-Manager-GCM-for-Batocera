# Building the GCM frontend

The GCM frontend is Batocera's own EmulationStation with GCM's menus added. Since developer preview 4
it is built from the **same source and patches as Batocera v44 stable's frontend**.

## Source

1. Batocera EmulationStation at commit `04b2a9e6cee66e1b89434b8a2cfa23d952875e53` (the version Batocera 44
   stable ships), with its `external/pugixml` submodule:
   https://github.com/batocera-linux/batocera-emulationstation
2. Batocera's six v44 patches, applied in alphabetical order with `patch -p1`, from
   `package/batocera/emulationstation/batocera-emulationstation/` in the `batocera-44` branch of
   https://github.com/batocera-linux/batocera.linux:
   `001-no-next-slot`, `002-quit-add-shift`, `003-fix-sleep-theme-downloader`, `004-http-api-shutdown`,
   `batocera-emulationstation-battery-event`, `batocera-emulationstation-config-event-codes`.
3. GCM's changes:
   - copy [`es-app/src/guis/GuiGcmNative.h`](es-app/src/guis/GuiGcmNative.h) into the same folder;
   - apply [`gcm-emulationstation.patch`](gcm-emulationstation.patch) with `patch -p1`. It includes
     `GuiGcmNative.h` in `GuiMenu.cpp`, adds the **UPDATES** and **DLC** rows to a game's Advanced Game
     Options (`GuiMenu::popSpecificConfigurationGui`, before *Screen ratio choice*), and shows Advanced
     Game Options for PS3 and PS4 games (`GuiGameOptions.cpp`).

## Build

Cross-compile with a Batocera 44 x86_64 buildroot toolchain (Batocera's SDL2 is patched; plain SDL2 lacks
`SDL_Joystick*EventCodeById` and `SDL_JoystickDevicePathById`). Use Batocera's x86_64 options:

```
cmake -S <source> -B <build> -DCMAKE_TOOLCHAIN_FILE=<buildroot>/host/share/buildroot/toolchainfile.cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_FLAGS="-D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -O2 -g0 -D_FORTIFY_SOURCE=1 -DX86_64" \
  -DBATOCERA=ON -DGL=ON -DDISABLE_KODI=OFF -DENABLE_PULSE=ON -DENABLE_TTS=ON -DENABLE_FILEMANAGER=ON
cmake --build <build>
```

The release ships the result with debug data removed (`strip --strip-debug`).

## Rebasing on a new Batocera version

Take the EmulationStation commit and patches listed in that Batocera version's
`batocera-emulationstation.mk`, apply them, then copy `GuiGcmNative.h` and apply `gcm-emulationstation.patch`
(adjusting its context if the menu code moved). `GuiGcmNative.h` uses only EmulationStation's public GUI
classes, so it normally needs no changes.
