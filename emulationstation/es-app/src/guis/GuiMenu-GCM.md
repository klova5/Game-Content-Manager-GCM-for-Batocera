# GCM section of `GuiMenu.cpp`

The GCM frontend is Batocera's EmulationStation with two changes:

1. `es-app/src/guis/GuiGcmNative.h` (this folder) – all of GCM's menu code.
2. `es-app/src/guis/GuiMenu.cpp` – Batocera's own file, which must include `guis/GuiGcmNative.h` and
   gets the block below in the game's **Advanced Game Options**, just before *Screen ratio choice*.

The block adds the **UPDATES** and **DLC** rows. Their values come from GCM's summary; while either
says `ASSESSING`, the DLC value (`gcmV78LiveSummary`, 61TJ) re-reads the summary every 2 seconds in
the background and shows the result when the check finishes.

```cpp
// Game Content Manager v3 - native Advanced Game Options
#ifndef WIN32
if (fileData != nullptr &&
    (systemData->getName() == "switch" || systemData->getName() == "ps3" ||
     systemData->getName() == "ps4" || systemData->getName() == "wiiu" ||
     systemData->getName() == "psvita" || systemData->getName() == "xbox360" ||
     systemData->getName() == "3ds" || systemData->getName() == "wii" ||
     systemData->getName() == "psp"))
{
    auto source = fileData->getSourceFileData();
    if (source != nullptr &&
        !(systemData->getName() == "ps4" &&
          (Utils::FileSystem::getFileName(source->getPath()) == "DLC.sh" ||
           Utils::FileSystem::getFileName(source->getPath()) == "images.sh")))
    {
        const std::string rom = source->getPath();
        std::string emulator = currentEmulator;
        if (emulator.empty() || emulator == "auto") emulator = source->getEmulator(true);
        if (emulator.empty() || emulator == "auto") emulator = systemData->getEmulator(true);
        const auto systemName = systemData->getName();
        if (systemName == "ps3") emulator = "rpcs3";
        else if (systemName == "ps4") emulator = "shadps4";
        else if (systemName == "wiiu") emulator = "cemu";
        else if (systemName == "psvita") emulator = "vita3k";
        else if (systemName == "3ds") emulator = emulator == "libretro" ? "azahar-libretro" : "azahar";
        else if (systemName == "wii") emulator = "dolphin";
        else if (systemName == "psp") emulator = "ppsspp";
        else if (systemName == "xbox360" && (emulator.empty() || emulator == "auto")) emulator = "xenia";
        if (emulator.rfind("nextendo", 0) != 0 && emulator != "xemu")
        {
            const GcmV3Summary summary = gcmV3ReadSummary(rom, emulator);
            auto updateValue = gcmV3Value(mWindow, summary.update);
            auto dlcValue = gcmV78LiveSummary(mWindow, rom, emulator, summary.dlc, updateValue);
            const std::function<void()> refresh = [rom, emulator, updateValue, dlcValue]
            {
                const GcmV3Summary current = gcmV3ReadSummary(rom, emulator);
                updateValue->setText(current.update);dlcValue->setText(current.dlc);
            };
            systemConfiguration->addWithLabel(_("UPDATES"), updateValue, false,
                [mWindow, rom, emulator, refresh] { gcmV3OpenPage(mWindow, rom, emulator, "update", refresh); });
            systemConfiguration->addWithLabel(_("DLC"), dlcValue, false,
                [mWindow, rom, emulator, refresh] { gcmV3OpenPage(mWindow, rom, emulator, "dlc", refresh); });
        }
    }
}
#endif
```

Before 61TJ the DLC value was `gcmV3Value(mWindow, summary.dlc)`.
