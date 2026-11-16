#pragma once

#include "guis/GuiSettings.h"
#include "components/TextComponent.h"
#include "ThemeData.h"
#include "guis/GuiMsgBox.h"
#include "guis/GuiLoading.h"
#include <fstream>
#include "components/SwitchComponent.h"

#include <cstdio>
#include <cstdlib>
#include <functional>
#include <sstream>
#include <string>
#include <vector>
#include <atomic>
#include <chrono>
#include <map>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>
#include <memory>

// 61TA/61TC/61TD/61TF: GCM Python tools load the read profiler, proof reuse (with add-ons) and exFAT-stable file identity through
// PYTHONPATH. It is inert in any process that is not running a GCM script.
static const struct GcmV74ProofReusePath
{
    GcmV74ProofReusePath()
    {
        const char* dir = "/userdata/system/gcm-native-test/step61-common-content/gcm-proof-reuse-v04";
        const char* old = getenv("PYTHONPATH");
        std::string value = dir;
        if (old && *old && std::string(old).find(dir) == std::string::npos)
            value += std::string(":") + old;
        if (!old || std::string(old).find(dir) == std::string::npos)
            setenv("PYTHONPATH", value.c_str(), 1);
    }
} gcmV74ProofReusePathInit;

// 61TE: keep GCM's Citron install folder present (Citron and GCM keep it in Citron's folder list
// only while it exists), and write a crash report for any uncaught error before the frontend exits.
#include <sys/stat.h>
#include <execinfo.h>
#include <exception>
#include <typeinfo>
#include <ctime>
static void gcmV75Log(const std::string& where, const std::string& what)
{
    mkdir("/userdata/system/logs", 0755);
    std::ofstream log("/userdata/system/logs/gcm-v36-debug.log", std::ios::app);
    char stamp[32] = {0};
    const std::time_t now = std::time(nullptr);
    std::strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", std::localtime(&now));
    log << stamp << " " << where << ": " << what << "\n";
    void* frames[48];
    const int count = backtrace(frames, 48);
    char** names = backtrace_symbols(frames, count);
    for (int i = 0; names && i < count; ++i) log << "    " << names[i] << "\n";
    free(names);
}
static const struct GcmV75Startup
{
    GcmV75Startup()
    {
        struct stat info;
        if (stat("/userdata/system/game-content", &info) == 0 && S_ISDIR(info.st_mode))
            mkdir("/userdata/system/game-content/citron-registered", 0755);
        std::set_terminate([]()
        {
            std::string what = "unknown error";
            try { if (auto current = std::current_exception()) std::rethrow_exception(current); }
            catch (const std::exception& e) { what = std::string(typeid(e).name()) + ": " + e.what(); }
            catch (...) {}
            gcmV75Log("FRONTEND STOPPED (uncaught error)", what);
            std::abort();
        });
    }
} gcmV75StartupInit;

// 61TA: PS4 shows COPY SELECTED TO GCM only when content installed outside the GCM exists.
static std::map<std::string, bool>& gcmV74Ps4External()
{
    static std::map<std::string, bool> external;
    return external;
}

// Rebuild content pages only after their temporary dialogs have gone away.
// Requests remain owned by the originating page and coalesce until it is visible.
class GcmV66ContentPage : public GuiSettings
{
public:
    GcmV66ContentPage(Window* window, const std::string& title)
        : GuiSettings(window, title.c_str()), mRefreshWindow(window) {}

    void requestInventoryRefresh(const std::function<void()>& callback)
    {
        mInventoryRefresh = callback;
    }

    void update(int deltaTime) override
    {
        GuiSettings::update(deltaTime);
        if (!mInventoryRefresh || mRefreshQueued || mRefreshWindow->peekGui() != this)
            return;
        mRefreshQueued = true;
        mRefreshWindow->postToUiThread([this]()
        {
            mRefreshQueued = false;
            // An overlay may have opened after update queued this callback.
            // Retain the request and retry when this same page returns to the top.
            if (mRefreshWindow->peekGui() != this || !mInventoryRefresh) return;
            auto callback = mInventoryRefresh;
            mInventoryRefresh = nullptr;
            callback();
        }, this);
    }
    bool input(InputConfig* config, Input input) override
    {
        // 61TE: an error inside a GCM page action is reported instead of closing the frontend.
        try { return GuiSettings::input(config, input); }
        catch (const std::exception& e)
        {
            gcmV75Log("GCM page action", e.what());
            mRefreshWindow->pushGui(new GuiMsgBox(mRefreshWindow,
                _("GCM HIT AN INTERNAL ERROR AND STOPPED THIS ACTION.\nDETAILS: /userdata/system/logs/gcm-v36-debug.log"), _("OK")));
            return true;
        }
    }
private:
    Window* mRefreshWindow;
    bool mRefreshQueued = false;
    std::function<void()> mInventoryRefresh;
};

static void gcmV66QueueRefresh(Window*, const std::function<void()>& callback,
    GcmV66ContentPage* page)
{
    page->requestInventoryRefresh(callback);
}

// List presentation is scoped to this game's emulator/content section for this session.
static std::map<std::string, bool>& gcmV67MinimizedLists()
{
    static std::map<std::string, bool> values;
    return values;
}
static std::string gcmV67ListScope(const std::string& rom, const std::string& emulator, const std::string& kind)
{ return rom + "\n" + emulator + "\n" + kind; }

// Collapsed item rows remain alive offscreen so their job labels still poll and
// complete. Selection controls are built normally; only their rendering changes.
class GcmV67ContentPage;
// 61SY: the open content page for each list scope; a page removes itself when destroyed.
static std::map<std::string, GcmV67ContentPage*>& gcmV72LivePages()
{ static std::map<std::string, GcmV67ContentPage*> pages;return pages; }

class GcmV67ContentPage : public GcmV66ContentPage
{
public:
    ~GcmV67ContentPage()
    {
        auto& live = gcmV72LivePages();
        for (auto it = live.begin(); it != live.end();)
            if (it->second == this) it = live.erase(it); else ++it;
    }
    // 61SY: action rows by position, so a rebuilt page returns the cursor to the action used.
    bool markAction(const std::string& id) { mActions[(int)getMenu().size()] = id;return true; }
    std::string actionAtCursor()
    { const auto it = mActions.find(getMenu().getCursorIndex());return it == mActions.end() ? "" : it->second; }
    int actionRow(const std::string& id) const
    { for (const auto& item : mActions) if (item.second == id) return item.first;return -1; }
    GcmV67ContentPage(Window* window, const std::string& title, bool minimized)
        : GcmV66ContentPage(window, title), mMinimized(minimized)
    {
        if (minimized)
        {
            mHiddenRows.reset(new GuiSettings(window, title.c_str()));
            mHiddenRows->setUpdateType(ComponentListFlags::UpdateType::UPDATE_ALWAYS);
        }
    }
    void collectListRows(bool collecting) { mCollecting = collecting; }
    GuiSettings* listTarget()
    { return mMinimized && mCollecting ? mHiddenRows.get() : static_cast<GuiSettings*>(this); }
    template<class... Args> void addWithLabel(Args&&... args)
    { listTarget()->addWithLabel(std::forward<Args>(args)...); }
    template<class... Args> void addRow(Args&&... args)
    { listTarget()->addRow(std::forward<Args>(args)...); }
    template<class... Args> void addEntry(Args&&... args)
    { listTarget()->addEntry(std::forward<Args>(args)...); }
    void update(int deltaTime) override
    {
        GcmV66ContentPage::update(deltaTime);
        if (mHiddenRows) mHiddenRows->update(deltaTime);
    }
private:
    bool mMinimized, mCollecting = false;
    std::unique_ptr<GuiSettings> mHiddenRows;
    std::map<int, std::string> mActions;
};

static std::string gcmV67Summary(const std::string& caption, unsigned selected, unsigned total, bool selectable)
{ return caption + (selectable ? std::to_string(selected) + "/" : "") + std::to_string(total); }

static std::map<std::string, bool>& gcmV68FocusMinimize()
{ static std::map<std::string, bool> values;return values; }
struct GcmV68ReadContext
{
    std::map<std::string, std::string> statuses;
    unsigned commands = 0, cacheHits = 0, localReads = 0;
    long long readerMs = 0, toolMs = 0;
};
static thread_local GcmV68ReadContext* gcmV68Reading = nullptr;
class GcmV68MenuReadScope
{
    GcmV68ReadContext mContext;
    GcmV68ReadContext* mPrevious;
    std::chrono::steady_clock::time_point mStart;
public:
    GcmV68MenuReadScope() : mPrevious(gcmV68Reading), mStart(std::chrono::steady_clock::now())
    { gcmV68Reading = &mContext; }
    ~GcmV68MenuReadScope()
    {
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-mStart).count();
        std::ofstream log("/tmp/gcm-step61SM-menu-timings.log", std::ios::app);
        if (log) log << "MENU elapsed_ms=" << elapsed << " reader_ms=" << mContext.readerMs
            << " tool_ms=" << mContext.toolMs << " tool_commands=" << mContext.commands
            << " status_cache_hits=" << mContext.cacheHits << " local_status_reads=" << mContext.localReads << "\n";
        gcmV68Reading = mPrevious;
    }
};
class GcmV68ReadTimer
{
    bool mReader;
    std::chrono::steady_clock::time_point mStart;
public:
    explicit GcmV68ReadTimer(bool reader) : mReader(reader), mStart(std::chrono::steady_clock::now()) {}
    ~GcmV68ReadTimer()
    {
        if (!gcmV68Reading) return;
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-mStart).count();
        if (mReader) gcmV68Reading->readerMs += elapsed;else gcmV68Reading->toolMs += elapsed;
    }
};

struct GcmV3Summary
{
    std::string update = "UNKNOWN";
    std::string dlc = "UNKNOWN";
};

struct GcmV3Row
{
    std::string section;
    std::string state;
    std::string label;
    std::string token;
};

static std::string gcmV3Quote(const std::string& value)
{
    std::string result = "'";

    for (char c : value)
    {
        if (c == '\'')
            result += "'\"'\"'";
        else
            result += c;
    }

    return result + "'";
}

static bool gcmV36IsCitron(const std::string& emulator)
{
    return emulator == "citron-emu" || emulator == "citron";
}

static bool gcmV36IsRyujinx(const std::string& emulator)
{
    return emulator == "ryujinx" || emulator == "ryujinx-emu";
}

static bool gcmV36IsSharedSwitch(const std::string& emulator)
{
    return gcmV36IsCitron(emulator) || gcmV36IsRyujinx(emulator);
}


static bool gcmV63IsExtraProvider(const std::string& emulator)
{
    return emulator == "xenia" || emulator == "xenia-canary" || emulator == "xenia-edge"
        || emulator == "xenia_canary" || emulator == "xenia_edge"
        || emulator == "azahar" || emulator == "azahar-libretro" || emulator == "libretro-azahar"
        || emulator == "citra" || emulator == "citra-emu"
        || emulator == "dolphin" || emulator == "dolphin-emu"
        || emulator == "ppsspp" || emulator == "PPSSPP";
}

static std::string gcmV63ScanJobPath(const std::string& emulator)
{
    return gcmV63IsExtraProvider(emulator)
        ? "/userdata/system/gcm-native-test/step61-common-content/beta-global-update-v01/gcm-global-update-jobs-v01.py"
        : "/userdata/system/gcm-native-test/step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py";
}

static bool gcmV61IsNativeFolder(const std::string& emulator)
{
    return emulator == "rpcs3" || emulator == "cemu" || emulator == "vita3k";
}

static bool gcmV61UsesGlobalContentMenu(const std::string& emulator)
{
    return gcmV61IsNativeFolder(emulator) || gcmV63IsExtraProvider(emulator) || gcmV36IsSharedSwitch(emulator)
        || emulator == "shadps4" || emulator == "rpcs3";
}

static bool gcmV61CommandFailed(const std::string& output);

static std::string gcmV3Run(
    const std::string& rom,
    const std::string& emulator,
    const std::string& args)
{
    GcmV68ReadTimer readTimer(true);
    const std::string command =
        "python3 " +
        gcmV3Quote(
            gcmV63IsExtraProvider(emulator)
                ? "/userdata/system/gcm-native-test/step61-common-content/beta-global-update-v01/gcm-global-update-workflow-v01.py"
                : gcmV61IsNativeFolder(emulator)
                ? "/userdata/system/gcm-native-test/step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py"
                : gcmV36IsSharedSwitch(emulator)
                ? "/userdata/system/gcm-native-test/"
                  "step61-common-content/beta-switch-dlc-inplace-v02/gcm-switch-fresh-menu-v02.py"
                : emulator == "shadps4"
                ? "/userdata/system/gcm-native-test/"
                  "step61-common-content/gcm-global-menu-existing-v01.py"
                : emulator == "rpcs3"
                    ? "/userdata/system/gcm-native-test/"
                      "step61-common-content/gcm-platform-global-backend-fast-menu-v02.py"
                    : "/userdata/system/add-ons/game-content-manager/"
                      "gcm-native-v33.py"
        ) +
        " --rom " + gcmV3Quote(rom) +
        " --emulator " + gcmV3Quote(emulator) +
        " " + args + " 2>&1";

    FILE* pipe = popen(command.c_str(), "r");

    if (!pipe)
        return "ERROR\tUNABLE TO START CONTENT READER\n";

    std::string output;
    char buffer[2048];

    while (fgets(buffer, sizeof(buffer), pipe))
    {
        if (output.size() >= 65536)
            break;

        output += buffer;
    }

    const int result = pclose(pipe);

    if (result != 0 &&
        !gcmV61CommandFailed(output))
    {
        return "ERROR\tINVENTORY REQUEST FAILED\n";
    }

    return output;
}


// GCM v3.4: separate preview runner.
// Only the plan-update operation is used by this native test build.
static std::string gcmV34Run(
    const std::string& rom,
    const std::string& emulator,
    const std::string& args)
{
    const std::string command =
        "python3 " +
        gcmV3Quote(
            "/userdata/system/gcm-native-test/v34-development/"
            "gcm-native-v34.py"
        ) +
        " --rom " + gcmV3Quote(rom) +
        " --emulator " + gcmV3Quote(emulator) +
        " " + args + " 2>&1";

    FILE* pipe = popen(command.c_str(), "r");

    if (!pipe)
        return "ERROR\tUNABLE TO START V3.4 PLANNER\n";

    std::string output;
    char buffer[2048];

    while (fgets(buffer, sizeof(buffer), pipe))
    {
        if (output.size() >= 65536)
            break;

        output += buffer;
    }

    const int result = pclose(pipe);

    if (result != 0)
        return "ERROR\tUPDATE PLAN FAILED\n" + output;

    return output;
}


// GCM v3.5: quick background verification start/status calls.
static std::string gcmV35BackgroundRun(
    const std::string& rom,
    const std::string& token,
    const std::string& action)
{
    if (action != "start" && action != "status")
        return "ERROR\tINVALID ACTION\n";

    const std::string command =
        "python3 " +
        gcmV3Quote(
            "/userdata/system/gcm-native-test/v34-development/"
            "gcm-v35-background.py"
        ) +
        " " + action +
        " --rom " + gcmV3Quote(rom) +
        " --token " + gcmV3Quote(token) +
        " 2>&1";

    FILE* pipe = popen(command.c_str(), "r");

    if (!pipe)
        return "ERROR\tCANNOT START VERIFICATION\n";

    std::string output;
    char buffer[2048];

    while (fgets(buffer, sizeof(buffer), pipe))
    {
        output += buffer;
        if (output.size() > 8192)
            break;
    }

    const int code = pclose(pipe);

    if (code != 0 &&
        !gcmV61CommandFailed(output))
        output += "ERROR\tVERIFICATION COMMAND FAILED\n";

    return output;
}


// GCM v3.5: isolated verification-job and verified-inventory clients.
// Shared presentation and command-error rules for every content route.
static bool gcmV61CommandFailed(const std::string& output)
{
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
    {
        if (line.rfind("ERROR\t", 0) == 0) return true;
        for (const auto& prefix : {std::string("BATCH\tERROR\t"), std::string("INSTALL\tERROR\t")})
            if (line.rfind(prefix, 0) == 0 && line.size() > prefix.size()) return true;
        if (line == "BATCH\tSTATUS\tERROR" || line == "INSTALL\tSTATUS\tERROR") return true;
    }
    return false;
}

static std::string gcmV61DialogText(const std::string& text)
{
    std::string readable;
    unsigned column = 0;
    for (char c : text.substr(0, 2800))
    {
        if (c == '\t' || c == '\r') c = ' ';
        if (c == '\n') column = 0;
        else if (++column > 64) { readable += '\n'; column = 1; }
        readable += c;
    }
    return readable;
}

static std::string gcmV68RawRunTool(const std::string& command)
{
    FILE* pipe = popen((command + " 2>&1").c_str(), "r");
    if (!pipe)
        return "ERROR\tCANNOT LAUNCH GCM V3.5 TOOL\n";

    std::string result;
    char buffer[2048];
    while (fgets(buffer, sizeof(buffer), pipe))
    {
        result += buffer;
        if (result.size() > 262144)
            break;
    }
    const int code = pclose(pipe);
    if (code != 0 && !gcmV61CommandFailed(result))
        result += "ERROR\tGCM V3.5 TOOL FAILED\n";
    return result;
}

static std::string gcmV35RunTool(const std::string& command)
{
    const bool memoize = gcmV68Reading && command.find(" status ") != std::string::npos;
    if (memoize)
    {
        const auto found = gcmV68Reading->statuses.find(command);
        if (found != gcmV68Reading->statuses.end()) { ++gcmV68Reading->cacheHits;return found->second; }
    }
    GcmV68ReadTimer timer(false);
    if (gcmV68Reading) ++gcmV68Reading->commands;
    const auto result = gcmV68RawRunTool(command);
    if (memoize) gcmV68Reading->statuses[command] = result;
    return result;
}

static std::string gcmV68VerifyRun(const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& action, const std::string& tokens = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-dlc-stage-separation-v04/gcm-selected-verification-v01.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --type " + gcmV3Quote(kind) + " --tokens " + gcmV3Quote(tokens) + ((action == "verify" || action == "verify-detected") ? " --confirm" : ""));
}

// Package verification is exposed only for connected adapters.
// Discovery and scanner support do not imply a package verification provider.
static bool gcmV61CanVerifyPackages(const std::string& emulator,
                                    const std::string& type)
{
    return (type == "update" || type == "dlc")
        && (emulator == "shadps4" || gcmV36IsSharedSwitch(emulator));
}

static std::string gcmV35BatchRun(const std::string& rom,
                                 const std::string& emulator,
                                 const std::string& action,
                                 const std::string& tokens,
                                 const std::string& type = "update")
{
    if (action != "start" && action != "status")
        return "ERROR\tINVALID BATCH ACTION\n";

    if (gcmV68Reading && action == "status" && type.rfind("local:", 0) == 0)
    {
        const auto key = "local-status\n" + rom + "\n" + emulator;
        auto found = gcmV68Reading->statuses.find(key);
        if (found == gcmV68Reading->statuses.end())
        {
            ++gcmV68Reading->localReads;
            found = gcmV68Reading->statuses.emplace(key, gcmV68VerifyRun(rom, emulator, "update", "local-status")).first;
        }
        else ++gcmV68Reading->cacheHits;
        const std::string prefix = "BATCH\tMATCH_TOKENS\t";
        std::istringstream lines(found->second);std::string line;
        while (std::getline(lines, line))
            if (line.rfind(prefix, 0) == 0 && line.substr(prefix.size()) == type.substr(6)) return found->second;
        if (gcmV61CommandFailed(found->second)) return found->second;
        return "BATCH\tSTATUS\tNOT_STARTED\n";
    }

    if (type.rfind("dlc-install:", 0) == 0 && gcmV36IsSharedSwitch(emulator))
    {
        return gcmV35RunTool("python3 -B " + gcmV3Quote(
            "/userdata/system/gcm-native-test/step61-common-content/beta-dlc-stage-separation-v04/gcm-dlc-stage-workflow-v01.py")
            + (action == "start" ? " install" : " install-status") + " --rom " + gcmV3Quote(rom)
            + " --emulator " + gcmV3Quote(emulator) + " --tokens " + gcmV3Quote(type.substr(12))
            + (action == "start" ? " --confirm" : ""));
    }
    if (type == "dlc" && gcmV36IsSharedSwitch(emulator)) return "BATCH\tSTATUS\tNOT_STARTED\n";

    if (gcmV63IsExtraProvider(emulator))
    {
        std::string operation = "prepare", kind = type == "dlc" ? "dlc" : "update", selected = tokens;
        if (type.rfind("native:", 0) == 0)
        {
            const auto first = type.find(':', 7);
            const auto second = first == std::string::npos ? first : type.find(':', first + 1);
            if (second == std::string::npos) return "ERROR\tINVALID UPDATE JOB SCOPE\n";
            operation = type.substr(7, first - 7);kind = type.substr(first + 1, second - first - 1);selected = type.substr(second + 1);
        }
        if (kind != "update") return "ERROR\tTHIS PROVIDER ACTION MANAGES UPDATES ONLY\n";
        return gcmV35RunTool("python3 -B " + gcmV3Quote(gcmV63ScanJobPath(emulator))
            + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
            + " --type update --operation " + gcmV3Quote(operation) + " --tokens " + gcmV3Quote(selected)
            + (action == "start" ? " --confirm" : ""));
    }

    if (gcmV61IsNativeFolder(emulator))
    {
        std::string kind = type == "update" ? "update" : "dlc";
        std::string operation = type == "dlc-apply" ? "apply"
            : type == "dlc-uninstall" ? "uninstall" : "prepare";
        std::string selected = tokens;
        if (type.rfind("native:", 0) == 0)
        {
            const auto first = type.find(':', 7);
            const auto second = first == std::string::npos ? first : type.find(':', first + 1);
            if (second == std::string::npos) return "ERROR\tINVALID NATIVE JOB SCOPE\n";
            operation = type.substr(7, first - 7);
            kind = type.substr(first + 1, second - first - 1);
            selected = type.substr(second + 1);
        }
        if (kind != "update" && kind != "dlc") return "ERROR\tINVALID NATIVE CONTENT TYPE\n";
        std::string command = "python3 -B " + gcmV3Quote(
            "/userdata/system/gcm-native-test/step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py")
            + " " + action + " --rom " + gcmV3Quote(rom)
            + " --emulator " + gcmV3Quote(emulator) + " --type " + gcmV3Quote(kind)
            + " --operation " + gcmV3Quote(operation) + " --tokens " + gcmV3Quote(selected);
        if (action == "start") command += " --confirm";
        return gcmV35RunTool(command);
    }

    if ((type == "update" || type == "dlc")
        && !gcmV61CanVerifyPackages(emulator, type))
        return "ERROR\tPACKAGE VERIFICATION ADAPTER NOT CONNECTED\n";

    if (type.rfind("local:", 0) == 0)
    {
        std::string command = "python3 -B " +
            gcmV3Quote("/userdata/system/gcm-native-test/step61-common-content/gcm-global-install-locally-job-v01.py")
            + " " + action + " --rom " + gcmV3Quote(rom)
            + " --emulator " + gcmV3Quote(emulator)
            + " --tokens " + gcmV3Quote(type.substr(6));
        if (action == "start") command += " --confirm";
        return gcmV35RunTool(command);
    }

    if (type == "dlc-uninstall")
    {
        std::string command = "python3 -B " +
            gcmV3Quote("/userdata/system/gcm-native-test/step61-common-content/gcm-global-uninstall-job-v01.py")
            + " " + action + " --rom " + gcmV3Quote(rom)
            + " --emulator " + gcmV3Quote(emulator);
        if (action == "start") command += " --tokens " + gcmV3Quote(tokens) + " --confirm";
        return gcmV35RunTool(command);
    }

    if ((type == "dlc" || type == "dlc-apply")
        && (emulator == "shadps4" || gcmV36IsSharedSwitch(emulator)))
    {
        std::string command = "python3 -B " +
            gcmV3Quote("/userdata/system/gcm-native-test/"
                       "step61-common-content/beta-shared-dlc-jobs-v03/gcm-shared-dlc-workflow-v02.py") +
            " " + action + " --rom " + gcmV3Quote(rom) +
            " --emulator " + gcmV3Quote(emulator) +
            " --operation " + (type == "dlc-apply" ? "apply" : "prepare");
        if (action == "start")
            command += " --tokens " + gcmV3Quote(tokens) + " --confirm";
        return gcmV35RunTool(command);
    }

    if (emulator == "shadps4")
    {
        std::string command =
            "/usr/bin/timeout -k 2s 10s /usr/bin/python3 -B " +
            gcmV3Quote("/userdata/system/gcm-native-test/"
                       "step61-common-content/gcm-platform-native-verification.py") +
            " " + action + " --rom " + gcmV3Quote(rom) +
            " --emulator shadps4 --type " + gcmV3Quote(type);
        if (action == "start")
            command += " --tokens " + gcmV3Quote(tokens);
        return gcmV35RunTool(command);
    }

    const std::string tool = gcmV36IsSharedSwitch(emulator)
        ? "/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-batch.py"
        : "/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verification-batch.py";
    std::string command = "python3 " + gcmV3Quote(tool)
        + " " + action + " --rom " + gcmV3Quote(rom);

    if (gcmV36IsSharedSwitch(emulator))
        command += " --type " + gcmV3Quote(type);
    if (action == "start")
        command += " --emulator " + gcmV3Quote(emulator)
                 + " --tokens " + gcmV3Quote(tokens);
    return gcmV35RunTool(command);
}

static std::string gcmV35BatchField(const std::string& output,
                                   const std::string& field)
{
    const std::string prefix = "BATCH\t" + field + "\t";
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
        if (line.rfind(prefix, 0) == 0)
            return line.substr(prefix.size());
    return "";
}

static std::string gcmV35FailureMessage(const std::string& output)
{
    std::string failures;
    const std::string prefix = "BATCH\tFAILURE\t";
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
    {
        if (line.rfind(prefix, 0) != 0)
            continue;
        const std::string rest = line.substr(prefix.size());
        const size_t split = rest.find('\t');
        if (split == std::string::npos)
            continue;
        if (!failures.empty())
            failures += "\n\n";
        failures += rest.substr(0, split) + "\n" + rest.substr(split + 1);
        if (failures.size() > 2800)
            break;
    }
    if (failures.empty())
        {
        failures = gcmV35BatchField(output, "ERROR");
        if (failures.empty())
        {
            std::istringstream errors(output);
            while (std::getline(errors, line))
                if (line.rfind("ERROR\t", 0) == 0) { failures = line.substr(6); break; }
        }
        if (failures.empty()) failures = _("The content operation did not complete. Check its status before retrying.");
    }
    return gcmV61DialogText(std::string(_("CONTENT OPERATION FAILED")) + "\n\n" + failures);
}

static std::string gcmV35VerifiedInventoryRun(const std::string& rom,
                                               const std::string& emulator)
{
    return gcmV35RunTool(
        "python3 " + gcmV3Quote(
            "/userdata/system/gcm-native-test/v34-development/"
            "v35-native-development/gcm-v35-verified-inventory.py"
        ) + " --rom " + gcmV3Quote(rom)
          + " --emulator " + gcmV3Quote(emulator)
          + " --mode list --type update"
    );
}


// v3.5: start installation in a detached worker, poll small JSON status only.
static std::string gcmV35InstallRun(const std::string& action,
                                     const std::string& rom,
                                     const std::string& token,
                                      const std::string& emulator = "ryujinx",
                                      const std::string& type = "update")
{
    if (emulator == "shadps4" || emulator == "rpcs3")
    {
        if (action != "start" && action != "status")
            return "ERROR\tINVALID INSTALL ACTION\n";
        return gcmV35RunTool(
            "python3 -B " +
            gcmV3Quote("/userdata/system/gcm-native-test/"
                       "step61-common-content/gcm-global-menu-existing-v01.py") +
            " --mode install-job --action " + gcmV3Quote(action) +
            " --rom " + gcmV3Quote(rom) +
            " --emulator " + gcmV3Quote(emulator) +
            " --type " + gcmV3Quote(type) +
            " --token " + gcmV3Quote(token));
    }

    if (action != "start" && action != "status")
        return "ERROR\tINVALID INSTALL ACTION\n";
    return gcmV35RunTool(
        "python3 " + gcmV3Quote(gcmV36IsCitron(emulator)
            ? "/userdata/system/gcm-native-test/step60-shared-switch/citron-content-install-job.py"
            : gcmV36IsRyujinx(emulator)
                ? "/userdata/system/gcm-native-test/step60-shared-switch/ryujinx-content-install-job.py"
                : "/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-install-job.py") + " " + action + " --rom " + gcmV3Quote(rom)
          + " --token " + gcmV3Quote(token)
          + (gcmV36IsSharedSwitch(emulator)
                 ? " --type " + gcmV3Quote(type) : "")
    );
}

static std::string gcmV35InstallField(const std::string& output,
                                      const std::string& field)
{
    const std::string prefix = "INSTALL\t" + field + "\t";
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
        if (line.rfind(prefix, 0) == 0)
            return line.substr(prefix.size());
    return "";
}

class GcmV35InstallActionLabel : public TextComponent
{
    std::string mContentType;
public:
    GcmV35InstallActionLabel(Window* window, const std::string& rom,
                             const std::string& token,
                              const std::string& emulator = "ryujinx",
                              const std::string& type = "update")
        : TextComponent(window, _("INSTALL"),
                        ThemeData::getMenuTheme()->Text.font,
                        ThemeData::getMenuTheme()->Text.color),
          mContentType(type), mEmulator(emulator), mRom(rom), mToken(token), mElapsed(0)
    {
        setStatusText(_("INSTALL"));
        // Read only a small existing job record. Never hash an NSP in GUI update.
        const std::string reply = gcmV35InstallRun("status", mRom, mToken, mEmulator, mContentType);
        const std::string state = gcmV35InstallField(reply, "STATUS");
        const std::string id = gcmV35InstallField(reply, "JOB_ID");
        if (state == "RUNNING" && !id.empty())
            watch(id);
        else if (gcmV35InstallField(reply, "LOCAL_STATE") == "READY")
            setStatusText(_("READY"));
    }


    // Preserve a constant width for every installation status.
    // Prevent INSTALLING... from being clipped after INSTALL changes.
    void setStatusText(const std::string& value)
    {
        const auto font = ThemeData::getMenuTheme()->Text.font;

        float width = font->sizeText(_("INSTALLING...")).x();

        const float installedWidth =
            font->sizeText(_("INSTALLED")).x();
        const float installWidth =
            font->sizeText(_("INSTALL")).x();
        const float readyWidth =
            font->sizeText(_("READY")).x();

        if (installedWidth > width) width = installedWidth;
        if (installWidth > width) width = installWidth;
        if (readyWidth > width) width = readyWidth;

        width += 16.0f;

        setText(value);
        setSize(width, getSize().y());
        setHorizontalAlignment(ALIGN_RIGHT);
    }

    bool busy() const { return mWatching || mPending; }
    void watch(const std::string& id)
    {
        mJobId = id;
        mElapsed = 0;
        mPollErrors = 0;
        mWatching = true;
        mPending = false;
        setStatusText(_("INSTALLING..."));
    }
    void setCompletionCallback(
        const std::function<bool(const std::string&, const std::string&)>& cb)
    {
        mOnComplete = cb;
    }
    void update(int deltaTime) override
    {
        TextComponent::update(deltaTime);
        if (!busy())
            return;
        mElapsed += deltaTime;
        if (mElapsed < 2000)
            return;
        mElapsed = 0;
        if (mWatching)
        {
            const std::string output = gcmV35InstallRun("status", mRom, mToken, mEmulator, mContentType);
            const std::string id = gcmV35InstallField(output, "JOB_ID");
            const std::string state = gcmV35InstallField(output, "STATUS");
            if (id == mJobId && (state == "INSTALLED" || state == "ERROR"))
            {
                mWatching = false;
                mPending = true;
                mResult = output;
                mResultState = state;
            }
            else if (output.find("ERROR\t") != std::string::npos ||
                     (!id.empty() && id != mJobId) || state == "NOT_STARTED")
            {
                if (++mPollErrors >= 3)
                {
                    mWatching = false;
                    mPending = true;
                    mResultState = "ERROR";
                    mResult = output + "\nINSTALL\tERROR\tInstaller job unavailable\n";
                }
            }
            else
                mPollErrors = 0;
        }
        if (mPending && mOnComplete && mOnComplete(mResultState, mResult))
        {
            mPending = false;
            setStatusText(mResultState == "INSTALLED" ? _("INSTALLED") : _("READY"));
        }
    }
private:
    std::string mEmulator;
    std::string mRom, mToken, mJobId, mResult, mResultState;
    int mElapsed;
    int mPollErrors = 0;
    bool mWatching = false;
    bool mPending = false;
    std::function<bool(const std::string&, const std::string&)> mOnComplete;
};

// Polls background content work; renders measured phase progress inline.
static std::string gcmV61NativeCaption(const std::string& type, bool running)
{
    if (type.find(":scan") != std::string::npos) return running ? _("CANCEL SCAN") : _("SCAN SYSTEM");
    if (type.find(":install:") != std::string::npos) return running ? _("INSTALLING") : _("INSTALL");
    if (type.find(":enable:") != std::string::npos) return running ? _("APPLYING CHANGES") : _("SELECT");
    if (type.find(":disable:") != std::string::npos) return running ? _("APPLYING CHANGES") : _("ACTIVE");
    if (type.find(":recover:") != std::string::npos) return running ? _("RECOVERING") : _("RECOVER");
    if (type.find(":uninstall:") != std::string::npos || type == "dlc-uninstall")
        return running ? _("UNINSTALLING") : _("UNINSTALL SELECTED ITEMS");
    if (type == "dlc-apply") return running ? _("APPLYING CHANGES") : _("APPLY CHANGES");
    return running ? _("COPYING FILES") : _("INSTALL LOCALLY");
}

class GcmV35BatchActionLabel : public TextComponent
{
    std::string mContentType;
public:
    GcmV35BatchActionLabel(Window* window, const std::string& rom,
                           const std::string& emulator,
                           const std::string& type = "update")
        : TextComponent(window, (gcmV61IsNativeFolder(emulator) || gcmV63IsExtraProvider(emulator)) ? gcmV61NativeCaption(type, false) : type.rfind("dlc-install:", 0) == 0 ? _("INSTALL DLC") : type.rfind("local:", 0) == 0 ? _("INSTALL LOCALLY") : type == "dlc-uninstall" ? _("UNINSTALL SELECTED ITEMS") : type == "dlc-apply" ? _("APPLY CHANGES") : type == "dlc"
                            ? _("VERIFY SELECTED DLC")
                            : _("VERIFY SELECTED UPDATES"),
                        ThemeData::getMenuTheme()->Text.font,
                        ThemeData::getMenuTheme()->Text.color),
          mContentType(type), mRom(rom), mEmulator(emulator), mElapsed(0)
    {
        setSize(Renderer::getScreenWidth() * 0.40f, getFont()->getHeight());
        setHorizontalAlignment(ALIGN_RIGHT);
        setMultiLine(TextComponent::SINGLELINE);
        setAutoScroll(TextComponent::HORIZONTAL);
    }

    void setCompletionCallback(
        const std::function<bool(const std::string&, const std::string&)>& cb)
    {
        mOnCompletion = cb;
    }

    void setContentType(const std::string& type) { mContentType = type; }
    bool busy() const { return mWatching || mPending; }

    void watch(const std::string& batchId)
    {
        mBatchId = batchId;
        mElapsed = 0;
        mPollErrors = 0;
        mWatching = true;
        mPending = false;
        setText((gcmV61IsNativeFolder(mEmulator) || gcmV63IsExtraProvider(mEmulator)) ? gcmV61NativeCaption(mContentType, mWatching) : mContentType.rfind("local:", 0) == 0 ? _("COPYING") : mContentType == "dlc-uninstall" ? _("UNINSTALLING") : mContentType == "dlc-apply" ? _("APPLYING CHANGES") : mContentType.rfind("dlc-install:", 0) == 0 ? _("INSTALLING DLC...") : mContentType == "dlc"
                    ? _("VERIFYING DLC...") : _("VERIFYING UPDATES..."));
    }

    void update(int deltaTime) override
    {
        TextComponent::update(deltaTime);
        if (!busy())
            return;
        mElapsed += deltaTime;
        if (mElapsed < 1500)
            return;
        mElapsed = 0;

        if (mWatching)
        {
            const std::string result = gcmV35BatchRun(
                mRom, mEmulator, "status", "", mContentType);
            const std::string id = gcmV35BatchField(result, "ID");
            const std::string status = gcmV35BatchField(result, "STATUS");
            if (id == mBatchId &&
                (status == "PASS" || status == "PARTIAL" || status == "ERROR"))
            {
                mWatching = false;
                mPending = true;
                mResult = result;
                mStatus = status;
            }
            else if (gcmV61CommandFailed(result) ||
                     (!id.empty() && id != mBatchId) ||
                     status == "NOT_STARTED")
            {
                if (++mPollErrors >= 3)
                {
                    mWatching = false;
                    mPending = true;
                    mStatus = "ERROR";
                    mResult = result + "\nBATCH\tFAILURE\tVerification job\t"
                                       "Lost or unreadable verification job\n";
                }
            }
            else
            {
                mPollErrors = 0;
                std::string phase = gcmV35BatchField(result, "PHASE");
                const std::string percent = gcmV35BatchField(result, "PERCENT");
                const std::string total = gcmV35BatchField(result, "TOTAL");
                const std::string complete = gcmV35BatchField(result, "COMPLETE");
                if (mContentType == "dlc-apply") phase = _("APPLYING CHANGES");
                if (phase.empty()) phase = _("WORKING");
                if (!percent.empty() && percent != "-1") phase += " " + percent + "%";
                if (!total.empty() && total != "0") phase += " (" + complete + "/" + total + ")";
                setText((gcmV61IsNativeFolder(mEmulator) || gcmV63IsExtraProvider(mEmulator)) && mContentType.find(":scan") != std::string::npos ? _("CANCEL SCAN") : phase);
            }
        }
        if (mPending && mOnCompletion && mOnCompletion(mStatus, mResult))
        {
            mPending = false;
            setText((gcmV61IsNativeFolder(mEmulator) || gcmV63IsExtraProvider(mEmulator)) ? gcmV61NativeCaption(mContentType, mWatching) : mContentType.rfind("dlc-install:", 0) == 0 ? _("INSTALL DLC") : mContentType.rfind("local:", 0) == 0 ? _("INSTALL LOCALLY") : mContentType == "dlc-uninstall" ? _("UNINSTALL SELECTED ITEMS") : mContentType == "dlc-apply" ? _("APPLY CHANGES")
                : mContentType == "dlc" ? _("VERIFY SELECTED DLC") : _("VERIFY SELECTED UPDATES"));
        }
    }
private:
    std::string mRom, mEmulator, mBatchId, mStatus, mResult;
    int mElapsed;
    int mPollErrors = 0;
    bool mWatching = false;
    bool mPending = false;
    std::function<bool(const std::string&, const std::string&)> mOnCompletion;
};

static std::string gcmV34TitleId(const std::string& rom)
{
    std::size_t start = 0;

    while ((start = rom.find('[', start)) != std::string::npos)
    {
        const std::size_t end = rom.find(']', start);

        if (end == std::string::npos)
            break;

        const std::string id =
            rom.substr(start + 1, end - start - 1);

        if (id.size() == 16 &&
            id.find_first_not_of(
                "0123456789abcdefABCDEF"
            ) == std::string::npos)
        {
            return id;
        }

        start = end + 1;
    }

    return "";
}

static GcmV3Summary gcmV3ReadSummary(
    const std::string& rom,
    const std::string& emulator)
{
    GcmV3Summary summary;

    const std::string output =
        gcmV3Run(rom, emulator, "--mode summary");

    std::istringstream stream(output);
    std::string line;

    while (std::getline(stream, line))
    {
        if (line.rfind("SUMMARY\t", 0) != 0)
            continue;

        const size_t split = line.find('\t', 8);

        if (split == std::string::npos)
            continue;

        summary.update = line.substr(8, split - 8);
        summary.dlc = line.substr(split + 1);
        break;
    }

    return summary;
}


// Development-only PS3/PS4 backend selection; no production fallback.
static std::string gcmV36ScriptPath(const std::string& name)
{
    const char* root = std::getenv("GCM_V36_RUNTIME_DIR");
    if (!root || std::string(root).rfind("/", 0) != 0)
        return "";
    if (name != "gcm36_native_summary.py" && name != "gcm36_native_rows.py"
        && name != "citron_gcm_bridge.py"
        && name != "citron_game_gcm_bridge.py"
        && name != "citron_game_content_bridge.py")
        return "";
    return std::string(root) + "/" + name;
}

// GCM v3.6: read-only PS3 snapshot and PS4 live discovery summary.
// No package installation or hashing is performed here.
static GcmV3Summary gcmV36ReadSummary(
    const std::string& rom,
    const std::string& emulator)
{
    if (emulator == "shadps4")
        return gcmV3ReadSummary(rom, emulator);


    GcmV3Summary summary;
    summary.update = "UNAVAILABLE";
    summary.dlc = "UNAVAILABLE";

    const std::string script = gcmV36ScriptPath("gcm36_native_summary.py");
    if (script.empty())
        return summary;

    const std::string command =
        "/usr/bin/timeout -k 2s 10s /usr/bin/python3 " + gcmV3Quote(script)
        + " --rom " + gcmV3Quote(rom)
        + " --emulator " + gcmV3Quote(emulator);

    const std::string output = gcmV35RunTool(command);

    std::istringstream stream(output);
    std::string line;

    while (std::getline(stream, line))
    {
        if (line.rfind("SUMMARY\t", 0) != 0)
            continue;

        const size_t split = line.find('\t', 8);
        if (split == std::string::npos)
            continue;

        summary.update = line.substr(8, split - 8);
        summary.dlc = line.substr(split + 1);
        break;
    }

    return summary;
}

static std::shared_ptr<TextComponent> gcmV3Value(
    Window* window,
    const std::string& value)
{
    auto theme = ThemeData::getMenuTheme();

    return std::make_shared<TextComponent>(
        window,
        value,
        theme->Text.font,
        theme->Text.color
    );
}



// GCM v3.1: state can change only after confirmed backend success.
// Controller left/right must never toggle this component directly.
class GcmV31ConfirmSwitch : public SwitchComponent
{
public:
    explicit GcmV31ConfirmSwitch(Window* window)
        : SwitchComponent(window)
    {
    }

    bool input(InputConfig*, Input) override
    {
        return false;
    }
};



static void gcmV34AddRightMaster(
    Window* window,
    GuiSettings* page,
    const std::shared_ptr<GcmV31ConfirmSwitch>& master,
    const std::function<void()>& callback)
{
    ComponentListRow row;

    row.addElement(gcmV3Value(window, ""), true);
    row.addElement(gcmV3Value(window, _("SELECT ALL")), false);
    row.addElement(gcmV3Value(window, "  "), false);
    row.addElement(master, false);

    row.makeAcceptInputHandler(callback);
    page->addRow(row);
}

static void gcmV34AddRightAction(
    Window* window,
    GuiSettings* page,
    const std::string& label,
    const std::function<void()>& callback)
{
    ComponentListRow row;

    row.addElement(gcmV3Value(window, ""), true);
    row.addElement(gcmV3Value(window, label), false);

    row.makeAcceptInputHandler(callback);
    page->addRow(row);
}


// GCM v3.4: launch or inspect background full-storage discovery.
// No installation operation is performed here.
static void gcmV34OpenScanAction(
    Window* window,
    const std::string& rom,
    const std::string& emulator,
    const std::string& mode)
{
    // A full-device scan indexes all Switch storage locations.
    // It does not require the selected ROM's Title ID.
    const std::string arguments =
        std::string("--mode ") + mode + " --type update";

    std::string response = gcmV34Run(
        rom, emulator, arguments
    );

    if (response.rfind("OK\t", 0) == 0)
        response.erase(0, 3);
    else if (response.rfind("ERROR\t", 0) == 0)
    {
        response.erase(0, 6);
        response = std::string(_("SCAN ERROR")) + "\n\n" + response;
    }

    for (char& c : response)
        if (c == '\t' || c == '\r')
            c = ' ';

    if (response.empty())
        response = _("NO SCAN RESPONSE");

    if (response.size() > 1400)
    {
        response.resize(1400);
        response += "\n...";
    }

    window->pushGui(new GuiMsgBox(
        window, response, _("OK")
    ));
}


// GCM v3.5: inspect the isolated scan controller's atomic state.
static std::string gcmV35ScanState()
{
    std::ifstream input(
        "/userdata/system/gcm-native-test/v34-development/"
        "v35-native-development/v35-scan-state.json"
    );

    if (!input)
        return "idle";

    std::string line;
    std::string state;
    std::string bootId;

    while (std::getline(input, line))
    {
        const bool isState =
            line.find("\"state\"") != std::string::npos;

        const bool isBoot =
            line.find("\"boot_id\"") != std::string::npos;

        if (!isState && !isBoot)
            continue;

        const auto colon = line.find(':');
        const auto first = line.find('"', colon + 1);

        if (colon == std::string::npos ||
            first == std::string::npos)
            continue;

        const auto last = line.find('"', first + 1);

        if (last == std::string::npos)
            continue;

        const std::string value =
            line.substr(first + 1, last - first - 1);

        if (isState)
            state = value;
        else
            bootId = value;
    }

    // Do not treat persisted state from a previous boot as active.
    std::ifstream bootFile(
        "/proc/sys/kernel/random/boot_id"
    );

    std::string currentBoot;
    std::getline(bootFile, currentBoot);

    if (bootId.empty() ||
        currentBoot.empty() ||
        bootId != currentBoot)
        return "idle";

    return state.empty() ? "idle" : state;
}

static bool gcmV35ScanActive()
{
    const std::string state = gcmV35ScanState();

    return state == "starting" ||
           state == "running" ||
           state == "cancelling";
}

// GCM v3.5: read only the current scan's unique job ID.
static std::string gcmV35ScanJobId()
{
    std::ifstream input(
        "/userdata/system/gcm-native-test/v34-development/"
        "v35-native-development/v35-scan-state.json"
    );

    std::string line;
    while (std::getline(input, line))
    {
        const size_t key = line.find("\"job_id\"");
        if (key == std::string::npos)
            continue;

        const size_t colon = line.find(':', key);
        const size_t first = line.find('"', colon + 1);
        if (colon == std::string::npos ||
            first == std::string::npos)
            continue;

        const size_t last = line.find('"', first + 1);
        if (last != std::string::npos)
            return line.substr(first + 1, last - first - 1);
    }
    return "";
}

// This component refreshes its own displayed label.
// It performs only a small local file read, never a disk scan.
class GcmV35ScanActionLabel : public TextComponent
{
public:
    explicit GcmV35ScanActionLabel(Window* window)
        : TextComponent(
              window,
              "",
              ThemeData::getMenuTheme()->Text.font,
              ThemeData::getMenuTheme()->Text.color
          ),
          mElapsed(0)
    {
        refreshLabel();
        mLastJob = gcmV35ScanJobId();
        mWasActive = gcmV35ScanActive();
    }

    bool refreshLabel()
    {
        const bool active = gcmV35ScanActive();

        const std::string desired =
            active
                ? _("CANCEL SCAN")
                : _("SCAN SYSTEM");

        if (desired != mCurrent)
        {
            mCurrent = desired;
            setText(desired);
        }

        return active;
    }

    bool showsCancelScan() const
    {
        return mCurrent == _("CANCEL SCAN");
    }

    void setCompletionCallback(
        const std::function<bool()>& callback)
    {
        mOnComplete = callback;
    }

    void update(int deltaTime) override
    {
        TextComponent::update(deltaTime);

        mElapsed += deltaTime;

        if (mElapsed >= 1000)
        {
            mElapsed = 0;
            refreshLabel();

            const std::string job = gcmV35ScanJobId();
            const std::string state = gcmV35ScanState();

            const bool active =
                state == "starting" ||
                state == "running" ||
                state == "cancelling";

            const bool finished =
                state == "complete" ||
                state == "partial" ||
                state == "cancelled" ||
                state == "failed" ||
                state == "interrupted";

            if (finished &&
                (mWasActive ||
                 (!job.empty() && job != mLastJob)))
                mRefreshPending = true;

            mLastJob = job;
            mWasActive = active;

            // Keep the refresh pending while a dialog is open.
            if (mRefreshPending && mOnComplete &&
                mOnComplete())
                mRefreshPending = false;
        }
    }

private:
    std::string mLastJob;
    bool mWasActive = false;
    bool mRefreshPending = false;
    std::function<bool()> mOnComplete;
    int mElapsed;
    std::string mCurrent;
};

static std::string gcmV35ScanCommand(
    const std::string& action)
{
    if (action != "status" &&
        action != "cancel" &&
        action != "start fast" &&
        action != "start deep" &&
        action != "start both")
    {
        return "ERROR\tINVALID SCAN ACTION";
    }

    const std::string command =
        "python3 " +
        gcmV3Quote(
            "/userdata/system/gcm-native-test/v34-development/"
            "v35-native-development/gcm-v35-full-scan.py"
        ) +
        " " + action + " 2>&1";

    FILE* pipe = popen(command.c_str(), "r");

    if (!pipe)
        return "ERROR\tCANNOT CONTACT SCANNER";

    std::string output;
    char buffer[2048];

    while (fgets(buffer, sizeof(buffer), pipe))
    {
        if (output.size() >= 8192)
            break;

        output += buffer;
    }

    const int code = pclose(pipe);

    if (code != 0 &&
        !gcmV61CommandFailed(output))
        output += "\nERROR\tSCAN COMMAND FAILED";

    return output;
}

static void gcmV35ShowScanResponse(
    Window* window,
    const std::string& action)
{
    std::string response = gcmV35ScanCommand(action);

    std::string heading = _("SCAN STATUS");

    if (gcmV61CommandFailed(response))
        heading = _("SCAN ERROR");
    else if (action == "cancel")
        heading = _("CANCELLATION REQUESTED");
    else if (action.rfind("start ", 0) == 0)
        heading = _("SCAN STARTED");

    if (response.rfind("OK\t", 0) == 0)
        response.erase(0, 3);

    for (char& c : response)
    {
        if (c == '\t' || c == '\r')
            c = ' ';
    }

    if (response.empty())
        response = _("NO SCAN RESPONSE");

    if (response.size() > 2600)
    {
        response.resize(2600);
        response += "\n...";
    }

    window->pushGui(new GuiMsgBox(
        window, gcmV61DialogText(heading + "\n\n" + response), _("OK")
    ));
}

// GCM v3.5: scan selector and dynamic cancellation control.
static void gcmV34AddScanActions(
    Window* window,
    GuiSettings* page,
    const std::string& rom,
    const std::string& emulator,
    const std::function<bool()>& onComplete = nullptr)
{
    auto label =
        std::make_shared<GcmV35ScanActionLabel>(window);

    label->setCompletionCallback(onComplete);

    ComponentListRow scanRow;

    scanRow.addElement(
        gcmV3Value(window, ""), true
    );

    scanRow.addElement(label, false);

    scanRow.makeAcceptInputHandler(
        [window, label]()
        {
            const bool wasCancelLabel =
                label->showsCancelScan();

            const bool currentlyActive =
                label->refreshLabel();

            // If the display and current state disagree,
            // show status instead of changing the action.
            if (wasCancelLabel != currentlyActive)
            {
                gcmV35ShowScanResponse(window, "status");
                return;
            }

            if (currentlyActive)
            {
                window->pushGui(new GuiMsgBox(
                    window,
                    _("CANCEL CURRENT SCAN?\n\n"
                      "Discovered files will be preserved. "
                      "The active filesystem search will stop "
                      "when cancellation completes."),
                    _("YES, CANCEL"),
                    [window, label]()
                    {
                        const std::string currentState =
                            gcmV35ScanState();

                        if (currentState == "starting" ||
                            currentState == "running")
                        {
                            gcmV35ShowScanResponse(
                                window, "cancel"
                            );
                        }
                        else
                        {
                            gcmV35ShowScanResponse(
                                window, "status"
                            );
                        }
                        label->refreshLabel();
                    },
                    _("NO, KEEP SCANNING"),
                    nullptr
                ));

                return;
            }

            window->pushGui(new GuiMsgBox(
                window,
                _("SELECT SCAN TYPE\n\n"
                  "Choose how GCM should search all "
                  "mounted storage devices."),
                _("FAST SCAN"),
                [window, label]()
                {
                    gcmV35ShowScanResponse(
                        window,
                        gcmV35ScanActive()
                            ? "status"
                            : "start fast"
                    );
                    label->refreshLabel();
                },
                _("DEEP SCAN"),
                [window, label]()
                {
                    gcmV35ShowScanResponse(
                        window,
                        gcmV35ScanActive()
                            ? "status"
                            : "start deep"
                    );
                    label->refreshLabel();
                },
                _("FAST + DEEP"),
                [window, label]()
                {
                    gcmV35ShowScanResponse(
                        window,
                        gcmV35ScanActive()
                            ? "status"
                            : "start both"
                    );
                    label->refreshLabel();
                }
            ));
        }
    );

    page->addRow(scanRow);

    gcmV34AddRightAction(
        window, page, _("SCAN STATUS"),
        [window, label]()
        {
            gcmV35ShowScanResponse(
                window, "status"
            );
            label->refreshLabel();
        }
    );
}

struct GcmV32DlcControl
{
    std::shared_ptr<GcmV31ConfirmSwitch> toggle;
    std::shared_ptr<bool> enabled;
};

static void gcmV32UpdateSelectedCount(
    const std::shared_ptr<std::vector<
        std::shared_ptr<GcmV31ConfirmSwitch>>>& controls,
    const std::shared_ptr<TextComponent>& label, bool minimized = false)
{
    unsigned int count = 0;
    for (const auto& control : *controls)
        if (control->getState())
            ++count;

    label->setText(minimized ? gcmV67Summary(_("TOTAL CANDIDATES: "), count, controls->size(), true) : std::to_string(count) + " SELECTED");
}

static bool gcmV31Execute(
    Window* window,
    const std::string& rom,
    const std::string& emulator,
    const std::string& arguments)
{
    std::string response =
        gcmV3Run(rom, emulator, arguments);

    if (response.rfind("OK\t", 0) == 0)
        return true;

    if (response.rfind("ERROR\t", 0) == 0)
        response.erase(0, 6);

    for (char& c : response)
        if (c == '\n' || c == '\r' || c == '\t')
            c = ' ';

    if (response.empty())
        response = "CONTENT OPERATION FAILED";

    if (response.size() > 240)
        response.resize(240);

    window->pushGui(
        new GuiMsgBox(window, response, _("OK"))
    );

    return false;
}


struct GcmV33Choice
{
    std::string token;
    std::shared_ptr<GcmV31ConfirmSwitch> check;
};

// Shared DLC operations never run a provider on the rendering thread.
class GcmV61ProgressBar : public GuiComponent
{
public:
    explicit GcmV61ProgressBar(Window* window) : GuiComponent(window)
    {
        setSize(Renderer::getScreenWidth() * 0.24f,
                ThemeData::getMenuTheme()->Text.font->getHeight());
    }
    void setPercent(int value) { mPercent = value; }
    void update(int delta) override
    {
        GuiComponent::update(delta);
        mClock = (mClock + delta) % 1600;
    }
    void render(const Transform4x4f& parent) override
    {
        if (!mVisible) return;
        const auto trans = parent * getTransform();
        Renderer::setMatrix(trans);
        const float height = mSize.y() * 0.35f;
        const float y = (mSize.y() - height) * 0.5f;
        const unsigned int color = ThemeData::getMenuTheme()->Text.color;
        Renderer::drawRect(0, y, mSize.x(), height,
                           (color & 0xFFFFFF00) | 0x35);
        if (mPercent >= 0)
            Renderer::drawRect(0, y, mSize.x() * mPercent / 100.0f,
                               height, color);
        else
        {
            const float t = mClock / 800.0f;
            const float position = t <= 1.0f ? t : 2.0f - t;
            Renderer::drawRect(position * mSize.x() * 0.75f, y,
                               mSize.x() * 0.25f, height, color);
        }
    }
private:
    int mPercent = -1;
    int mClock = 0;
};

struct GcmV61ActionSnapshot
{
    std::string status, id, label, phase, error, recovery;
    int total = 0, complete = 0, percent = -1;
};

static GcmV61ActionSnapshot gcmV61ActionReply(const std::string& reply)
{
    std::map<std::string, std::string> fields;
    std::istringstream lines(reply);
    std::string line;
    while (std::getline(lines, line))
    {
        if (line.rfind("ERROR\t", 0) == 0)
            throw std::runtime_error(line.substr(6));
        if (line.rfind("ACTION\t", 0) != 0) continue;
        const auto split = line.find('\t', 7);
        if (split == std::string::npos ||
            !fields.emplace(line.substr(7, split - 7),
                            line.substr(split + 1)).second)
            throw std::runtime_error("Invalid action progress reply");
    }
    auto number = [&fields](const std::string& name, int low, int high)
    {
        const auto value = fields.at(name);
        std::size_t used = 0;
        const int parsed = std::stoi(value, &used);
        if (used != value.size() || parsed < low || parsed > high)
            throw std::runtime_error("Invalid action progress number");
        return parsed;
    };
    GcmV61ActionSnapshot result;
    result.status = fields.at("STATUS");
    result.id = fields.at("ID");
    if ((result.status != "RUNNING" && result.status != "PASS"
         && result.status != "ERROR") || result.id.size() != 32
        || result.id.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw std::runtime_error("Invalid action identity or status");
    result.label = fields.at("LABEL");
    result.phase = fields.at("PHASE");
    result.error = fields.at("ERROR");
    result.recovery = fields.at("RECOVERY_REQUIRED");
    if (result.recovery != "YES" && result.recovery != "NO")
        throw std::runtime_error("Invalid recovery status");
    result.total = number("TOTAL", 0, 32);
    result.complete = number("COMPLETE", 0, result.total);
    result.percent = number("PERCENT", -1, 100);
    if (result.status == "PASS" && result.complete != result.total)
        throw std::runtime_error("Incomplete action reported success");
    return result;
}

class GcmV61ActionProgress : public GuiSettings
{
    struct Shared
    {
        std::mutex mutex;
        std::atomic<bool> stopped{false};
        GcmV61ActionSnapshot snapshot;
        unsigned long revision = 0;
    };
public:
    GcmV61ActionProgress(Window* window, const std::string& rom,
        const std::string& emulator, const std::string& operation,
        const std::string& tokens, const std::function<void()>& finished)
        : GuiSettings(window, operation == "install"
            ? _("INSTALLING DLC") : _("APPLYING DLC CHANGES")),
          mShared(std::make_shared<Shared>()), mFinished(finished)
    {
        setUpdateType(ComponentListFlags::UpdateType::UPDATE_ALWAYS);
        mItem = gcmV3Value(window, _("STARTING"));
        mPhase = gcmV3Value(window, _("QUEUED"));
        mPercent = gcmV3Value(window, _("WORKING"));
        mCount = gcmV3Value(window, "0 / 0");
        mBar = std::make_shared<GcmV61ProgressBar>(window);
        mOverall = std::make_shared<GcmV61ProgressBar>(window);
        const float valueWidth = Renderer::getScreenWidth() * 0.24f;
        const float lineHeight = ThemeData::getMenuTheme()->Text.font->getHeight();
        for (const auto& value : {mItem, mPhase, mPercent, mCount})
        {
            value->setMultiLine(TextComponent::SINGLELINE);
            value->setSize(valueWidth, lineHeight);
            value->setAutoScroll(TextComponent::HORIZONTAL);
        }
        addWithLabel(_("ITEM"), mItem);
        addWithLabel(_("PHASE"), mPhase);
        addWithLabel(_("PROGRESS"), mPercent);
        addWithLabel("", mBar);
        addWithLabel(_("COMPLETED"), mCount);
        addWithLabel("", mOverall);
        addEntry(_("DONE"), false, [this]() { finish(); });
        const auto shared = mShared;
        std::thread([shared, rom, emulator, operation, tokens]()
        {
            GcmV61ActionSnapshot last;
            std::string job;
            const std::string tool =
                "/usr/bin/timeout -k 2s 20s /usr/bin/python3 -B " +
                gcmV3Quote("/userdata/system/gcm-native-test/"
                    "step61-common-content/gcm-global-actions.py");
            try
            {
                std::string command = tool + " start --rom " + gcmV3Quote(rom)
                    + " --emulator " + gcmV3Quote(emulator)
                    + " --operation " + gcmV3Quote(operation)
                    + " --tokens " + gcmV3Quote(tokens) + " --confirm";
                while (!shared->stopped.load())
                {
                    last = gcmV61ActionReply(gcmV35RunTool(command));
                    if (!job.empty() && last.id != job)
                        throw std::runtime_error("Action job identity changed");
                    job = last.id;
                    {
                        std::lock_guard<std::mutex> lock(shared->mutex);
                        shared->snapshot = last;
                        ++shared->revision;
                    }
                    if (last.status != "RUNNING") return;
                    for (int tick = 0; tick < 10 && !shared->stopped.load(); ++tick)
                        std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    command = tool + " status --job-id " + gcmV3Quote(job);
                }
            }
            catch (const std::exception& error)
            {
                last.status = "ERROR";
                last.phase = "PROGRESS UNAVAILABLE";
                last.error = std::string(error.what()).substr(0, 400);
                // A failed reply cannot establish whether the provider stopped.
                last.recovery = "YES";
                std::lock_guard<std::mutex> lock(shared->mutex);
                shared->snapshot = last;
                ++shared->revision;
            }
        }).detach();
    }
    ~GcmV61ActionProgress() override { mShared->stopped.store(true); }
    bool input(InputConfig* config, Input input) override
    {
        if (mDone && input.value != 0 && config->isMappedTo(BUTTON_BACK, input))
        {
            finish();
            return true;
        }
        if (!mDone) return true;
        return GuiSettings::input(config, input);
    }
    void update(int delta) override
    {
        GuiSettings::update(delta);
        GcmV61ActionSnapshot snapshot;
        {
            std::lock_guard<std::mutex> lock(mShared->mutex);
            if (mRevision == mShared->revision) return;
            mRevision = mShared->revision;
            snapshot = mShared->snapshot;
        }
        mDone = snapshot.status != "RUNNING";
        mItem->setText(snapshot.label.empty() ? _("DLC SELECTION") : snapshot.label);
        mPhase->setText(snapshot.phase);
        mPercent->setText(snapshot.percent < 0 ? _("WORKING")
            : std::to_string(snapshot.percent) + "%");
        mBar->setPercent(snapshot.percent);
        mCount->setText(std::to_string(snapshot.complete) + " / "
                       + std::to_string(snapshot.total));
        mOverall->setPercent(snapshot.total > 0
            ? snapshot.complete * 100 / snapshot.total : -1);
        if (snapshot.status == "PASS")
        {
            setTitle(_("DLC CHANGES COMPLETE"));
            mPercent->setText(_("COMPLETE"));
            mBar->setPercent(100);
            mOverall->setPercent(100);
        }
        else if (snapshot.status == "ERROR")
        {
            setTitle(_("DLC ACTION FAILED"));
            mPercent->setText(_("STOPPED"));
            mBar->setPercent(0);
            addGroup(_("DETAILS"));
            addWithDescription(_("ERROR"), snapshot.error,
                               gcmV3Value(mWindow, ""));
            if (snapshot.recovery == "YES")
                addEntry(_("RECOVERY ASSESSMENT REQUIRED"));
        }
    }
private:
    void finish()
    {
        if (!mDone) return;
        auto callback = mFinished;
        delete this;
        if (callback) callback();
    }
    std::shared_ptr<Shared> mShared;
    std::function<void()> mFinished;
    std::shared_ptr<TextComponent> mItem, mPhase, mPercent, mCount;
    std::shared_ptr<GcmV61ProgressBar> mBar, mOverall;
    unsigned long mRevision = 0;
    bool mDone = false;
};


// Native providers use the same bounded action labels and shared page layout.
// Never render the tab-separated job transport directly in a scan dialog.
static std::string gcmV61ScanDialogText(const std::string& response)
{
    std::string error = gcmV35BatchField(response, "ERROR");
    if (response.rfind("ERROR\t", 0) == 0) error = response.substr(6);
    if (error.empty()) return _("CANCELLATION REQUESTED.\n\nThe scan will stop shortly. Existing files are preserved.");
    std::string readable;
    unsigned column = 0;
    for (char c : error.substr(0, 480))
    {
        if (c == '\t' || c == '\r') c = ' ';
        if (c == '\n') column = 0;
        else if (++column > 64) { readable += '\n'; column = 1; }
        readable += c;
    }
    return readable;
}

static std::string gcmV63ImportUpdateRun(const std::string& rom, const std::string& emulator,
    const std::string& action, const std::string& token = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-global-update-v01/gcm-global-update-workflow-v01.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --token " + gcmV3Quote(token) + (action == "import" || action == "install" || action == "recover" ? " --confirm" : ""));
}

static std::string gcmV64ActivationRun(const std::string& rom, const std::string& emulator,
    const std::string& action, const std::string& token = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-update-deactivation-v01/gcm-update-deactivation-v01.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --token " + gcmV3Quote(token) + (action == "list" ? "" : " --confirm"));
}

static std::string gcmV62UpdateRemovalRun(const std::string& rom, const std::string& emulator,
    const std::string& action, const std::string& tokens = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(gcmV36IsCitron(emulator)
        ? "/userdata/system/gcm-native-test/step61-common-content/beta-citron-update-uninstall-v01/gcm-citron-update-uninstall-v01.py"
        : "/userdata/system/gcm-native-test/step61-common-content/beta-global-update-v01/gcm-global-update-remove-v01.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --tokens " + gcmV3Quote(tokens) + (action == "uninstall" || action == "restore" ? " --confirm" : ""));
}

static std::string gcmV65DeleteRun(const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& action, const std::string& tokens = "", const std::string& inventory = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        (kind == "dlc" && gcmV36IsSharedSwitch(emulator) ? "/userdata/system/gcm-native-test/step61-common-content/beta-dlc-stage-separation-v04/delete-bridge.py" : "/userdata/system/gcm-native-test/step61-common-content/beta-managed-delete-v01/gcm-managed-delete-v01.py"))
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --type " + gcmV3Quote(kind) + " --tokens " + gcmV3Quote(tokens)
        + " --inventory-base64 " + gcmV3Quote(inventory) + (action == "delete" ? " --confirm" : ""));
}

// 61TE: after an uninstall, permanently delete what it set aside (originals are kept) and remove its record.
static std::string gcmV75Cleanup()
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-uninstall-cleanup-v01/gcm-uninstall-cleanup-v01.py")
        + " apply --confirm");
}

static std::string gcmV75CleanupNote(const std::string& output)
{
    if (output.find("PASS: uninstall cleanup") != std::string::npos) return "";
    std::string line = output.substr(0, output.find('\n'));
    if (line.rfind("STOP: ", 0) == 0) line = line.substr(6);
    return std::string("\n\n") + _("GCM COPIES WERE NOT REMOVED YET: ") + line;
}

// 61TE3: around an uninstall, note the Available entries first; afterwards forget the entries it
// freed so the item leaves Available and a scan finds it again as Detected.
static std::string gcmV76Forget(const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& action)
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-uninstall-forget-v01/gcm-uninstall-forget-v01.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator) + " --type " + gcmV3Quote(kind));
}

// 61TE4: Citron loads every DLC file in its folders; this tool deletes GCM's own copies only.
static std::string gcmV77CitronDlcRun(const std::string& rom, const std::string& tokens)
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-citron-dlc-uninstall-v01/gcm-citron-dlc-uninstall-v01.py")
        + " uninstall --rom " + gcmV3Quote(rom) + " --tokens " + gcmV3Quote(tokens) + " --confirm");
}

static std::string gcmV77CitronDlcMessage(const std::string& reply)
{
    if (reply.rfind("OK\tUNINSTALLED=", 0) != 0) return gcmV35FailureMessage(reply);
    std::istringstream lines(reply); std::string line, kept; unsigned removed = 0, keptCount = 0;
    while (std::getline(lines, line))
    {
        if (line.rfind("REMOVED\t", 0) == 0) ++removed;
        else if (line.rfind("KEPT\t", 0) == 0)
        {
            const auto a = line.find('\t', 5);
            if (++keptCount <= 5)
                kept += "\n" + line.substr(5, a == std::string::npos ? std::string::npos : a - 5)
                    + (a == std::string::npos ? std::string() : ": " + line.substr(a + 1));
        }
    }
    std::string message = std::to_string(removed) + " " + _("DLC UNINSTALLED AND REMOVED FROM GCM.");
    if (keptCount)
        message += "\n\n" + std::to_string(keptCount) + " " + _("KEPT:") + kept + (keptCount > 5 ? "\n..." : "");
    return message;
}

static std::string gcmV65DeleteOverlay(const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& output)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string encoded;unsigned value = 0;int bits = -6;
    for (unsigned char c : output) { value = (value << 8) | c;bits += 8;while (bits >= 0) { encoded += alphabet[(value >> bits) & 63];bits -= 6; } }
    if (bits > -6) encoded += alphabet[((value << 8) >> (bits + 8)) & 63];
    while (encoded.size() % 4) encoded += '=';
    return gcmV35RunTool("python3 -B " + gcmV3Quote(
        "/userdata/system/gcm-native-test/step61-common-content/beta-dlc-stage-separation-v04/gcm-selected-verification-v01.py")
        + " overlay --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --type " + gcmV3Quote(kind) + " --inventory-base64 " + gcmV3Quote(encoded));
}

static void gcmV69OpenDetectedBatch(Window* window, const std::string& rom,
    const std::string& emulator, const std::string& kind, const std::string& tokens,
    const std::function<bool(const std::string&)>& refreshed)
{
    window->pushGui(new GuiMsgBox(window, _("VERIFY ALL SELECTED DETECTED CONTENT?"), _("VERIFY"),
        [window, rom, emulator, kind, tokens, refreshed]()
        {
            window->pushGui(new GuiLoading<std::string>(window, _("VERIFYING SELECTED CONTENT..."),
                [rom, emulator, kind, tokens](IGuiLoadingHandler*)
                { return gcmV68VerifyRun(rom, emulator, kind, "verify-detected", tokens); },
                [window, rom, emulator, kind, tokens, refreshed](std::string reply)
                {
                    const auto message = gcmV61CommandFailed(reply) ? gcmV35FailureMessage(reply) : "";
                    if (!refreshed(message) && !message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                }));
        }, _("CANCEL"), nullptr));
}

static void gcmV68OpenVerify(Window* window, const std::string& rom,
    const std::string& emulator, const std::string& kind, const std::function<void()>& refreshed)
{
    window->pushGui(new GuiLoading<std::string>(window, _("READING UNVERIFIED CONTENT..."),
        [rom, emulator, kind](IGuiLoadingHandler*) { return gcmV68VerifyRun(rom, emulator, kind, "list"); },
        [window, rom, emulator, kind, refreshed](std::string output)
        {
            if (gcmV61CommandFailed(output))
            {
                window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(output), _("OK")));
                return;
            }
            auto picker = new GuiSettings(window, kind == "update" ? _("VERIFY UNVERIFIED UPDATES") : _("VERIFY UNVERIFIED DLC"));
            auto controls = std::make_shared<std::vector<std::shared_ptr<GcmV31ConfirmSwitch>>>();
            auto tokens = std::make_shared<std::vector<std::string>>();
            auto master = std::make_shared<GcmV31ConfirmSwitch>(window);
            master->setState(false);
            std::istringstream lines(output); std::string line;
            while (std::getline(lines, line))
            {
                if (line.rfind("ROW\tAVAILABLE\t", 0) != 0) continue;
                const auto a = line.find('\t', 14), b = a == std::string::npos ? a : line.find('\t', a + 1);
                if (a == std::string::npos || b == std::string::npos) continue;
                const auto state = line.substr(14, a - 14), label = line.substr(a + 1, b - a - 1), token = line.substr(b + 1);
                if (state == "ACTIVE" || state == "ENABLED") continue;
                auto toggle = std::make_shared<GcmV31ConfirmSwitch>(window);toggle->setState(false);
                controls->push_back(toggle);tokens->push_back(token);
                picker->addWithLabel(label, toggle, false, [controls, master, toggle]()
                {
                    toggle->setState(!toggle->getState());bool all = !controls->empty();
                    for (const auto& item : *controls) if (!item->getState()) all = false;
                    master->setState(all);
                });
            }
            if (controls->empty()) picker->addEntry(_("NO UNVERIFIED CONTENT WITH A VERIFIABLE SOURCE"));
            // The master toggle is below all update rows, as requested.
            gcmV34AddRightMaster(window, picker, master, [controls, master]()
            {
                const bool desired = !master->getState();
                for (const auto& toggle : *controls) toggle->setState(desired);
                master->setState(desired && !controls->empty());
            });
            gcmV34AddRightAction(window, picker, _("VERIFY"), [window, picker, rom, emulator, kind, controls, tokens, refreshed]()
            {
                std::string selected;unsigned count = 0;
                for (unsigned i = 0; i < controls->size(); ++i) if ((*controls)[i]->getState())
                { if (!selected.empty()) selected += ","; selected += (*tokens)[i]; ++count; }
                if (count == 0 || (count > 512 && !(kind == "dlc" && gcmV36IsSharedSwitch(emulator))))
                { window->pushGui(new GuiMsgBox(window, _("SELECT BETWEEN 1 AND 512 ITEMS"), _("OK"))); return; }
                window->pushGui(new GuiMsgBox(window,
                    _("VERIFY SELECTED CONTENT?") + std::string("\n\n") + std::to_string(count)
                    + ((kind == "dlc" && gcmV36IsSharedSwitch(emulator))
                        ? _(" ITEM(S)\n\nCheck that the selected DLC belongs to this game. Install is a separate action in Available.")
                        : _(" ITEM(S)\n\nSelected content will be identified and copied or prepared using the existing verification providers. External originals, base games and saves will be preserved.")),
                    _("VERIFY"), [window, picker, rom, emulator, kind, selected, refreshed]()
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("VERIFYING SELECTED CONTENT..."),
                            [rom, emulator, kind, selected](IGuiLoadingHandler*) { return gcmV68VerifyRun(rom, emulator, kind, "verify", selected); },
                            [window, picker, refreshed](std::string reply)
                            {
                                if (reply.rfind("OK\t", 0) == 0)
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, gcmV61DialogText(reply), _("OK")));
                                }
                                else
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                                }
                            }));
                    }, _("CANCEL"), nullptr));
            });
            window->pushGui(picker);
        }));
}

static void gcmV65OpenDelete(Window* window, const std::string& rom,
    const std::string& emulator, const std::string& kind, const std::function<void()>& refreshed)
{
    window->pushGui(new GuiLoading<std::string>(window, _("READING AVAILABLE CONTENT..."),
        [rom, emulator, kind](IGuiLoadingHandler*) { return gcmV65DeleteRun(rom, emulator, kind, "list"); },
        [window, rom, emulator, kind, refreshed](std::string output)
        {
            if (gcmV61CommandFailed(output))
            {
                window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(output), _("OK")));
                return;
            }
            auto picker = new GuiSettings(window, kind == "update" ? _("DELETE UPDATES") : _("DELETE DLC"));
            auto controls = std::make_shared<std::vector<std::shared_ptr<GcmV31ConfirmSwitch>>>();
            auto tokens = std::make_shared<std::vector<std::string>>();
            auto master = std::make_shared<GcmV31ConfirmSwitch>(window);
            master->setState(false);
            std::istringstream lines(output); std::string line;
            while (std::getline(lines, line))
            {
                if (line.rfind("ROW\tAVAILABLE\t", 0) != 0) continue;
                const auto a = line.find('\t', 14), b = a == std::string::npos ? a : line.find('\t', a + 1);
                if (a == std::string::npos || b == std::string::npos) continue;
                const auto state = line.substr(14, a - 14), label = line.substr(a + 1, b - a - 1), token = line.substr(b + 1);
                if (state == "ACTIVE" || state == "ENABLED") continue;
                auto toggle = std::make_shared<GcmV31ConfirmSwitch>(window);toggle->setState(false);
                controls->push_back(toggle);tokens->push_back(token);
                picker->addWithLabel(label, toggle, false, [controls, master, toggle]()
                {
                    toggle->setState(!toggle->getState());bool all = !controls->empty();
                    for (const auto& item : *controls) if (!item->getState()) all = false;
                    master->setState(all);
                });
            }
            if (controls->empty()) picker->addEntry(_("NO NON-ACTIVE CONTENT AVAILABLE TO DELETE"));
            // The master toggle is below all update rows, as requested.
            gcmV34AddRightMaster(window, picker, master, [controls, master]()
            {
                const bool desired = !master->getState();
                for (const auto& toggle : *controls) toggle->setState(desired);
                master->setState(desired && !controls->empty());
            });
            gcmV34AddRightAction(window, picker, _("DELETE"), [window, picker, rom, emulator, kind, controls, tokens, refreshed]()
            {
                std::string selected;unsigned count = 0;
                for (unsigned i = 0; i < controls->size(); ++i) if ((*controls)[i]->getState())
                { if (!selected.empty()) selected += ","; selected += (*tokens)[i]; ++count; }
                if (count == 0 || count > 32)
                { window->pushGui(new GuiMsgBox(window, _("SELECT BETWEEN 1 AND 32 ITEMS"), _("OK"))); return; }
                window->pushGui(new GuiMsgBox(window,
                    _("PERMANENTLY DELETE SELECTED CONTENT?") + std::string("\n\n") + std::to_string(count)
                    + _(" ITEM(S)\n\nGCM-owned copies and GUI entries will be permanently deleted. This cannot be undone. External originals, base games and saves will be preserved."),
                    _("DELETE"), [window, picker, rom, emulator, kind, selected, refreshed]()
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("DELETING LOCAL CONTENT..."),
                            [rom, emulator, kind, selected](IGuiLoadingHandler*) { return gcmV65DeleteRun(rom, emulator, kind, "delete", selected); },
                            [window, picker, refreshed](std::string reply)
                            {
                                if (reply.rfind("OK\t", 0) == 0)
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, gcmV61DialogText(reply), _("OK")));
                                }
                                else
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                                }
                            }));
                    }, _("CANCEL"), nullptr));
            });
            window->pushGui(picker);
        }));
}

static void gcmV62OpenUpdateRemoval(Window* window, const std::string& rom,
    const std::string& emulator, const std::function<void()>& refreshed)
{
    window->pushGui(new GuiLoading<std::string>(window, _("READING INSTALLED UPDATES..."),
        [rom, emulator](IGuiLoadingHandler*) { return gcmV62UpdateRemovalRun(rom, emulator, "list"); },
        [window, rom, emulator, refreshed](std::string output)
        {
            if (gcmV61CommandFailed(output))
            {
                window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(output), _("OK")));
                return;
            }
            auto picker = new GuiSettings(window, _("UNINSTALL UPDATE"));
            auto controls = std::make_shared<std::vector<std::shared_ptr<GcmV31ConfirmSwitch>>>();
            auto tokens = std::make_shared<std::vector<std::string>>();
            auto master = std::make_shared<GcmV31ConfirmSwitch>(window);
            master->setState(false);
            std::istringstream lines(output); std::string line;
            while (std::getline(lines, line))
            {
                if (line.rfind("ROW\tAVAILABLE\t", 0) != 0) continue;
                const auto a = line.find('\t', 14), b = a == std::string::npos ? a : line.find('\t', a + 1);
                if (a == std::string::npos || b == std::string::npos) continue;
                const auto state = line.substr(14, a - 14), label = line.substr(a + 1, b - a - 1), token = line.substr(b + 1);
                if (state != "REMOVABLE" && state != "READY")  // 61TE2: shown with the reason, not as Review
                {
                    picker->addWithLabel(label, gcmV3Value(window, state == "KEPT_SHARED" ? _("USED BY RYUJINX")
                        : state == "ORIGINAL" ? _("YOUR FILE") : state == "CITRON_NAND" ? _("IN CITRON") : _("NOT REMOVABLE")), false,
                        [window, rom, emulator, token]()
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("READING UPDATE DETAILS..."),
                            [rom, emulator, token](IGuiLoadingHandler*) { return gcmV62UpdateRemovalRun(rom, emulator, "preview", token); },
                            [window](std::string details) { window->pushGui(new GuiMsgBox(window, gcmV61DialogText(details), _("OK"))); }));
                    });
                    continue;
                }
                auto toggle = std::make_shared<GcmV31ConfirmSwitch>(window);toggle->setState(false);
                controls->push_back(toggle);tokens->push_back(token);
                picker->addWithLabel(label, toggle, false, [controls, master, toggle]()
                {
                    toggle->setState(!toggle->getState());bool all = !controls->empty();
                    for (const auto& item : *controls) if (!item->getState()) all = false;
                    master->setState(all);
                });
            }
            if (controls->empty()) picker->addEntry(_("NO GCM UPDATE COPIES TO UNINSTALL"));
            // The master toggle is below all update rows, as requested.
            gcmV34AddRightMaster(window, picker, master, [controls, master]()
            {
                const bool desired = !master->getState();
                for (const auto& toggle : *controls) toggle->setState(desired);
                master->setState(desired && !controls->empty());
            });
            gcmV34AddRightAction(window, picker, _("UNINSTALL"), [window, picker, rom, emulator, controls, tokens, refreshed]()
            {
                std::string selected;unsigned count = 0;
                for (unsigned i = 0; i < controls->size(); ++i) if ((*controls)[i]->getState())
                { if (!selected.empty()) selected += ","; selected += (*tokens)[i]; ++count; }
                if (count == 0 || count > 32)
                { window->pushGui(new GuiMsgBox(window, _("SELECT BETWEEN 1 AND 32 UPDATES"), _("OK"))); return; }
                window->pushGui(new GuiMsgBox(window,
                    _("UNINSTALL SELECTED UPDATES?") + std::string("\n\n") + std::to_string(count)
                    + _(" UPDATE(S)\n\nGCM copies will be permanently deleted. Original packages, base games, saves and DLC will be preserved."),
                    _("UNINSTALL"), [window, picker, rom, emulator, selected, refreshed]()
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("UNINSTALLING UPDATES..."),
                            [rom, emulator, selected](IGuiLoadingHandler*) { gcmV76Forget(rom, emulator, "update", "before"); std::string reply = gcmV62UpdateRemovalRun(rom, emulator, "uninstall", selected); if (reply.rfind("OK\tUNINSTALLED=", 0) == 0) { gcmV76Forget(rom, emulator, "update", "after"); reply += "\n" + gcmV75Cleanup(); } return reply; },
                            [window, picker, refreshed](std::string reply)
                            {
                                if (reply.rfind("OK\tUNINSTALLED=", 0) == 0)
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, std::string(_("UPDATES UNINSTALLED AND REMOVED FROM GCM. ORIGINALS PRESERVED.")) + gcmV75CleanupNote(reply.substr(reply.find('\n') == std::string::npos ? reply.size() : reply.find('\n') + 1)), _("OK")));
                                }
                                else
                                {
                                    picker->close();if (refreshed) refreshed();
                                    window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                                }
                            }));
                    }, _("CANCEL"), nullptr));
            });
            window->pushGui(picker);
        }));
}

static void gcmV61AddNativeScanActions(Window* window, GuiSettings* page,
    const std::string& rom, const std::string& emulator, const std::string& kind,
    const std::function<bool(const std::string&)>& refreshed)
{
    auto label = std::make_shared<GcmV35BatchActionLabel>(
        window, rom, emulator, "native:scan-both:" + kind + ":");
    label->setCompletionCallback([refreshed](const std::string& status, const std::string& reply)
        { return refreshed(status == "PASS" || gcmV35BatchField(reply, "PHASE") == "CANCELLED" ? "" : gcmV35FailureMessage(reply)); });
    const auto statusType = "native:scan:" + kind + ":";
    const auto prior = gcmV35BatchRun(rom, emulator, "status", "", statusType);
    if (gcmV35BatchField(prior, "STATUS") == "RUNNING")
    {
        label->setContentType("native:" + gcmV35BatchField(prior, "OPERATION") + ":" + kind + ":");
        label->watch(gcmV35BatchField(prior, "ID"));
    }
    page->addWithLabel("", label, false, [window, rom, emulator, kind, label]()
    {
        const auto current = gcmV35BatchRun(rom, emulator, "status", "", "native:scan:" + kind + ":");
        if (gcmV35BatchField(current, "STATUS") == "RUNNING")
        {
            const auto id = gcmV35BatchField(current, "ID");
            window->pushGui(new GuiMsgBox(window, _("CANCEL CURRENT SCAN?"), _("YES, CANCEL"), [window, id, emulator]()
            {
                const auto response = gcmV35RunTool("python3 -B " + gcmV3Quote(gcmV63ScanJobPath(emulator))
                    + " cancel --job-id " + gcmV3Quote(id));
                window->pushGui(new GuiMsgBox(window, gcmV61ScanDialogText(response), _("OK")));
            }, _("NO, KEEP SCANNING"), nullptr));
            return;
        }
        if (label->busy()) return;
        auto modes = new GuiSettings(window, _("SCAN SYSTEM"));
        for (const auto& choice : std::vector<std::pair<std::string, std::string>>{
                {"fast", _("FAST SCAN")}, {"deep", _("DEEP SCAN")}, {"both", _("FAST SCAN + DEEP SCAN")}})
        {
            modes->addEntry(choice.second, false, [window, modes, rom, emulator, kind, label, choice]()
            {
                const std::string type = "native:scan-" + choice.first + ":" + kind + ":";
                const auto reply = gcmV35BatchRun(rom, emulator, "start", "", type);
                if (gcmV35BatchField(reply, "STATUS") != "RUNNING")
                {
                    window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                    return;
                }
                label->setContentType(type);
                label->watch(gcmV35BatchField(reply, "ID"));
                modes->close();
            });
        }
        window->pushGui(modes);
    });
    gcmV34AddRightAction(window, page, _("SCAN STATUS"), [window, rom, emulator, kind, statusType, refreshed]()
    {
        const auto reply = gcmV35BatchRun(rom, emulator, "status", "", statusType);
        const auto id = gcmV35BatchField(reply, "ID");
        const auto status = gcmV35BatchField(reply, "STATUS");
        std::string message = gcmV35BatchField(reply, "PHASE") + "\n"
            + gcmV35BatchField(reply, "ELAPSED_SECONDS") + " " + _("SECONDS");
        const auto error = gcmV35BatchField(reply, "ERROR");
        if (!error.empty()) message += "\n" + error;
        if (status != "RUNNING")
        {
            if (gcmV63IsExtraProvider(emulator) && gcmV35BatchField(reply, "PHASE") == "INTERRUPTED")
            {
                window->pushGui(new GuiMsgBox(window, gcmV61DialogText(message), _("RECOVER SCAN"), [window, id, emulator, refreshed]()
                {
                    const auto report = gcmV35RunTool("python3 -B " + gcmV3Quote(gcmV63ScanJobPath(emulator)) + " recover --job-id " + gcmV3Quote(id) + " --confirm");
                    refreshed("");
                    window->pushGui(new GuiMsgBox(window, gcmV61ScanDialogText(report), _("OK")));
                }, _("CLOSE"), nullptr));
            }
            else window->pushGui(new GuiMsgBox(window, gcmV61DialogText(status + "\n" + message), _("OK")));
            return;
        }
        window->pushGui(new GuiMsgBox(window, gcmV61DialogText(message), _("CANCEL SCAN"), [window, id, emulator]()
        {
            window->pushGui(new GuiMsgBox(window, _("CANCEL CURRENT SCAN?"), _("YES, CANCEL"), [window, id, emulator]()
            {
                const auto response = gcmV35RunTool("python3 -B " + gcmV3Quote(gcmV63ScanJobPath(emulator))
                    + " cancel --job-id " + gcmV3Quote(id));
                window->pushGui(new GuiMsgBox(window, gcmV61ScanDialogText(response), _("OK")));
            }, _("NO, KEEP SCANNING"), nullptr));
        }, _("CLOSE"), nullptr));
    });
}

static void gcmV61AddNativeItemRow(Window* window, GuiSettings* page,
    const std::string& rom, const std::string& emulator, const std::string& kind,
    const GcmV3Row& row, const std::function<bool(const std::string&)>& refreshed)
{
    if (row.token.rfind("folder:", 0) != 0)
    {
        page->addWithLabel(row.label, gcmV3Value(window, _("UNVERIFIED")));
        return;
    }
    std::vector<std::pair<std::string, std::string>> actions;
    if (row.state == "RECOVERY_REQUIRED") actions.push_back({"recover", _("RECOVER")});
    else if (row.state == "ASSESSMENT_REQUIRED")
    {
        page->addWithLabel(row.label, gcmV3Value(window, _("ASSESSMENT REQUIRED")));
        return;
    }
    else
    {
        if (row.state == "FOLDER_PREPARED") actions.push_back({"install", _("INSTALL")});
        else if (row.state == "ACTIVE" || row.state == "ENABLED") actions.push_back({"disable", kind == "update" ? _("DEACTIVATE") : _("DISABLE")});
        else actions.push_back({"enable", _("ACTIVATE")});
        if (row.state != "FOLDER_PREPARED") actions.push_back({"uninstall", _("UNINSTALL")});
    }
    auto label = std::make_shared<GcmV35BatchActionLabel>(window, rom, emulator,
        "native:" + actions.front().first + ":" + kind + ":" + row.token);
    label->setCompletionCallback([refreshed](const std::string& status, const std::string& reply)
        { return refreshed(status == "PASS" ? "" : gcmV35FailureMessage(reply)); });
    for (const auto& action : actions)
    {
        const auto type = "native:" + action.first + ":" + kind + ":" + row.token;
        const auto prior = gcmV35BatchRun(rom, emulator, "status", "", type);
        if (gcmV35BatchField(prior, "STATUS") == "RUNNING")
        {
            label->setContentType(type);
            label->watch(gcmV35BatchField(prior, "ID"));
            break;
        }
    }
    page->addWithLabel(row.label, label, false, [window, rom, emulator, kind, row, actions, label]()
    {
        if (label->busy()) return;
        auto options = new GuiSettings(window, row.label.c_str());
        for (const auto& action : actions)
            options->addEntry(action.second, false, [window, options, rom, emulator, kind, row, action, label]()
            {
                window->pushGui(new GuiMsgBox(window, action.second + "?", _("YES"),
                    [window, options, rom, emulator, kind, row, action, label]()
                    {
                        const std::string type = "native:" + action.first + ":" + kind + ":" + row.token;
                        const auto reply = gcmV35BatchRun(rom, emulator, "start", "", type);
                        if (gcmV35BatchField(reply, "STATUS") != "RUNNING")
                        {
                            window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                            return;
                        }
                        label->setContentType(type);
                        label->watch(gcmV35BatchField(reply, "ID"));
                        options->close();
                    }, _("CANCEL"), nullptr));
            });
        window->pushGui(options);
    });
}

// Shared existing-content row. Copying never changes emulator enablement.
static bool gcmV61AddExistingLocalRow(
    Window* window, GuiSettings* page,
    const std::string& rom, const std::string& emulator,
    const std::string& title, const std::string& state, const std::string& token,
    const std::function<bool(const std::string&)>& refresh)
{
    if (state.rfind("EXISTING_", 0) != 0) return false;
    // 61SY/61SZ: Switch and PS4 use COPY SELECTED TO GCM instead of one Install Locally row per item.
    if (emulator == "shadps4" && state == "EXISTING_EXTERNAL") gcmV74Ps4External()[rom] = true;
    if (gcmV36IsSharedSwitch(emulator) || emulator == "shadps4") return true;
    if (state != "EXISTING_EXTERNAL")
    {
        page->addWithLabel(title, gcmV3Value(window,
            state == "EXISTING_LOCAL" ? _("LOCAL COPY VERIFIED") : _("ASSESSMENT REQUIRED")));
        return true;
    }
    auto action = std::make_shared<GcmV35BatchActionLabel>(
        window, rom, emulator, "local:" + token);
    action->setCompletionCallback(
        [refresh](const std::string& status, const std::string& reply) -> bool
        { return refresh(status == "PASS" ? "" : gcmV35FailureMessage(reply)); });
    const std::string prior = gcmV35BatchRun(rom, emulator, "status", "", "local:" + token);
    if (gcmV35BatchField(prior, "STATUS") == "RUNNING")
        action->watch(gcmV35BatchField(prior, "ID"));
    page->addWithLabel(title, action, false,
        [window, rom, emulator, token, action]()
        {
            if (action->busy()) return;
            window->pushGui(new GuiMsgBox(window,
                _("COPY THIS INSTALLED CONTENT TO MANAGED STORAGE? ORIGINAL FILES AND ENABLEMENT WILL BE PRESERVED."),
                _("INSTALL LOCALLY"), [window, rom, emulator, token, action]()
                {
                    const std::string reply = gcmV35BatchRun(
                        rom, emulator, "start", "", "local:" + token);
                    const std::string id = gcmV35BatchField(reply, "ID");
                    if (gcmV35BatchField(reply, "STATUS") != "RUNNING" || id.empty())
                    {
                        window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));
                        return;
                    }
                    action->watch(id);
                }, _("CANCEL"), nullptr));
        });
    return true;
}

// 61SY: shared Switch content store (COPY SELECTED TO GCM).
static std::string gcmV72StoreRun(const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& action, const std::string& tokens = "")
{
    return gcmV35RunTool("python3 -B " + gcmV3Quote(emulator == "shadps4"
            ? "/userdata/system/gcm-native-test/step61-common-content/beta-ps4-content-store-v02/gcm-ps4-content-store-v02.py"
            : "/userdata/system/gcm-native-test/step61-common-content/beta-switch-content-store-v02/gcm-switch-content-store-v02.py")
        + " " + action + " --rom " + gcmV3Quote(rom) + " --emulator " + gcmV3Quote(emulator)
        + " --type " + gcmV3Quote(kind)
        + (action == "copy" ? " --tokens " + gcmV3Quote(tokens) + " --confirm" : std::string()));
}

static void gcmV72CopyToGcm(Window* window, const std::string& rom, const std::string& emulator,
    const std::string& kind, const std::string& tokens, int count,
    const std::function<bool(const std::string&)>& refreshed)
{
    window->pushGui(new GuiMsgBox(window,
        _("COPY SELECTED TO GCM?") + std::string("\n\n") + std::to_string(count) + " " + _("ITEMS") + "\n"
        + (emulator == "shadps4"
            ? _("INSTALLED FILES ARE COPIED TO GCM STORAGE AND CHECKED FILE BY FILE. SHADPS4 KEEPS USING ITS CURRENT FILES; NOTHING IS ENABLED OR DISABLED.")
            : _("EACH COPY IS CHECKED AGAINST ITS VERIFIED HASH, THEN THE EMULATOR USES THE GCM COPY. ORIGINAL FILES ARE KEPT.")),
        _("COPY"), [window, rom, emulator, kind, tokens, refreshed]()
        {
            window->pushGui(new GuiLoading<std::string>(window, _("COPYING SELECTED CONTENT TO GCM..."),
                [rom, emulator, kind, tokens](IGuiLoadingHandler*) { return gcmV72StoreRun(rom, emulator, kind, "copy", tokens); },
                [window, refreshed](std::string reply)
                {
                    const auto message = gcmV61CommandFailed(reply) ? gcmV35FailureMessage(reply) : std::string();
                    if (!refreshed(message) && !message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                }));
        }, _("CANCEL"), nullptr));
}

static void gcmV72OpenCopyPicker(Window* window, const std::string& rom,
    const std::string& emulator, const std::string& kind,
    const std::function<bool(const std::string&)>& refreshed)
{
    window->pushGui(new GuiLoading<std::string>(window, _("READING CONTENT..."),
        [rom, emulator, kind](IGuiLoadingHandler*) { return gcmV72StoreRun(rom, emulator, kind, "list"); },
        [window, rom, emulator, kind, refreshed](std::string output)
        {
            if (gcmV61CommandFailed(output))
            {
                window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(output), _("OK")));
                return;
            }
            auto picker = new GuiSettings(window, _("COPY SELECTED TO GCM"));
            auto controls = std::make_shared<std::vector<std::shared_ptr<GcmV31ConfirmSwitch>>>();
            auto tokens = std::make_shared<std::vector<std::string>>();
            auto master = std::make_shared<GcmV31ConfirmSwitch>(window);
            master->setState(false);
            std::istringstream lines(output); std::string line;
            while (std::getline(lines, line))
            {
                if (line.rfind("ROW\tAVAILABLE\t", 0) != 0) continue;
                const auto a = line.find('\t', 14), b = a == std::string::npos ? a : line.find('\t', a + 1);
                if (a == std::string::npos || b == std::string::npos) continue;
                const auto state = line.substr(14, a - 14), label = line.substr(a + 1, b - a - 1), token = line.substr(b + 1);
                if (state != "EXTERNAL")
                {
                    picker->addWithLabel(label, gcmV3Value(window, state == "IN_GCM" ? _("IN GCM")
                        : state == "CITRON_NAND" ? _("IN CITRON NAND") : _("CHECK NEEDED")));
                    continue;
                }
                auto toggle = std::make_shared<GcmV31ConfirmSwitch>(window);toggle->setState(false);
                controls->push_back(toggle);tokens->push_back(token);
                picker->addWithLabel(label, toggle, false, [controls, master, toggle]()
                {
                    toggle->setState(!toggle->getState());bool all = !controls->empty();
                    for (const auto& item : *controls) if (!item->getState()) all = false;
                    master->setState(all);
                });
            }
            if (controls->empty())
                picker->addEntry(_("EVERYTHING HERE IS ALREADY IN GCM"));
            else
            {
                gcmV34AddRightMaster(window, picker, master, [controls, master]()
                {
                    const bool desired = !master->getState();
                    for (const auto& toggle : *controls) toggle->setState(desired);
                    master->setState(desired);
                });
                gcmV34AddRightAction(window, picker, _("COPY"), [window, picker, rom, emulator, kind, controls, tokens, refreshed]()
                {
                    std::string selected;int count = 0;
                    for (unsigned i = 0; i < controls->size(); ++i) if ((*controls)[i]->getState())
                    { if (!selected.empty()) selected += ",";selected += (*tokens)[i];++count; }
                    if (count == 0)
                    {
                        window->pushGui(new GuiMsgBox(window, _("SELECT AT LEAST ONE ITEM"), _("OK")));
                        return;
                    }
                    gcmV72CopyToGcm(window, rom, emulator, kind, selected, count,
                        [picker, refreshed](const std::string& message) -> bool { picker->close();return refreshed(message); });
                });
            }
            window->pushGui(picker);
        }));
}

static void gcmV33OpenDlcPage(
    Window* window,
    const std::string& rom,
    const std::string& emulator,
    const std::function<void()>& refresh,
    const std::vector<std::string>& preservedTokens = {},
    int preservedCursor = -1,
    bool preserveSelections = false)
{
    GcmV68MenuReadScope menuRead;
    gcmV74Ps4External()[rom] = false;
    std::string output = gcmV3Run(
        rom, emulator, "--mode list --type dlc"
    );

    output = gcmV65DeleteOverlay(rom, emulator, "dlc", output);

    struct InventoryRow
    {
        std::string section;
        std::string state;
        std::string label;
        std::string token;
    };

    std::vector<InventoryRow> rows;
    std::istringstream stream(output);
    std::string line;

    while (std::getline(stream, line))
    {
        if (line.rfind("ERROR", 0) == 0)
        {
            window->pushGui(
                new GuiMsgBox(window, line, _("OK"))
            );
            return;
        }

        if (line.rfind("ROW\t", 0) != 0)
            continue;

        std::vector<std::string> fields;
        std::istringstream fieldStream(line);
        std::string field;

        while (std::getline(fieldStream, field, '\t'))
            fields.push_back(field);

        if (fields.size() != 5)
        {
            window->pushGui(new GuiMsgBox(
                window, _("INVALID DLC INVENTORY"), _("OK")
            ));
            return;
        }

        rows.push_back({
            fields[1], fields[2], fields[3], fields[4]
        });
    }

    const auto listScope = gcmV67ListScope(rom, emulator, "dlc");
    const bool minimized = gcmV67MinimizedLists()[listScope];
    unsigned availableTotal = 0, detectedTotal = 0;
    for (const auto& row : rows) { if (row.section == "AVAILABLE") ++availableTotal;else if (row.section == "DETECTED") ++detectedTotal; }
    auto page = new GcmV67ContentPage(window, _("DLC MANAGEMENT"), minimized);

    // 61SY: the page this one replaces, so the cursor can return to the action that was used.
    GcmV67ContentPage* previousPage = gcmV72LivePages()[listScope];
    const std::string previousAction = previousPage ? previousPage->actionAtCursor() : std::string();
    gcmV72LivePages()[listScope] = page;

    // Keep scan state and DETECTED refresh active
    // regardless of which menu row is highlighted.
    page->setUpdateType(
        ComponentListFlags::UpdateType::UPDATE_ALWAYS
    );

    auto available = std::make_shared<
        std::vector<GcmV33Choice>>();
    auto detected = std::make_shared<
        std::vector<GcmV33Choice>>();
    auto availableValue =
        gcmV3Value(window, "0 SELECTED");
    auto detectedValue =
        gcmV3Value(window, "0 SELECTED");

    auto availableMaster =
        std::make_shared<GcmV31ConfirmSwitch>(window);
    auto detectedMaster =
        std::make_shared<GcmV31ConfirmSwitch>(window);

    availableMaster->setState(false);
    detectedMaster->setState(false);

    auto updateCounts = [
        available, detected,
        availableValue, detectedValue, availableMaster, detectedMaster, minimized, availableTotal, detectedTotal
    ]()
    {
        int a = 0, d = 0;

        for (const auto& item : *available)
            if (item.check->getState())
                ++a;

        for (const auto& item : *detected)
            if (item.check->getState())
                ++d;

        availableValue->setText(
            minimized ? gcmV67Summary(_("TOTAL DLC: "), a, static_cast<unsigned>(available->size()), true) : std::to_string(a) + " SELECTED"
        );
        detectedValue->setText(
            minimized ? gcmV67Summary(_("TOTAL CANDIDATES: "), d, detectedTotal, true) : std::to_string(d) + " SELECTED"
        );

        availableMaster->setState(
            !available->empty() &&
            a == static_cast<int>(available->size())
        );

        detectedMaster->setState(
            !detected->empty() &&
            d == static_cast<int>(detected->size())
        );

    };


    const std::function<bool(const std::string&)> reopenPage =
        [window, page, rom, emulator, refresh, available, detected](
            const std::string& message) -> bool
        {
            gcmV66QueueRefresh(window, 
                [window, page, rom, emulator, refresh,
                 available, detected, message]()
                {
                    if (window->peekGui() != page)
                        return;
                    std::vector<std::string> selected;
                    for (const auto& choices : {available, detected})
                        for (const auto& item : *choices)
                            if (item.check->getState())
                                selected.push_back(item.token);
                    const int cursor = page->getMenu().getCursorIndex();
                    gcmV33OpenDlcPage(
                        window, rom, emulator, refresh, selected, cursor, true);
                    page->close();
                    if (refresh)
                        refresh();
                    if (!message.empty())
                        window->pushGui(new GuiMsgBox(window, message, _("OK")));
                }, page);
            return true;
        };

    auto addSelectionRows = [
        &rows, window, page, updateCounts, rom, emulator, reopenPage
    ](
        const std::string& section,
        const std::shared_ptr<
            std::vector<GcmV33Choice>>& choices)
    {
        for (const auto& row : rows)
        {
            if (row.section != section)
                continue;

            if (section == "AVAILABLE" && row.state == "IDENTITY_VERIFIED" && gcmV36IsSharedSwitch(emulator))
            {
                const auto jobType = "dlc-install:" + row.token;
                auto label = std::make_shared<GcmV35BatchActionLabel>(window, rom, emulator, jobType);
                label->setCompletionCallback([reopenPage](const std::string& state, const std::string& output) -> bool
                { return reopenPage(state == "PASS" ? "" : gcmV35FailureMessage(output)); });
                const auto prior = gcmV35BatchRun(rom, emulator, "status", "", jobType);
                if (gcmV35BatchField(prior, "STATUS") == "RUNNING") label->watch(gcmV35BatchField(prior, "ID"));
                ComponentListRow installRow;installRow.addElement(gcmV3Value(window, row.label), true);installRow.addElement(label, false);
                installRow.makeAcceptInputHandler([window, rom, emulator, jobType, label]()
                {
                    if (label->busy()) return;
                    window->pushGui(new GuiMsgBox(window, _("INSTALL DLC?\n\nCopy and register this DLC. External originals are preserved. Enable it afterward and use Apply Changes."), _("INSTALL"),
                        [window, rom, emulator, jobType, label]()
                        {
                            const auto reply = gcmV35BatchRun(rom, emulator, "start", "", jobType);
                            const auto id = gcmV35BatchField(reply, "ID");
                            if (gcmV35BatchField(reply, "STATUS") != "RUNNING" || id.empty())
                            { window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(reply), _("OK")));return; }
                            label->watch(id);
                        }, _("CANCEL"), nullptr));
                });
                page->addRow(installRow);continue;
            }

            if (gcmV61AddExistingLocalRow(window, page->listTarget(), rom, emulator,
                    row.label, row.state, row.token, reopenPage)) continue;

            if (gcmV61IsNativeFolder(emulator) && section == "AVAILABLE"
                && (row.state == "RECOVERY_REQUIRED" || row.state == "ASSESSMENT_REQUIRED"))
            {
                gcmV61AddNativeItemRow(window, page->listTarget(), rom, emulator, "dlc",
                    {row.section, row.state, row.label, row.token}, reopenPage);
                continue;
            }

            if (gcmV61UsesGlobalContentMenu(emulator) && section == "AVAILABLE"
                && row.state != "ENABLED" && row.state != "DISABLED")
            {
                page->addWithLabel(
                    row.label, gcmV3Value(window, _("UNVERIFIED")));
                continue;
            }

            auto check =
                std::make_shared<GcmV31ConfirmSwitch>(
                    window
                );

            check->setState(
                section == "AVAILABLE" &&
                row.state == "ENABLED"
            );

            choices->push_back({row.token, check});

            page->addWithLabel(
                row.label,
                check,
                false,
                [check, updateCounts]()
                {
                    check->setState(!check->getState());
                    updateCounts();
                }
            );
        }
    };

    auto addBulkSwitch = [window, page, updateCounts](
        const std::shared_ptr<std::vector<GcmV33Choice>>& choices,
        const std::shared_ptr<GcmV31ConfirmSwitch>& master)
    {
        if (choices->empty())
            return;

        gcmV34AddRightMaster(
            window, page, master,
            [choices, master, updateCounts]()
            {
                const bool desired = !master->getState();

                for (const auto& item : *choices)
                    item.check->setState(desired);

                updateCounts();
            }
        );
    };

    page->addGroup(_("AVAILABLE"));
    page->collectListRows(true);
    addSelectionRows("AVAILABLE", available);
    page->collectListRows(false);

    bool hasAvailableRows = false;
    for (const auto& row : rows) if (row.section == "AVAILABLE") hasAvailableRows = true;
    if (!hasAvailableRows && !minimized)
        page->addEntry(_("NO AVAILABLE DLC"));

    page->addWithLabel(
        "", availableValue
    );
    if (!available->empty())
    {
        ComponentListRow enableMaster;
        enableMaster.addElement(gcmV3Value(window, ""), true);
        enableMaster.addElement(gcmV3Value(window, _("SELECT ALL TO ENABLE")), false);
        enableMaster.addElement(gcmV3Value(window, "  "), false);
        enableMaster.addElement(availableMaster, false);
        enableMaster.makeAcceptInputHandler([available, availableMaster, updateCounts]()
        {
            const bool desired = !availableMaster->getState();
            for (const auto& item : *available) item.check->setState(desired);
            updateCounts();
        });
        page->addRow(enableMaster);
    }
    page->markAction("verify-unverified");
    gcmV34AddRightAction(window, page, _("VERIFY UNVERIFIED DLC"), [window, rom, emulator, reopenPage]()
    { gcmV68OpenVerify(window, rom, emulator, "dlc", [reopenPage]() { reopenPage(""); }); });

    // 61SY2/61SZ: COPY SELECTED TO GCM opens its own picker (IN GCM shown per DLC); the Available
    // on/off checks stay what they are: which DLC are enabled.
    if (gcmV36IsSharedSwitch(emulator) || (emulator == "shadps4" && gcmV74Ps4External()[rom]))
    {
        page->markAction("copy");
        gcmV34AddRightAction(window, page, _("COPY SELECTED TO GCM"), [window, rom, emulator, reopenPage]()
        { gcmV72OpenCopyPicker(window, rom, emulator, "dlc", reopenPage); });
    }

    // Apply and Uninstall share the Available DLC selection.
    auto uninstallLabel = std::make_shared<GcmV35BatchActionLabel>(
        window, rom, emulator, "dlc-uninstall");
    uninstallLabel->setText(_("UNINSTALL DLC"));
    uninstallLabel->setCompletionCallback(
        [reopenPage, rom, emulator](const std::string& status, const std::string& output) -> bool
        {
            if (status != "PASS") return reopenPage(gcmV35FailureMessage(output));
            gcmV76Forget(rom, emulator, "dlc", "after");
            const std::string note = gcmV75CleanupNote(gcmV75Cleanup());
            return reopenPage(note.empty() ? "" : std::string(_("DLC UNINSTALLED.")) + note);
        });
    const std::string priorUninstall = gcmV35BatchRun(
        rom, emulator, "status", "", "dlc-uninstall");
    if (gcmV35BatchField(priorUninstall, "STATUS") == "RUNNING")
        uninstallLabel->watch(gcmV35BatchField(priorUninstall, "ID"));
    ComponentListRow uninstallAction;
    uninstallAction.addElement(gcmV3Value(window, ""), true);
    uninstallAction.addElement(uninstallLabel, false);
    uninstallAction.makeAcceptInputHandler(
        [window, rom, emulator, available, uninstallLabel, reopenPage]()
        {
            if (uninstallLabel->busy()) return;
            std::string tokens;
            int count = 0;
            for (const auto& item : *available)
            {
                if (!item.check->getState()) continue;
                if (!tokens.empty()) tokens += ",";
                tokens += item.token;
                ++count;
            }
            if (tokens.empty())
            {
                window->pushGui(new GuiMsgBox(window, _("NO INSTALLED DLC"), _("OK")));
                return;
            }
            const std::string capability = gcmV35RunTool(
                "python3 -B " + gcmV3Quote(gcmV61IsNativeFolder(emulator)
                    ? "/userdata/system/gcm-native-test/step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py"
                    : "/userdata/system/gcm-native-test/step61-common-content/gcm-global-uninstall-v01.py")
                + " capabilities --emulator " + gcmV3Quote(emulator));
            if (capability.find("CAPABILITY\tUNINSTALL_DLC\t1") == std::string::npos)
            {
                window->pushGui(new GuiMsgBox(window, _("UNINSTALL IS NOT AVAILABLE FOR THIS EMULATOR YET"), _("OK")));
                return;
            }
            window->pushGui(new GuiMsgBox(window,
                std::string(_("UNINSTALL SELECTED DLC?")) + "\n\n"
                + std::to_string(count) + " " + _("ITEMS") + "\n"
                + _("GCM COPIES WILL BE PERMANENTLY DELETED. ORIGINAL PACKAGES WILL BE PRESERVED."),
                _("UNINSTALL"), [window, rom, emulator, tokens, uninstallLabel, reopenPage]()
                {
                    if (gcmV36IsCitron(emulator))
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("UNINSTALLING DLC..."),
                            [rom, emulator, tokens](IGuiLoadingHandler*)
                            {
                                gcmV76Forget(rom, emulator, "dlc", "before");
                                std::string reply = gcmV77CitronDlcRun(rom, tokens);
                                if (reply.rfind("OK\tUNINSTALLED=", 0) == 0)
                                {
                                    gcmV76Forget(rom, emulator, "dlc", "after");
                                    gcmV75Cleanup();
                                }
                                return reply;
                            },
                            [reopenPage](std::string reply) { reopenPage(gcmV77CitronDlcMessage(reply)); }));
                        return;
                    }
                    gcmV76Forget(rom, emulator, "dlc", "before");
                    const std::string response = gcmV35BatchRun(
                        rom, emulator, "start", tokens, "dlc-uninstall");
                    const std::string id = gcmV35BatchField(response, "ID");
                    if (gcmV35BatchField(response, "STATUS") != "RUNNING" || id.empty())
                    {
                        window->pushGui(new GuiMsgBox(window,
                            gcmV35FailureMessage(response), _("OK")));
                        return;
                    }
                    uninstallLabel->watch(id);
                }, _("CANCEL"), nullptr));
        });
    page->markAction("uninstall");
    page->addRow(uninstallAction);


    page->addGroup(_("DETECTED"));
    page->collectListRows(true);
    addSelectionRows("DETECTED", detected);
    page->collectListRows(false);

    if (preserveSelections || !preservedTokens.empty())
    {
        for (const auto& choices : {available, detected})
            for (const auto& item : *choices)
            {
                bool selected = false;
                for (const auto& token : preservedTokens)
                    if (item.token == token)
                        selected = true;
                item.check->setState(selected);
            }
    }

    if (detected->empty() && !minimized)
        page->addEntry(_("NO DETECTED DLC"));

    page->addWithLabel(
        "", detectedValue
    );
    addBulkSwitch(detected, detectedMaster);

    updateCounts();

    if (gcmV61UsesGlobalContentMenu(emulator))
    {
        auto verificationLabel = std::make_shared<GcmV35BatchActionLabel>(
            window, rom, emulator, "dlc");
        verificationLabel->setText(_("VERIFY DLC"));
        verificationLabel->setCompletionCallback(
            [reopenPage, rom, emulator, detected](const std::string& status,
                         const std::string& output) -> bool
            {
                if (status == "PASS")
                {
                    std::string tokens;
                    for (const auto& item : *detected) if (item.check->getState()) { if (!tokens.empty()) tokens += ",";tokens += item.token; }
                    gcmV65DeleteRun(rom, emulator, "dlc", "verified", tokens);
                }
                return reopenPage(
                    status == "PASS" ? "" : gcmV35FailureMessage(output));
            });

        const std::string existing = gcmV35BatchRun(
            rom, emulator, "status", "", "dlc");
        const std::string existingId = gcmV35BatchField(existing, "ID");
        if (gcmV35BatchField(existing, "STATUS") == "RUNNING"
            && !existingId.empty())
            verificationLabel->watch(existingId);

        ComponentListRow verifyAction;
        verifyAction.addElement(gcmV3Value(window, ""), true);
        verifyAction.addElement(verificationLabel, false);
        verifyAction.makeAcceptInputHandler(
            [window, rom, emulator, detected, verificationLabel, reopenPage]()
            {
                if (verificationLabel->busy())
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("VERIFICATION IN PROGRESS"), _("OK")));
                    return;
                }
                std::string tokens;
                int count = 0;
                for (const auto& item : *detected)
                {
                    if (!item.check->getState())
                        continue;
                    if (!tokens.empty())
                        tokens += ",";
                    tokens += item.token;
                    ++count;
                }
                if (count < 1 || (count > 512 && !gcmV36IsSharedSwitch(emulator)))
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("SELECT BETWEEN 1 AND 512 DETECTED DLC"),
                        _("OK")));
                    return;
                }
                if (count > 32 || gcmV36IsSharedSwitch(emulator))
                { gcmV69OpenDetectedBatch(window, rom, emulator, "dlc", tokens, reopenPage);return; }
                const std::string reply = gcmV35BatchRun(
                    rom, emulator, "start", tokens, "dlc");
                const std::string id = gcmV35BatchField(reply, "ID");
                if (reply.find("ERROR\t") != std::string::npos
                    || gcmV35BatchField(reply, "STATUS") != "RUNNING"
                    || id.empty())
                {
                    std::string reason = reply;
                    for (char& c : reason)
                        if (c == '\t' || c == '\r' || c == '\n')
                            c = ' ';
                    window->pushGui(new GuiMsgBox(
                        window, std::string(_("COULD NOT START VERIFICATION"))
                        + "\n\n" + reason.substr(0, 400), _("OK")));
                    return;
                }
                verificationLabel->watch(id);
            });
        page->markAction("verify-detected");
        page->addRow(verifyAction);
    }

    page->addGroup(_("CONTENT MANAGEMENT"));

    const std::function<bool()> refreshAfterScan =
        [reopenPage]() -> bool { return reopenPage(""); };

    if (gcmV61IsNativeFolder(emulator) || gcmV63IsExtraProvider(emulator))
        gcmV61AddNativeScanActions(window, page, rom, emulator, "dlc", [refreshAfterScan](const std::string&) { return refreshAfterScan(); });
    else
    gcmV34AddScanActions(
        window, page, rom, emulator, refreshAfterScan
    );


    auto minimizeToggle = std::make_shared<GcmV31ConfirmSwitch>(window);
    minimizeToggle->setState(minimized);
    ComponentListRow minimizeRow;
    minimizeRow.addElement(gcmV3Value(window, ""), true);
    minimizeRow.addElement(gcmV3Value(window, _("MINIMIZE LISTS")), false);
    minimizeRow.addElement(minimizeToggle, false);
    minimizeRow.makeAcceptInputHandler([listScope, minimizeToggle, reopenPage]()
    {
        const bool desired = !minimizeToggle->getState();
        minimizeToggle->setState(desired);gcmV67MinimizedLists()[listScope] = desired;
        gcmV68FocusMinimize()[listScope] = true;
        reopenPage("");
    });
    const int minimizeCursor = page->getMenu().size();
    page->addRow(minimizeRow);

    auto applyLabel = std::make_shared<GcmV35BatchActionLabel>(
        window, rom, emulator, "dlc-apply");
    applyLabel->setCompletionCallback(
        [reopenPage](const std::string& status, const std::string& output) -> bool
        { return reopenPage(status == "PASS" ? "" : gcmV35FailureMessage(output)); });
    if (gcmV61IsNativeFolder(emulator) || emulator == "shadps4" || gcmV36IsSharedSwitch(emulator))
    {
        const std::string existing = gcmV35BatchRun(rom, emulator, "status", "", "dlc-apply");
        const std::string id = gcmV35BatchField(existing, "ID");
        if (gcmV35BatchField(existing, "STATUS") == "RUNNING" && !id.empty())
            applyLabel->watch(id);
    }
    ComponentListRow applyAction;
    applyAction.addElement(gcmV3Value(window, ""), true);
    applyAction.addElement(applyLabel, false);
    applyAction.makeAcceptInputHandler(
        [window, page, rom, emulator,
         available, detected, refresh, reopenPage, applyLabel]()
        {
            if (applyLabel->busy()) return;
            if (gcmV61UsesGlobalContentMenu(emulator))
            {
                const std::string capabilities =
                    gcmV3Run(rom, emulator, "--mode capabilities");
                if (capabilities.find(
                        "CAPABILITY\tCOMMIT_AVAILABLE_DLC\t1\n")
                    == std::string::npos)
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("DLC CHANGES ARE NOT AVAILABLE"), _("OK")));
                    return;
                }
            }

            auto collect = [](
                const std::shared_ptr<
                    std::vector<GcmV33Choice>>& items)
            {
                std::string tokens;

                for (const auto& item : *items)
                {
                    if (!item.check->getState())
                        continue;

                    if (!tokens.empty())
                        tokens += ",";

                    tokens += item.token;
                }

                return tokens;
            };

            const std::string availableTokens =
                collect(available);
            const std::string detectedTokens =
                collect(detected);
            if (gcmV61UsesGlobalContentMenu(emulator) && !detectedTokens.empty())
            {
                window->pushGui(new GuiMsgBox(
                    window, _("VERIFY SELECTED DLC FIRST"), _("OK")));
                return;
            }

            int availableSelected = 0;
            int detectedSelected = 0;

            for (const auto& item : *available)
                if (item.check->getState())
                    ++availableSelected;

            for (const auto& item : *detected)
                if (item.check->getState())
                    ++detectedSelected;

            const int enable =
                availableSelected + detectedSelected;
            const int disable =
                static_cast<int>(available->size())
                - availableSelected;

            const std::string confirmation =
                _("APPLY DLC CHANGES?") +
                std::string("\n\n") +
                _("ENABLE DLC: ") +
                std::to_string(enable) +
                "\n" +
                _("DISABLE DLC: ") +
                std::to_string(disable);

            // Read the game's bracketed 16-character title ID.
            std::string titleId;
            std::size_t start = 0;

            while ((start = rom.find('[', start))
                   != std::string::npos)
            {
                const std::size_t end = rom.find(']', start);

                if (end == std::string::npos)
                    break;

                const std::string candidate =
                    rom.substr(start + 1, end - start - 1);

                if (candidate.size() == 16 &&
                    candidate.find_first_not_of(
                        "0123456789abcdefABCDEF"
                    ) == std::string::npos)
                {
                    titleId = candidate;
                    break;
                }

                start = end + 1;
            }

            // Shared Switch adapters resolve identity from the selected ROM.
            if (gcmV61UsesGlobalContentMenu(emulator))
                titleId.clear();
            if (titleId.empty() && !gcmV61UsesGlobalContentMenu(emulator))
            {
                window->pushGui(new GuiMsgBox(
                    window,
                    _("GAME TITLE ID NOT FOUND"),
                    _("OK")
                ));
                return;
            }

            window->pushGui(new GuiMsgBox(
                window,
                confirmation,
                _("YES"),
                [window, page, rom, emulator,
                 availableTokens, detectedTokens,
                 titleId, refresh, reopenPage, applyLabel]()
                {
                    if (gcmV61IsNativeFolder(emulator) || gcmV36IsSharedSwitch(emulator) || emulator == "shadps4")
                    {
                        const std::string reply = gcmV35BatchRun(
                            rom, emulator, "start", availableTokens, "dlc-apply");
                        const std::string id = gcmV35BatchField(reply, "ID");
                        if (gcmV35BatchField(reply, "STATUS") != "RUNNING" || id.empty())
                        {
                            window->pushGui(new GuiMsgBox(window,
                                gcmV35FailureMessage(reply), _("OK")));
                            return;
                        }
                        applyLabel->watch(id);
                        return;
                    }
                    const std::string arguments =
                        "--mode commit --type dlc " +
                        std::string("--rom-title-id ") +
                        gcmV3Quote(titleId) +
                        " --selected-available " +
                        gcmV3Quote(availableTokens) +
                        " --selected-detected " +
                        gcmV3Quote(detectedTokens) +
                        " --confirm";

                    if (gcmV31Execute(
                        window, rom, emulator, arguments))
                    {
                        if (refresh)
                            refresh();

                        // Closing prevents stale switches.
                        // Reopening reads verified backend state.
                        window->removeGui(page);
                    }
                },
                _("NO"),
                nullptr
            ));
        }
    );
    page->markAction("apply");
    page->addRow(applyAction);

    // Restore controller position as closely as possible.
    // 61SY: after an action finishes, return to that action rather than the old row number.
    const int actionTarget = previousAction.empty() ? -1 : page->actionRow(previousAction);
    if ((actionTarget >= 0 || preservedCursor >= 0 || gcmV68FocusMinimize()[listScope]) && page->getMenu().size() > 0)
    {
        int target = gcmV68FocusMinimize()[listScope] ? minimizeCursor : actionTarget >= 0 ? actionTarget : preservedCursor;
        gcmV68FocusMinimize()[listScope] = false;
        if (target >= page->getMenu().size())
            target = page->getMenu().size() - 1;

        const int current = page->getMenu().getCursorIndex();
        if (target != current)
            page->getMenu().getList()->moveCursor(
                target - current
            );
    }

    window->pushGui(page);
}


// GCM v3.6: read-only multi-emulator inventory page.
// Staged integration. Does not expose installation operations.
static std::string gcmV61CopyRun(
    const std::string& action,
    const std::string& rom,
    const std::string& token,
    const std::string& emulator,
    const std::string& type)
{
    if (emulator != "shadps4" ||
        (action != "start" && action != "status"))
        return "ERROR\tCOPY ADAPTER UNAVAILABLE\n";
    return gcmV35RunTool(
        "/usr/bin/timeout -k 2s 10s /usr/bin/python3 -B " +
        gcmV3Quote("/userdata/system/gcm-native-test/"
                   "step61-common-content/gcm-platform-native-copy.py") +
        " " + action + " --rom " + gcmV3Quote(rom) +
        " --emulator shadps4 --type " + gcmV3Quote(type) +
        " --token " + gcmV3Quote(token));
}

static std::string gcmV61CopyField(const std::string& output,
                                      const std::string& field)
{
    const std::string prefix = "COPY\t" + field + "\t";
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
        if (line.rfind(prefix, 0) == 0)
            return line.substr(prefix.size());
    return "";
}

class GcmV61CopyActionLabel : public TextComponent
{
    std::string mContentType;
public:
    GcmV61CopyActionLabel(Window* window, const std::string& rom,
                             const std::string& token,
                              const std::string& emulator = "ryujinx",
                              const std::string& type = "update")
        : TextComponent(window, _("COPY TO SYSTEM"),
                        ThemeData::getMenuTheme()->Text.font,
                        ThemeData::getMenuTheme()->Text.color),
          mContentType(type), mEmulator(emulator), mRom(rom), mToken(token), mElapsed(0)
    {
        setStatusText(_("COPY TO SYSTEM"));
        // Read only a small existing job record. Never hash an NSP in GUI update.
        const std::string reply = gcmV61CopyRun("status", mRom, mToken, mEmulator, mContentType);
        const std::string state = gcmV61CopyField(reply, "STATUS");
        const std::string id = gcmV61CopyField(reply, "JOB_ID");
        if (state == "RUNNING" && !id.empty())
            watch(id);
        else if (state == "READY")
            setStatusText(_("READY"));
        else if (gcmV61CopyField(reply, "LOCAL_STATE") == "READY")
            setStatusText(_("READY"));
    }


    // Preserve a constant width for every installation status.
    // Prevent INSTALLING... from being clipped after INSTALL changes.
    void setStatusText(const std::string& value)
    {
        const auto font = ThemeData::getMenuTheme()->Text.font;

        float width = font->sizeText(_("COPYING...")).x();

        const float installedWidth =
            font->sizeText(_("READY")).x();
        const float installWidth =
            font->sizeText(_("COPY TO SYSTEM")).x();
        const float readyWidth =
            font->sizeText(_("READY")).x();

        if (installedWidth > width) width = installedWidth;
        if (installWidth > width) width = installWidth;
        if (readyWidth > width) width = readyWidth;

        width += 16.0f;

        setText(value);
        setSize(width, getSize().y());
        setHorizontalAlignment(ALIGN_RIGHT);
    }

    bool busy() const { return mWatching || mPending; }
    void watch(const std::string& id)
    {
        mJobId = id;
        mElapsed = 0;
        mPollErrors = 0;
        mWatching = true;
        mPending = false;
        setStatusText(_("COPYING..."));
    }
    void setCompletionCallback(
        const std::function<bool(const std::string&, const std::string&)>& cb)
    {
        mOnComplete = cb;
    }
    void update(int deltaTime) override
    {
        TextComponent::update(deltaTime);
        if (!busy())
            return;
        mElapsed += deltaTime;
        if (mElapsed < 2000)
            return;
        mElapsed = 0;
        if (mWatching)
        {
            const std::string output = gcmV61CopyRun("status", mRom, mToken, mEmulator, mContentType);
            const std::string id = gcmV61CopyField(output, "JOB_ID");
            const std::string state = gcmV61CopyField(output, "STATUS");
            if (id == mJobId && (state == "READY" || state == "ERROR"))
            {
                mWatching = false;
                mPending = true;
                mResult = output;
                mResultState = state;
            }
            else if (output.find("ERROR\t") != std::string::npos ||
                     (!id.empty() && id != mJobId) || state == "NOT_STARTED")
            {
                if (++mPollErrors >= 3)
                {
                    mWatching = false;
                    mPending = true;
                    mResultState = "ERROR";
                    mResult = output + "\nCOPY\tERROR\tCopy job unavailable\n";
                }
            }
            else
                mPollErrors = 0;
        }
        if (mPending && mOnComplete && mOnComplete(mResultState, mResult))
        {
            mPending = false;
            setStatusText(mResultState == "READY" ? _("READY") : _("COPY TO SYSTEM"));
        }
    }
private:
    std::string mEmulator;
    std::string mRom, mToken, mJobId, mResult, mResultState;
    int mElapsed;
    int mPollErrors = 0;
    bool mWatching = false;
    bool mPending = false;
    std::function<bool(const std::string&, const std::string&)> mOnComplete;
};

static std::string gcmV61InstallRun(
    const std::string& action,
    const std::string& rom,
    const std::string& token,
    const std::string& emulator,
    const std::string& type)
{
    if (emulator != "shadps4" || type != "update" ||
        (action != "start" && action != "status"))
        return "ERROR\tINSTALL ADAPTER UNAVAILABLE\n";
    return gcmV35RunTool(
        "/usr/bin/timeout -k 2s 10s /usr/bin/python3 -B " +
        gcmV3Quote("/userdata/system/gcm-native-test/"
                   "step61-common-content/gcm-platform-native-install.py") +
        " " + action + " --rom " + gcmV3Quote(rom) +
        " --emulator shadps4 --type " + gcmV3Quote(type) +
        " --token " + gcmV3Quote(token));
}

static std::string gcmV61InstallField(const std::string& output,
                                      const std::string& field)
{
    const std::string prefix = "INSTALL\t" + field + "\t";
    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line))
        if (line.rfind(prefix, 0) == 0)
            return line.substr(prefix.size());
    return "";
}

class GcmV61InstallActionLabel : public TextComponent
{
    std::string mContentType;
public:
    GcmV61InstallActionLabel(Window* window, const std::string& rom,
                             const std::string& token,
                              const std::string& emulator = "ryujinx",
                              const std::string& type = "update")
        : TextComponent(window, _("INSTALL"),
                        ThemeData::getMenuTheme()->Text.font,
                        ThemeData::getMenuTheme()->Text.color),
          mContentType(type), mEmulator(emulator), mRom(rom), mToken(token), mElapsed(0)
    {
        setStatusText(_("INSTALL"));
        // Read only a small existing job record. Never hash an NSP in GUI update.
        const std::string reply = gcmV61InstallRun("status", mRom, mToken, mEmulator, mContentType);
        const std::string state = gcmV61InstallField(reply, "STATUS");
        const std::string id = gcmV61InstallField(reply, "JOB_ID");
        if (state == "RUNNING" && !id.empty())
            watch(id);
        else if (state == "INSTALLED_FILES_VERIFIED")
        {
            mInstalled = true;
            setStatusText(_("INSTALLED"));
        }
        else if (gcmV61InstallField(reply, "RECOVERY_REQUIRED") == "YES")
        {
            mBlocked = true;
            setStatusText(_("RECOVERY REQUIRED"));
        }
    }


    // Preserve a constant width for every installation status.
    // Prevent INSTALLING... from being clipped after INSTALL changes.
    void setStatusText(const std::string& value)
    {
        const auto font = ThemeData::getMenuTheme()->Text.font;

        float width = font->sizeText(_("RECOVERY REQUIRED")).x();

        const float installedWidth =
            font->sizeText(_("INSTALLED")).x();
        const float installWidth =
            font->sizeText(_("INSTALL")).x();
        const float readyWidth =
            font->sizeText(_("INSTALLED")).x();

        if (installedWidth > width) width = installedWidth;
        if (installWidth > width) width = installWidth;
        if (readyWidth > width) width = readyWidth;

        width += 16.0f;

        setText(value);
        setSize(width, getSize().y());
        setHorizontalAlignment(ALIGN_RIGHT);
    }

    bool busy() const { return mWatching || mPending; }
    bool installed() const { return mInstalled; }
    bool blocked() const { return mBlocked; }
    void watch(const std::string& id)
    {
        mJobId = id;
        mElapsed = 0;
        mPollErrors = 0;
        mWatching = true;
        mPending = false;
        setStatusText(_("INSTALLING..."));
    }
    void setCompletionCallback(
        const std::function<bool(const std::string&, const std::string&)>& cb)
    {
        mOnComplete = cb;
    }
    void update(int deltaTime) override
    {
        TextComponent::update(deltaTime);
        if (!busy())
            return;
        mElapsed += deltaTime;
        if (mElapsed < 2000)
            return;
        mElapsed = 0;
        if (mWatching)
        {
            const std::string output = gcmV61InstallRun("status", mRom, mToken, mEmulator, mContentType);
            const std::string id = gcmV61InstallField(output, "JOB_ID");
            const std::string state = gcmV61InstallField(output, "STATUS");
            if (state == "RUNNING")
                setStatusText(gcmV61InstallField(output, "PHASE") == "PREPARING"
                    ? _("PREPARING...") : _("INSTALLING..."));
            if (id == mJobId && (state == "INSTALLED_FILES_VERIFIED" || state == "ERROR"))
            {
                mWatching = false;
                mPending = true;
                mResult = output;
                mResultState = state;
            }
            else if (output.find("ERROR\t") != std::string::npos ||
                     (!id.empty() && id != mJobId) || state == "NOT_STARTED")
            {
                if (++mPollErrors >= 3)
                {
                    mWatching = false;
                    mPending = true;
                    mResultState = "ERROR";
                    mResult = output + "\nINSTALL\tERROR\tInstallation job unavailable\n";
                }
            }
            else
                mPollErrors = 0;
        }
        if (mPending && mOnComplete && mOnComplete(mResultState, mResult))
        {
            mPending = false;
            setStatusText(mResultState == "INSTALLED_FILES_VERIFIED" ? _("INSTALLED") : _("INSTALL"));
        }
    }
private:
    std::string mEmulator;
    std::string mRom, mToken, mJobId, mResult, mResultState;
    int mElapsed;
    int mPollErrors = 0;
    bool mInstalled = false;
    bool mBlocked = false;
    bool mWatching = false;
    bool mPending = false;
    std::function<bool(const std::string&, const std::string&)> mOnComplete;
};

static void gcmV3OpenPage(Window*, const std::string&, const std::string&,
    const std::string&, const std::function<void()>&,
    const std::vector<std::string>&, int);

static void gcmV36OpenReadOnlyPage(
    Window* window,
    const std::string& rom,
    const std::string& emulator,
    const std::string& type)
{
    if (gcmV61IsNativeFolder(emulator))
    {
        gcmV3OpenPage(window, rom, emulator, type, {}, {}, -1);
        return;
    }
    auto page = new GuiSettings(
        window, type == "update" ? _("UPDATES") : _("DLC"));

    page->setUpdateType(
        ComponentListFlags::UpdateType::UPDATE_ALWAYS);

    const std::string command =
        "/usr/bin/timeout -k 2s 10s /usr/bin/python3 -B " +
        gcmV3Quote("/userdata/system/gcm-native-test/"
                   "step61-common-content/gcm-platform-native-bridge.py") +
        " --rom " + gcmV3Quote(rom) +
        " --emulator " + gcmV3Quote(emulator) +
        " --type " + gcmV3Quote(type);

    const std::string output = gcmV35RunTool(command);
    std::istringstream input(output);
    std::string line;
    std::string error;
    std::vector<GcmV3Row> rows;

    while (std::getline(input, line))
    {
        if (line.rfind("ERROR\t", 0) == 0)
        {
            error = line.substr(6);
            continue;
        }
        if (line.rfind("ROW\t", 0) != 0)
            continue;
        std::istringstream fields(line);
        std::vector<std::string> values;
        std::string value;
        while (std::getline(fields, value, '\t'))
            values.push_back(value);
        if (values.size() != 5 || values[3].empty()
            || values[4].empty() || rows.size() >= 100)
        {
            error = "Invalid content inventory response";
            continue;
        }
        const bool available =
            values[1] == "AVAILABLE" &&
            (values[2] == "VERIFIED" || values[2] == "FILES_VERIFIED");
        const bool detected =
            values[1] == "DETECTED" && values[2] == "UNVERIFIED";
        if (!available && !detected)
        {
            error = "Unsupported content inventory state";
            continue;
        }
        GcmV3Row row;
        row.section = values[1];
        row.state = values[2];
        row.label = values[3];
        row.token = values[4];
        rows.push_back(row);
    }

    if (!error.empty())
    {
        page->addGroup(_("CONTENT INVENTORY"));
        page->addEntry(error);
        window->pushGui(page);
        return;
    }

    page->addGroup(_("AVAILABLE"));
    bool available = false;
    for (const auto& row : rows)
    {
        if (row.section != "AVAILABLE")
            continue;
        available = true;
        const bool installScope =
            emulator == "shadps4" && type == "update" && row.state == "VERIFIED";
        const std::string installReply = installScope
            ? gcmV61InstallRun("status", rom, row.token, emulator, type) : "";
        const std::string installState = gcmV61InstallField(installReply, "STATUS");
        const bool installationKnown =
            installState == "RUNNING" || installState == "INSTALLED_FILES_VERIFIED" ||
            gcmV61InstallField(installReply, "RECOVERY_REQUIRED") == "YES";
        const bool copyReady = installScope && !installationKnown &&
            gcmV61CopyField(
                gcmV61CopyRun("status", rom, row.token, emulator, type),
                "STATUS") == "READY";
        if (installScope && (installationKnown || copyReady))
        {
            auto installLabel = std::make_shared<GcmV61InstallActionLabel>(
                window, rom, row.token, emulator, type);
            installLabel->setCompletionCallback(
                [window, page, rom, emulator, type](
                    const std::string& state, const std::string& output) -> bool
                {
                    if (window->peekGui() != page)
                        return false;
                    const std::string failure = state == "INSTALLED_FILES_VERIFIED"
                        ? "" : gcmV61InstallField(output, "ERROR");
                    window->postToUiThread(
                        [window, page, rom, emulator, type, state, failure]()
                        {
                            if (window->peekGui() != page)
                                return;
                            gcmV36OpenReadOnlyPage(window, rom, emulator, type);
                            page->close();
                            if (state != "INSTALLED_FILES_VERIFIED")
                                window->pushGui(new GuiMsgBox(
                                    window, failure.empty()
                                        ? _("INSTALLATION DID NOT COMPLETE") : failure,
                                    _("OK")));
                        }, page);
                    return true;
                });
            page->addWithLabel(
                row.label, installLabel, false,
                [window, rom, emulator, type, row, installLabel]()
                {
                    if (installLabel->busy() || installLabel->installed())
                        return;
                    if (installLabel->blocked())
                    {
                        window->pushGui(new GuiMsgBox(
                            window, _("INSTALLATION RECOVERY REQUIRED"), _("OK")));
                        return;
                    }
                    window->pushGui(new GuiMsgBox(
                        window,
                        _("INSTALL VERIFIED UPDATE?\n\n"
                          "EXISTING FILES WILL BE BACKED UP. "
                          "THE SOURCE PACKAGE WILL BE PRESERVED."),
                        _("YES"),
                        [window, rom, emulator, type, row, installLabel]()
                        {
                            const std::string reply = gcmV61InstallRun(
                                "start", rom, row.token, emulator, type);
                            const std::string id =
                                gcmV61InstallField(reply, "JOB_ID");
                            if (gcmV61InstallField(reply, "STATUS") == "RUNNING"
                                && !id.empty()
                                && reply.find("ERROR\t") == std::string::npos)
                                installLabel->watch(id);
                            else
                            {
                                std::string error = reply;
                                for (char& c : error)
                                    if (c == '\t' || c == '\r' || c == '\n')
                                        c = ' ';
                                window->pushGui(new GuiMsgBox(
                                    window, error.substr(0, 600), _("OK")));
                            }
                        },
                        _("NO"), nullptr));
                });
        }
        else if (emulator == "shadps4" && row.state == "VERIFIED")
        {
            auto copyLabel = std::make_shared<GcmV61CopyActionLabel>(
                window, rom, row.token, emulator, type);
            copyLabel->setCompletionCallback(
                [window, page, rom, emulator, type](
                    const std::string& state,
                    const std::string& output) -> bool
                {
                    if (window->peekGui() != page)
                        return false;
                    const std::string failure = state == "READY"
                        ? "" : gcmV61CopyField(output, "ERROR");
                    window->postToUiThread(
                        [window, page, rom, emulator, type, state, failure]()
                        {
                            if (window->peekGui() != page)
                                return;
                            gcmV36OpenReadOnlyPage(window, rom, emulator, type);
                            page->close();
                            if (state != "READY")
                                window->pushGui(new GuiMsgBox(
                                    window, failure.empty()
                                        ? _("COPY DID NOT COMPLETE") : failure,
                                    _("OK")));
                        }, page);
                    return true;
                });
            page->addWithLabel(
                row.label, copyLabel, false,
                [window, rom, emulator, type, row, copyLabel]()
                {
                    if (copyLabel->busy())
                        return;
                    window->pushGui(new GuiMsgBox(
                        window,
                        _("COPY VERIFIED PACKAGE TO SYSTEM STORAGE?\n\n"
                          "THE EXTERNAL ORIGINAL WILL BE PRESERVED."),
                        _("YES"),
                        [window, rom, emulator, type, row, copyLabel]()
                        {
                            const std::string reply = gcmV61CopyRun(
                                "start", rom, row.token, emulator, type);
                            const std::string id =
                                gcmV61CopyField(reply, "JOB_ID");
                            if (gcmV61CopyField(reply, "STATUS") == "RUNNING"
                                && !id.empty()
                                && reply.find("ERROR\t") == std::string::npos)
                                copyLabel->watch(id);
                            else
                            {
                                std::string error = reply;
                                for (char& c : error)
                                    if (c == '\t' || c == '\r' || c == '\n')
                                        c = ' ';
                                window->pushGui(new GuiMsgBox(
                                    window, error.substr(0, 600), _("OK")));
                            }
                        },
                        _("NO"), nullptr));
                });
        }
        else
            page->addWithLabel(
                row.label,
                gcmV3Value(window, row.state == "FILES_VERIFIED"
                    ? _("INSTALLED FILES") : _("VERIFIED")));
    }
    if (!available)
        page->addEntry(_("NO AVAILABLE CONTENT"));

    page->addGroup(_("DETECTED"));
    auto selections = std::make_shared<std::vector<GcmV33Choice>>();
    bool detected = false;
    for (const auto& row : rows)
    {
        if (row.section != "DETECTED")
            continue;
        detected = true;
        if (emulator == "shadps4")
        {
            auto check = std::make_shared<GcmV31ConfirmSwitch>(window);
            check->setState(false);
            selections->push_back({row.token, check});
            page->addWithLabel(
                row.label, check, false,
                [check]() { check->setState(!check->getState()); });
        }
        else
            page->addWithLabel(
                row.label, gcmV3Value(window, _("UNVERIFIED")));
    }
    if (!detected)
        page->addEntry(_("NO DETECTED CONTENT"));

    page->addGroup(_("CONTENT MANAGEMENT"));

    if (emulator == "shadps4")
    {
        auto verification = std::make_shared<GcmV35BatchActionLabel>(
            window, rom, emulator, type);
        verification->setHorizontalAlignment(ALIGN_RIGHT);
        verification->setCompletionCallback(
            [window, page, rom, emulator, type](
                const std::string& status,
                const std::string& output) -> bool
            {
                if (window->peekGui() != page)
                    return false;
                const std::string failure =
                    status == "PASS" ? "" : gcmV35FailureMessage(output);
                window->postToUiThread(
                    [window, page, rom, emulator, type, failure]()
                    {
                        if (window->peekGui() != page)
                            return;
                        gcmV36OpenReadOnlyPage(window, rom, emulator, type);
                        page->close();
                        if (!failure.empty())
                            window->pushGui(new GuiMsgBox(
                                window, failure, _("OK")));
                    }, page);
                return true;
            });

        const std::string existing = gcmV35BatchRun(
            rom, emulator, "status", "", type);
        const std::string existingId = gcmV35BatchField(existing, "ID");
        if (gcmV35BatchField(existing, "STATUS") == "RUNNING"
            && !existingId.empty())
            verification->watch(existingId);

        page->addWithLabel(
            "", verification, false,
            [window, rom, emulator, type, selections, verification]()
            {
                if (verification->busy())
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("VERIFICATION IN PROGRESS"), _("OK")));
                    return;
                }
                std::string tokens;
                int count = 0;
                for (const auto& item : *selections)
                {
                    if (!item.check->getState())
                        continue;
                    if (!tokens.empty())
                        tokens += ",";
                    tokens += item.token;
                    ++count;
                }
                if (count < 1 || count > 32)
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("SELECT BETWEEN 1 AND 32 CANDIDATES"), _("OK")));
                    return;
                }
                const std::string reply = gcmV35BatchRun(
                    rom, emulator, "start", tokens, type);
                const std::string id = gcmV35BatchField(reply, "ID");
                if (gcmV35BatchField(reply, "STATUS") != "RUNNING"
                    || id.empty() || reply.find("ERROR\t") != std::string::npos)
                {
                    std::string error = reply;
                    for (char& c : error)
                        if (c == '\t' || c == '\r' || c == '\n')
                            c = ' ';
                    window->pushGui(new GuiMsgBox(
                        window, error.substr(0, 600), _("OK")));
                    return;
                }
                verification->watch(id);
            });
    }
    gcmV34AddRightAction(
        window, page, _("REFRESH"),
        [window, page, rom, emulator, type]()
        {
            gcmV36OpenReadOnlyPage(window, rom, emulator, type);
            page->close();
        });
    window->pushGui(page);
}


// Step 54: Citron read-only development interface.
// No installation, registration, activation or file modification.
// Step 54: Citron live global NAND inventory, read-only.
// Counts are NAND-wide, NOT game-specific update/DLC identities.
// No installation, registration, activation or file modification.
static void gcmV36OpenCitronReadOnlyPage(
    Window* window,
    const std::string& rom,
    const std::string& type)
{
    // Step 58 visual shell.
    // No emulator operations, filesystem scans or persistent changes.
    (void)rom;

    const bool updates = type == "update";

    auto page = new GuiSettings(
        window,
        updates ? _("UPDATES") : _("DLC")
    );

    page->addGroup(_("AVAILABLE"));

    if (updates)
    {
        // Temporary rows for layout testing only.
        page->addWithLabel(
            _("V1.0.0"),
            gcmV3Value(window, _("ACTIVE"))
        );

        page->addWithLabel(
            _("V2.0.0"),
            gcmV3Value(window, _("SELECT")),
            false,
            []() {}
        );
    }
    else
    {
        // Step 58J: UI-only DLC selection preview.
        // No backend operations or persistent changes.

        auto first = std::make_shared<GcmV31ConfirmSwitch>(
            window
        );
        auto second = std::make_shared<GcmV31ConfirmSwitch>(
            window
        );
        auto master = std::make_shared<GcmV31ConfirmSwitch>(
            window
        );

        first->setState(true);
        second->setState(false);
        master->setState(false);

        const auto refreshMaster = [first, second, master]()
        {
            master->setState(
                first->getState() && second->getState()
            );
        };

        page->addWithLabel(
            _("DLC ITEM A"),
            first,
            false,
            [first, refreshMaster]()
            {
                first->setState(!first->getState());
                refreshMaster();
            }
        );

        page->addWithLabel(
            _("DLC ITEM B"),
            second,
            false,
            [second, refreshMaster]()
            {
                second->setState(!second->getState());
                refreshMaster();
            }
        );

        // Reuse the exact Ryujinx right-side master row.
        gcmV34AddRightMaster(
            window,
            page,
            master,
            [first, second, master]()
            {
                const bool selected =
                    first->getState() && second->getState();

                const bool next = !selected;

                first->setState(next);
                second->setState(next);
                master->setState(next);
            }
        );
    }

    page->addGroup(_("DETECTED"));

    page->addEntry(
        _("NO NEW PACKAGES IN CURRENT INVENTORY")
    );

    page->addGroup(_("CONTENT MANAGEMENT"));

    // These use the existing GCM selectable, right-aligned
    // action-row constructor. Callbacks intentionally do nothing.

    gcmV34AddRightAction(
        window,
        page,
        _("SCAN ALL STORAGE"),
        []() {}
    );

    gcmV34AddRightAction(
        window,
        page,
        _("SCAN STATUS"),
        []() {}
    );

    gcmV34AddRightAction(
        window,
        page,
        _("APPLY CHANGES"),
        []() {}
    );

    window->pushGui(page);
}

static void gcmV3OpenPage(
    Window* window,
    const std::string& rom,
    const std::string& emulator,
    const std::string& type,
    const std::function<void()>& refresh,
    const std::vector<std::string>& preservedTokens = {},
    int preservedCursor = -1)
{
    if (type == "dlc")
    {
        gcmV33OpenDlcPage(window, rom, emulator, refresh,
                          preservedTokens, preservedCursor);
        return;
    }

    GcmV68MenuReadScope menuRead;
    gcmV74Ps4External()[rom] = false;
    const std::string title =
        type == "update" ? _("UPDATES") : _("DLC");

    const auto listScope = gcmV67ListScope(rom, emulator, type);
    const bool minimized = gcmV67MinimizedLists()[listScope];
    auto page = new GcmV67ContentPage(window, title.c_str(), minimized);

    // 61SY: the page this one replaces, so the cursor can return to the action that was used.
    GcmV67ContentPage* previousPage = gcmV72LivePages()[listScope];
    const std::string previousAction = previousPage ? previousPage->actionAtCursor() : std::string();
    gcmV72LivePages()[listScope] = page;

    // Keep scan state and DETECTED refresh active
    // regardless of which menu row is highlighted.
    page->setUpdateType(
        ComponentListFlags::UpdateType::UPDATE_ALWAYS
    );

    std::string output = gcmV61UsesGlobalContentMenu(emulator)
        ? gcmV3Run(rom, emulator,
                   "--mode list --type " + gcmV3Quote(type))
        : type == "update"
            ? gcmV35VerifiedInventoryRun(rom, emulator)
            : gcmV3Run(rom, emulator,
                       "--mode list --type " + gcmV3Quote(type));

    if (type == "update")
    {
        const auto retained = gcmV62UpdateRemovalRun(rom, emulator, "retained")
            + gcmV63ImportUpdateRun(rom, emulator, "list")
            + gcmV64ActivationRun(rom, emulator, "list");
        std::istringstream retainedLines(retained);std::string retainedLine;
        while (std::getline(retainedLines, retainedLine))
            if (retainedLine.rfind("ROW\t", 0) == 0)
            {
                const auto split = retainedLine.rfind('\t');
                const auto token = split == std::string::npos ? std::string() : retainedLine.substr(split + 1);
                if (token.empty()) continue;
                std::istringstream existing(output);std::string existingLine, kept;
                while (std::getline(existing, existingLine))
                {
                    const auto at = existingLine.rfind('\t');
                    if (existingLine.rfind("ROW\t", 0) == 0 && at != std::string::npos && existingLine.substr(at + 1) == token) continue;
                    kept += existingLine + "\n";
                }
                output = kept + retainedLine + "\n";
            }
    }

    output = gcmV65DeleteOverlay(rom, emulator, type, output);

    std::vector<GcmV3Row> rows;
    std::string error;

    std::istringstream stream(output);
    std::string line;

    while (std::getline(stream, line))
    {
        if (line.rfind("ERROR\t", 0) == 0)
        {
            error = line.substr(6);
            continue;
        }

        if (line.rfind("ROW\t", 0) != 0)
            continue;

        const size_t first = line.find('\t', 4);
        const size_t second =
            first == std::string::npos ?
            std::string::npos :
            line.find('\t', first + 1);

        if (second == std::string::npos)
            continue;

        GcmV3Row row;

        row.section = line.substr(4, first - 4);
        row.state = line.substr(
            first + 1, second - first - 1
        );
        const size_t third = line.find('\t', second + 1);

        if (third == std::string::npos)
            continue;

        row.label = line.substr(
            second + 1, third - second - 1
        );
        row.token = line.substr(third + 1);

        if (rows.size() < 100)
            rows.push_back(row);
    }

    if (!error.empty())
    {
        std::vector<GcmV3Row> recoveryRows;
        for (const auto& row : rows)
            if (row.state == "ACTIVATION_RECOVERY_REQUIRED") recoveryRows.push_back(row);
        if (!recoveryRows.empty()) { rows = recoveryRows;error.clear(); }
    }

    if (!error.empty())
    {
        page->addGroup(_("CONTENT INVENTORY"));
        page->addEntry("ERROR: " + error);
        window->pushGui(page);
        return;
    }

    auto dlcControls =
        std::make_shared<std::vector<GcmV32DlcControl>>();

    page->addGroup(_("AVAILABLE"));

    bool available = false;

    page->collectListRows(true);
    for (const auto& row : rows)
    {
        if (row.section != "AVAILABLE")
            continue;

        available = true;

        if (type == "update" && (row.state == "RETAINED" || row.state == "RESTORE_REVIEW"))
        {
            page->addWithLabel(row.label, gcmV3Value(window, row.state == "RETAINED" ? _("RESTORE") : _("REVIEW")), false,
                [window, page, rom, emulator, type, refresh, row]()
                {
                    if (row.state == "RESTORE_REVIEW")
                    {
                        window->pushGui(new GuiLoading<std::string>(window, _("READING RESTORE DETAILS..."),
                            [rom, emulator, row](IGuiLoadingHandler*) { return gcmV62UpdateRemovalRun(rom, emulator, "preview", row.token); },
                            [window](std::string details) { window->pushGui(new GuiMsgBox(window, gcmV61DialogText(details), _("OK"))); }));
                        return;
                    }
                    window->pushGui(new GuiMsgBox(window, _("RESTORE THIS RETAINED UPDATE?\n\nExisting content will not be overwritten. External originals remain unchanged."),
                        _("RESTORE"), [window, page, rom, emulator, type, refresh, row]()
                        {
                            window->pushGui(new GuiLoading<std::string>(window, _("RESTORING UPDATE..."),
                                [rom, emulator, row](IGuiLoadingHandler*) { return gcmV62UpdateRemovalRun(rom, emulator, "restore", row.token); },
                                [window, page, rom, emulator, type, refresh](std::string response)
                                {
                                    if (response.rfind("OK\tRESTORED", 0) == 0)
                                    {
                                        gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                    }
                                    else
                                    {
                                        gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                                        { gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh(); }, page);
                                        window->pushGui(new GuiMsgBox(window, gcmV35FailureMessage(response), _("OK")));
                                    }
                                }));
                        }, _("CANCEL"), nullptr));
                });
            continue;
        }

        if (type == "update" && row.state == "IMPORTED_ARCHIVE")
        {
            page->addWithLabel(row.label, gcmV3Value(window, _("LOCAL COPY")), false,
                [window, page, rom, emulator, type, refresh, row]()
                {
                    auto choices = new GuiSettings(window, row.label.c_str());
                    choices->addEntry(_("INSTALL / REINSTALL UPDATE"), false,
                        [window, page, choices, rom, emulator, type, refresh, row]()
                        {
                            window->pushGui(new GuiMsgBox(window,
                                _("INSTALL THIS LOCAL UPDATE COPY?\n\nDifferent existing content will not be overwritten. Originals remain preserved."),
                                _("INSTALL"), [window, page, choices, rom, emulator, type, refresh, row]()
                                {
                                    choices->close();
                                    window->pushGui(new GuiLoading<std::string>(window, _("INSTALLING LOCAL UPDATE..."),
                                        [rom, emulator, row](IGuiLoadingHandler*) { return gcmV63ImportUpdateRun(rom, emulator, "install", row.token); },
                                        [window, page, rom, emulator, type, refresh](std::string response)
                                        {
                                            const auto message = gcmV61CommandFailed(response) ? gcmV35FailureMessage(response) : gcmV61DialogText(response);
                                            gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                            window->pushGui(new GuiMsgBox(window, message, _("OK")));
                                        }));
                                }, _("CANCEL"), nullptr));
                        });
                    choices->addEntry(_("UNINSTALL UPDATE"), false,
                        [window, page, choices, rom, emulator, type, refresh]()
                        {
                            // 61TE2: close() destroys this action; run a local copy afterwards.
                            auto open = [window, page, rom, emulator, type, refresh]()
                            {
                                gcmV62OpenUpdateRemoval(window, rom, emulator, [window, page, rom, emulator, type, refresh]()
                                {
                                    gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                                    {
                                        if (window->peekGui() != page) return;
                                        gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                    }, page);
                                });
                            };
                            choices->close();
                            open();
                        });
                    choices->addEntry(_("RECOVER INTERRUPTED INSTALL"), false,
                        [window, page, choices, rom, emulator, type, refresh, row]()
                        {
                            window->pushGui(new GuiMsgBox(window, _("RECOVER THIS UPDATE'S INTERRUPTED INSTALL?"), _("RECOVER"),
                                [window, page, choices, rom, emulator, type, refresh, row]()
                                {
                                    choices->close();
                                    window->pushGui(new GuiLoading<std::string>(window, _("RECOVERING UPDATE..."),
                                        [rom, emulator, row](IGuiLoadingHandler*) { return gcmV63ImportUpdateRun(rom, emulator, "recover", row.token); },
                                        [window, page, rom, emulator, type, refresh](std::string report)
                                        {
                                            gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                                            {
                                                gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                            }, page);
                                            window->pushGui(new GuiMsgBox(window, gcmV61DialogText(report), _("OK")));
                                        }));
                                }, _("CANCEL"), nullptr));
                        });
                    choices->addEntry(_("VERIFY LOCAL COPY"), false,
                        [window, rom, emulator, row]()
                        {
                            window->pushGui(new GuiLoading<std::string>(window, _("CHECKING LOCAL COPY..."),
                                [rom, emulator, row](IGuiLoadingHandler*) { return gcmV63ImportUpdateRun(rom, emulator, "details", row.token); },
                                [window](std::string report) { window->pushGui(new GuiMsgBox(window, gcmV61DialogText(report), _("OK"))); }));
                        });
                    window->pushGui(choices);
                });
            continue;
        }

        if (type == "update" && ((row.state == "ACTIVE" && row.token.rfind("folder:", 0) != 0)
            || row.state == "ACTIVATION_RECOVERY_REQUIRED" || row.state == "DEACTIVATED_UPDATE"))
        {
            const bool recovery = row.state == "ACTIVATION_RECOVERY_REQUIRED";
            const bool activation = row.state == "DEACTIVATED_UPDATE";
            page->addWithLabel(row.label, gcmV3Value(window, recovery ? _("RECOVER") : activation ? _("SELECT") : _("ACTIVE")), false,
                [window, page, rom, emulator, type, refresh, row, recovery, activation]()
                {
                    auto options = new GuiSettings(window, row.label.c_str());
                    options->addEntry(recovery ? _("RECOVER DEACTIVATION") : activation ? _("ACTIVATE") : _("DEACTIVATE"), false,
                        [window, page, options, rom, emulator, type, refresh, row, recovery, activation]()
                        {
                            window->pushGui(new GuiMsgBox(window,
                                recovery ? _("RECOVER THIS INTERRUPTED DEACTIVATION?") : activation ? _("ACTIVATE THIS UPDATE?")
                                    : _("DEACTIVATE THIS UPDATE?\n\nInstalled files and registrations will be preserved. You can select the update again from Available."),
                                recovery ? _("RECOVER") : activation ? _("ACTIVATE") : _("DEACTIVATE"),
                                [window, page, options, rom, emulator, type, refresh, row, recovery, activation]()
                                {
                                    options->close();
                                    window->pushGui(new GuiLoading<std::string>(window,
                                        recovery ? _("RECOVERING...") : activation ? _("ACTIVATING UPDATE...") : _("DEACTIVATING UPDATE..."),
                                        [rom, emulator, row, recovery, activation](IGuiLoadingHandler*)
                                            { return gcmV64ActivationRun(rom, emulator, recovery ? "recover" : activation ? "activate" : "deactivate", row.token); },
                                        [window, page, rom, emulator, type, refresh](std::string reply)
                                        {
                                            gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                            window->pushGui(new GuiMsgBox(window, gcmV61CommandFailed(reply)
                                                ? gcmV35FailureMessage(reply) : gcmV61DialogText(reply), _("OK")));
                                        }));
                                }, _("CANCEL"), nullptr));
                        });
                    if (!recovery && !activation) options->addEntry(_("UNINSTALL UPDATE"), false,
                        [window, page, options, rom, emulator, type, refresh]()
                        {
                            // 61TE2: close() destroys this action; run a local copy afterwards.
                            auto open = [window, page, rom, emulator, type, refresh]()
                            {
                                gcmV62OpenUpdateRemoval(window, rom, emulator,
                                    [window, page, rom, emulator, type, refresh]()
                                    {
                                        gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                                        {
                                            if (window->peekGui() != page) return;
                                            gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                        }, page);
                                    });
                            };
                            options->close();
                            open();
                        });
                    window->pushGui(options);
                });
            continue;
        }

        if (type == "update" && row.state == "REGISTERED_UNVERIFIED")
        {
            page->addWithLabel(row.label, gcmV3Value(window,
                row.state == "ACTIVE" ? _("ACTIVE") : _("UNVERIFIED")), false,
                [window, page, rom, emulator, type, refresh]()
                {
                    gcmV62OpenUpdateRemoval(window, rom, emulator, [window, page, rom, emulator, type, refresh]()
                    {
                        gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                        {
                            if (window->peekGui() != page) return;
                            gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();
                            if (refresh) refresh();
                        }, page);
                    });
                });
            continue;
        }

        if (gcmV61IsNativeFolder(emulator))
        {
            gcmV61AddNativeItemRow(window, page->listTarget(), rom, emulator, type, row,
                [window, page, rom, emulator, type, refresh](const std::string& message) -> bool
                {
                    gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh, message]()
                    {
                        if (window->peekGui() != page) return;
                        gcmV3OpenPage(window, rom, emulator, type, refresh);
                        page->close();
                        if (refresh) refresh();
                        if (!message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                    }, page);
                    return true;
                });
            continue;
        }

        if (gcmV61AddExistingLocalRow(window, page->listTarget(), rom, emulator,
                row.label, row.state, row.token,
                [window, page, rom, emulator, type, refresh](const std::string& message) -> bool
                {
                    gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh, message]()
                    {
                        if (window->peekGui() != page) return;
                        gcmV3OpenPage(window, rom, emulator, type, refresh);
                        page->close();
                        if (refresh) refresh();
                        if (!message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                    }, page);
                    return true;
                })) continue;

        if (row.state == "REGISTERED_UNVERIFIED")
        {
            page->addWithLabel(
                row.label, gcmV3Value(window, _("UNVERIFIED")));
            continue;
        }

        if (type == "update")
        {
            if (gcmV61UsesGlobalContentMenu(emulator) && row.state == "INSTALLED")
            {
                page->addWithLabel(
                    row.label, gcmV3Value(window, _("SELECT")), false,
                    [window, page, rom, emulator, type, row, refresh, preservedTokens]()
                    {
                        window->pushGui(new GuiMsgBox(
                            window, _("ACTIVATE THIS UPDATE?"),
                            _("YES"),
                            [window, page, rom, emulator, type, row, refresh, preservedTokens]()
                            {
                                window->pushGui(new GuiLoading<std::string>(
                                    window, _("SELECTING UPDATE..."),
                                    [rom, emulator, row](IGuiLoadingHandler*)
                                    {
                                        try
                                        {
                                            return gcmV3Run(
                                                rom, emulator,
                                                "--mode select-update --type update --token " +
                                                gcmV3Quote(row.token) + " --confirm");
                                        }
                                        catch (...)
                                        {
                                            return std::string(
                                                "ERROR\tUPDATE SELECTION FAILED\n");
                                        }
                                    },
                                    [window, page, rom, emulator, type, refresh, preservedTokens](std::string result)
                                    {
                                        const bool success = result.rfind(
                                            "OK\tCONFIGURED_UPDATE_SELECTED", 0) == 0;
                                        if (success)
                                        {
                                            if (window->peekGui() != page)
                                                return;
                                            const int cursor =
                                                page->getMenu().getCursorIndex();
                                            gcmV3OpenPage(
                                                window, rom, emulator, type,
                                                refresh, preservedTokens, cursor);
                                            page->close();
                                            if (refresh)
                                                refresh();
                                            window->pushGui(new GuiMsgBox(
                                                window, _("UPDATE SELECTED"), _("OK")));
                                            return;
                                        }
                                        if (result.rfind("ERROR\t", 0) == 0)
                                            result.erase(0, 6);
                                        for (char& c : result)
                                            if (c == '\t' || c == '\r' || c == '\n')
                                                c = ' ';
                                        if (result.empty())
                                            result = "UPDATE SELECTION FAILED";
                                        window->pushGui(new GuiMsgBox(
                                            window, result.substr(0, 300), _("OK")));
                                    }));
                            },
                            _("NO"), nullptr));
                    });
            }
            else if (row.state == "ACTIVE")
            {
                page->addWithLabel(
                    row.label,
                    gcmV3Value(window, _("ACTIVE"))
                );
            }
            else if (row.state == "VERIFIED_EXTERNAL")
            {
                auto installLabel = std::make_shared<GcmV35InstallActionLabel>(
                    window, rom, row.token, emulator);
                installLabel->setCompletionCallback(
                    [window, page, rom, emulator, type, refresh, preservedTokens](
                        const std::string& state,
                        const std::string& result) -> bool
                    {
                        gcmV66QueueRefresh(window, 
                            [window, page, rom, emulator, type, refresh,
                             preservedTokens, state, result]()
                            {
                                if (window->peekGui() != page)
                                    return;
                                const int cursor = page->getMenu().getCursorIndex();
                                gcmV3OpenPage(window, rom, emulator, type,
                                              refresh, preservedTokens, cursor);
                                page->close();
                                if (refresh)
                                    refresh();
                                if (state != "INSTALLED")
                                {
                                    std::string reason = gcmV35InstallField(result, "ERROR");
                                    std::string local = gcmV35InstallField(result, "LOCAL_STATE");
                                    if (reason.empty())
                                        reason = "Installation did not complete";
                                    std::string message = std::string(_("INSTALL FAILED"))
                                        + "\n\n" + reason;
                                    if (local == "READY")
                                        message += "\n\n" + std::string(_(
                                            "Verified local copy retained: READY"));
                                    window->pushGui(new GuiMsgBox(window, message, _("OK")));
                                }
                            }, page);
                        return true;
                    });
                page->addWithLabel(
                    row.label, installLabel, false,
                    [window, rom, emulator, row, installLabel]()
                    {
                        if (installLabel->busy())
                        {
                            window->pushGui(new GuiMsgBox(
                                window, _("INSTALLATION IN PROGRESS"), _("OK")));
                            return;
                        }
                        window->pushGui(new GuiMsgBox(
                            window,
                            std::string(_("VERIFIED - EXTERNAL")) + "\n\n" +
                                _("COPY VERIFIED UPDATE TO LOCAL STORAGE AND INSTALL?"),
                            _("YES"),
                            [window, rom, emulator, row, installLabel]()
                            {
                                const std::string output = gcmV35InstallRun(
                                    "start", rom, row.token, emulator);
                                const std::string state = gcmV35InstallField(output, "STATUS");
                                const std::string job = gcmV35InstallField(output, "JOB_ID");
                                if (output.find("ERROR\t") != std::string::npos ||
                                    state != "RUNNING" || job.empty())
                                {
                                    std::string error = output;
                                    for (char& c : error)
                                        if (c == '\t' || c == '\r') c = ' ';
                                    window->pushGui(new GuiMsgBox(
                                        window, std::string(_("INSTALL COULD NOT START"))
                                        + "\n\n" + error.substr(0, 1800), _("OK")));
                                    return;
                                }
                                installLabel->watch(job);
                            },
                            _("NO"), nullptr));
                    });
            }
            else
            {
                page->addWithLabel(
                    row.label,
                    gcmV3Value(window, _("SELECT")),
                    false,
                    [window, rom, emulator, row, page, refresh]
                    {
                        window->pushGui(new GuiMsgBox(
                            window,
                            _("ACTIVATE THIS UPDATE?"),
                            _("YES"),
                            [window, rom, emulator, row, page, refresh]
                            {
                                const std::string args =
                                    "--mode apply --type update --token " +
                                    gcmV3Quote(row.token) +
                                    " --confirm";

                                if (gcmV31Execute(
                                    window, rom, emulator, args))
                                {
                                    if (refresh)
                                        refresh();

                                    window->removeGui(page);
                                }
                            },
                            _("NO"), nullptr
                        ));
                    }
                );
            }
        }
        else
        {
            auto toggle =
                std::make_shared<GcmV31ConfirmSwitch>(window);

            auto enabled =
                std::make_shared<bool>(
                    row.state == "ENABLED"
                );

            toggle->setState(*enabled);
            dlcControls->push_back({toggle, enabled});

            page->addWithLabel(
                row.label,
                toggle,
                false,
                [window, rom, emulator, row,
                 toggle, enabled, refresh]
                {
                    // Do not change the displayed switch until
                    // the backend confirms the operation.
                    toggle->setState(*enabled);

                    const bool desired = !*enabled;

                    window->pushGui(new GuiMsgBox(
                        window,
                        desired
                            ? _("ENABLE THIS DLC?")
                            : _("DISABLE THIS DLC?"),
                        _("YES"),
                        [window, rom, emulator, row,
                         toggle, enabled, desired, refresh]
                        {
                            const std::string args =
                                "--mode toggle --type dlc --state " +
                                std::string(
                                    desired ? "enable" : "disable"
                                ) +
                                " --token " +
                                gcmV3Quote(row.token) +
                                " --confirm";

                            if (gcmV31Execute(
                                window, rom, emulator, args))
                            {
                                *enabled = desired;
                                toggle->setState(desired);

                                if (refresh)
                                    refresh();
                            }
                            else
                            {
                                toggle->setState(*enabled);
                            }
                        },
                        _("NO"),
                        [toggle, enabled]
                        {
                            toggle->setState(*enabled);
                        }
                    ));
                }
            );
        }
    }

    page->collectListRows(false);
    if (minimized)
    {
        unsigned total = 0;for (const auto& row : rows) if (row.section == "AVAILABLE") ++total;
        page->addWithLabel("", gcmV3Value(window, gcmV67Summary(_("TOTAL UPDATES: "), 0, total, false)));
    }
    else if (!available)
        page->addEntry(_("NO AVAILABLE PACKAGES"));


    if (type == "dlc" && !dlcControls->empty())
    {
        for (int i = 0; i < 2; ++i)
        {
            const bool enable = (i == 0);

            page->addEntry(
                enable
                    ? _("SELECT ALL / ENABLE DLC")
                    : _("DESELECT ALL / DISABLE DLC"),
                true,
                [window, rom, emulator, dlcControls,
                 refresh, enable]
                {
                    window->pushGui(new GuiMsgBox(
                        window,
                        enable
                            ? _("ENABLE ALL INSTALLED DLC?")
                            : _("DISABLE ALL INSTALLED DLC?"),
                        _("YES"),
                        [window, rom, emulator,
                         dlcControls, refresh, enable]
                        {
                            const std::string args =
                                std::string(
                                    "--mode bulk --type dlc --state "
                                ) +
                                (enable ? "enable" : "disable") +
                                " --confirm";

                            if (gcmV31Execute(
                                window, rom, emulator, args))
                            {
                                for (const auto& item : *dlcControls)
                                {
                                    *item.enabled = enable;
                                    item.toggle->setState(enable);
                                }

                                if (refresh)
                                    refresh();
                            }
                        },
                        _("NO"), nullptr
                    ));
                }
            );
        }
    }

    if (type == "update" && page->markAction("verify-unverified"))
        gcmV34AddRightAction(window, page, _("VERIFY UNVERIFIED UPDATES"), [window, page, rom, emulator, type, refresh]()
        {
            gcmV68OpenVerify(window, rom, emulator, type, [window, page, rom, emulator, type, refresh]()
            {
                gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                { gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh(); }, page);
            });
        });

    // 61SY: copy selected updates into the GCM content store (Switch), above Uninstall.
    if (type == "update" && (gcmV36IsSharedSwitch(emulator) || (emulator == "shadps4" && gcmV74Ps4External()[rom])) && page->markAction("copy"))
        gcmV34AddRightAction(window, page, _("COPY SELECTED TO GCM"), [window, page, rom, emulator, type, refresh]()
        {
            gcmV72OpenCopyPicker(window, rom, emulator, "update",
                [window, page, rom, emulator, type, refresh](const std::string& message) -> bool
                {
                    gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh, message]()
                    {
                        if (window->peekGui() != page) return;
                        gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();
                        if (refresh) refresh();
                        if (!message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                    }, page);
                    return true;
                });
        });

    if (type == "update" && page->markAction("uninstall"))
        gcmV34AddRightAction(window, page, _("UNINSTALL UPDATES"),
            [window, page, rom, emulator, type, refresh]()
            {
                gcmV62OpenUpdateRemoval(window, rom, emulator, [window, page, rom, emulator, type, refresh]()
                {
                    gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh]()
                    {
                        if (window->peekGui() != page) return;
                        gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();
                        if (refresh) refresh();
                    }, page);
                });
            });


    page->addGroup(_("DETECTED"));

    bool detected = false;

    auto detectedControls =
        std::make_shared<std::vector<
            std::shared_ptr<GcmV31ConfirmSwitch>>>();
    auto detectedRows = std::make_shared<std::vector<GcmV3Row>>();

    auto detectedCount = gcmV3Value(window, "0 SELECTED");
    auto detectedMaster =
        std::make_shared<GcmV31ConfirmSwitch>(window);
    detectedMaster->setState(false);


    page->collectListRows(true);
    for (const auto& row : rows)
    {
        if (row.section != "DETECTED")
            continue;

        detected = true;

        auto selected =
            std::make_shared<GcmV31ConfirmSwitch>(window);

        bool selectedBefore = false;

        for (const auto& token : preservedTokens)
            if (token == row.token)
                selectedBefore = true;

        selected->setState(selectedBefore);
        detectedControls->push_back(selected);
        detectedRows->push_back(row);

        page->addWithLabel(
            row.label,
            selected,
            false,
            [selected, detectedControls, detectedCount, detectedMaster, minimized]
            {
                selected->setState(!selected->getState());
                gcmV32UpdateSelectedCount(
                    detectedControls, detectedCount, minimized
                );
                bool all = !detectedControls->empty();

                for (const auto& item : *detectedControls)
                    if (!item->getState())
                        all = false;

                detectedMaster->setState(all);
            }
        );
    }

    page->collectListRows(false);
    gcmV32UpdateSelectedCount(
        detectedControls, detectedCount, minimized
    );

    bool allSelected = !detectedControls->empty();

    for (const auto& control : *detectedControls)
        if (!control->getState())
            allSelected = false;

    detectedMaster->setState(allSelected);

    if (!detected && !minimized)
    {
        page->addEntry(
            _("NO NEW PACKAGES IN CURRENT INVENTORY")
        );
    }
    else
    {
        page->addWithLabel("", detectedCount);

        gcmV34AddRightMaster(
            window, page, detectedMaster,
            [detectedControls, detectedCount, detectedMaster, minimized]()
            {
                const bool desired = !detectedMaster->getState();

                for (const auto& item : *detectedControls)
                    item->setState(desired);

                gcmV32UpdateSelectedCount(
                    detectedControls, detectedCount, minimized
                );

                detectedMaster->setState(desired);
            }
        );
    }

    if (gcmV61IsNativeFolder(emulator) || gcmV63IsExtraProvider(emulator) || gcmV61CanVerifyPackages(emulator, type))
    {
        // GCM v3.5: verify selections in the background, report errors once,
        // and refresh the verified external inventory after the batch completes.
        auto verificationLabel = std::make_shared<GcmV35BatchActionLabel>(
            window, rom, emulator);
        verificationLabel->setText(_("VERIFY UPDATES"));

        verificationLabel->setCompletionCallback(
            [window, page, rom, emulator, type, refresh,
             detectedControls, detectedRows](
                const std::string& status,
                const std::string& output) -> bool
            {
                // A dialog may be in front of this page; wait until it closes.

                if (status == "PASS")
                {
                    std::string tokens;
                    for (std::size_t i = 0; i < detectedRows->size() && i < detectedControls->size(); ++i)
                        if ((*detectedControls)[i]->getState()) { if (!tokens.empty()) tokens += ",";tokens += (*detectedRows)[i].token; }
                    gcmV65DeleteRun(rom, emulator, type, "verified", tokens);
                }
                const std::string failed =
                    status == "PASS" ? "" : gcmV35FailureMessage(output);
                gcmV66QueueRefresh(window, 
                    [window, page, rom, emulator, type, refresh,
                     detectedControls, detectedRows, failed]()
                    {
                        if (window->peekGui() != page)
                            return;

                        std::vector<std::string> selected;
                        for (std::size_t i = 0;
                             i < detectedRows->size() &&
                             i < detectedControls->size(); ++i)
                        {
                            if ((*detectedControls)[i]->getState())
                                selected.push_back((*detectedRows)[i].token);
                        }
                        const int cursor = page->getMenu().getCursorIndex();
                        gcmV3OpenPage(window, rom, emulator, type,
                                      refresh, selected, cursor);
                        page->close();
                        if (refresh)
                            refresh();
                        if (!failed.empty())
                            window->pushGui(new GuiMsgBox(window, failed, _("OK")));
                    }, page);
                return true;
            });

        ComponentListRow verifyAction;
        verifyAction.addElement(gcmV3Value(window, ""), true);
        verifyAction.addElement(verificationLabel, false);
        auto verifySelected = [window, page, rom, emulator, type, refresh, detectedControls, detectedRows,
             verificationLabel]()
            {
                if (verificationLabel->busy())
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("VERIFICATION IN PROGRESS"), _("OK")));
                    return;
                }
                if (detectedControls->size() != detectedRows->size())
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("UPDATE SELECTION MISMATCH"), _("OK")));
                    return;
                }
                std::string selectedTokens;
                int count = 0;
                for (std::size_t i = 0; i < detectedRows->size(); ++i)
                {
                    if (!(*detectedControls)[i]->getState())
                        continue;
                    if (!selectedTokens.empty())
                        selectedTokens += ",";
                    selectedTokens += (*detectedRows)[i].token;
                    ++count;
                }
                if (count == 0 || count > 512)
                {
                    window->pushGui(new GuiMsgBox(
                        window, _("SELECT BETWEEN 1 AND 512 DETECTED UPDATES"),
                        _("OK")));
                    return;
                }

                if (count > 32)
                {
                    gcmV69OpenDetectedBatch(window, rom, emulator, type, selectedTokens,
                        [window, page, rom, emulator, type, refresh](const std::string& message) -> bool
                        {
                            gcmV66QueueRefresh(window, [window, page, rom, emulator, type, refresh, message]()
                            {
                                gcmV3OpenPage(window, rom, emulator, type, refresh);page->close();if (refresh) refresh();
                                if (!message.empty()) window->pushGui(new GuiMsgBox(window, message, _("OK")));
                            }, page);
                            return true;
                        });
                    return;
                }
                const std::string result = gcmV35BatchRun(
                    rom, emulator, "start", selectedTokens);
                const std::string job = gcmV35BatchField(result, "ID");
                const std::string status = gcmV35BatchField(result, "STATUS");
                if (gcmV61CommandFailed(result) ||
                    job.empty() || status != "RUNNING")
                {
                    std::string report = result;
                    for (char& c : report)
                        if (c == '\t' || c == '\r') c = ' ';
                    window->pushGui(new GuiMsgBox(
                        window, _("COULD NOT START VERIFICATION") +
                        std::string("\n\n") + report.substr(0, 2200), _("OK")));
                    return;
                }
                verificationLabel->watch(job);
            };
        verifyAction.makeAcceptInputHandler(verifySelected);
        page->markAction("verify-detected");
        page->addRow(verifyAction);
    }
    else
        page->addWithLabel("", gcmV3Value(window, _("VERIFICATION UNAVAILABLE")));



    page->addGroup(_("CONTENT MANAGEMENT"));

    


    const std::function<bool()> refreshAfterScan =
        [window, page, rom, emulator, type, refresh,
         detectedControls, detectedRows]() -> bool
        {

            gcmV66QueueRefresh(window, 
                [window, page, rom, emulator, type, refresh,
                 detectedControls, detectedRows]()
                {
                    if (window->peekGui() != page)
                        return;

                    std::vector<std::string> selected;

                    for (std::size_t i = 0;
                         i < detectedRows->size() &&
                         i < detectedControls->size(); ++i)
                    {
                        if ((*detectedControls)[i]->getState())
                            selected.push_back(
                                (*detectedRows)[i].token
                            );
                    }

                    const int cursor =
                        page->getMenu().getCursorIndex();

                    gcmV3OpenPage(
                        window, rom, emulator, type,
                        refresh, selected, cursor
                    );

                    page->close();

                    if (refresh)
                        refresh();
                },
                page
            );

            return true;
        };

    if (gcmV61IsNativeFolder(emulator) || gcmV63IsExtraProvider(emulator))
        gcmV61AddNativeScanActions(window, page, rom, emulator, type, [refreshAfterScan](const std::string&) { return refreshAfterScan(); });
    else
    gcmV34AddScanActions(
        window, page, rom, emulator, refreshAfterScan
    );




    auto minimizeToggle = std::make_shared<GcmV31ConfirmSwitch>(window);
    minimizeToggle->setState(minimized);
    ComponentListRow minimizeRow;
    minimizeRow.addElement(gcmV3Value(window, ""), true);
    minimizeRow.addElement(gcmV3Value(window, _("MINIMIZE LISTS")), false);
    minimizeRow.addElement(minimizeToggle, false);
    minimizeRow.makeAcceptInputHandler([listScope, minimizeToggle, refreshAfterScan]()
    {
        const bool desired = !minimizeToggle->getState();
        minimizeToggle->setState(desired);gcmV67MinimizedLists()[listScope] = desired;
        gcmV68FocusMinimize()[listScope] = true;
        refreshAfterScan();
    });
    const int minimizeCursor = page->getMenu().size();
    page->addRow(minimizeRow);

    // Restore controller position as closely as possible.
    // 61SY: after an action finishes, return to that action rather than the old row number.
    const int actionTarget = previousAction.empty() ? -1 : page->actionRow(previousAction);
    if ((actionTarget >= 0 || preservedCursor >= 0 || gcmV68FocusMinimize()[listScope]) && page->getMenu().size() > 0)
    {
        int target = gcmV68FocusMinimize()[listScope] ? minimizeCursor : actionTarget >= 0 ? actionTarget : preservedCursor;
        gcmV68FocusMinimize()[listScope] = false;
        if (target >= page->getMenu().size())
            target = page->getMenu().size() - 1;

        const int current = page->getMenu().getCursorIndex();
        if (target != current)
            page->getMenu().getList()->moveCursor(
                target - current
            );
    }

    window->pushGui(page);
}
