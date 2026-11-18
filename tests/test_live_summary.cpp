// Behaviour test for the live UPDATES/DLC value (GcmV78LiveSummaryValue), with stand-ins for
// the EmulationStation classes it uses.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
struct Transform4x4f {};
struct Window {};
struct Font {}; struct TextStyle { std::shared_ptr<Font> font; unsigned color = 0; };
struct MenuTheme { TextStyle Text; };
struct ThemeData { static std::shared_ptr<MenuTheme> getMenuTheme() { static auto t = std::make_shared<MenuTheme>(); return t; } };
class TextComponent {
public:
    TextComponent(Window*, const std::string& t, std::shared_ptr<Font>, unsigned) : mText(t) {}
    virtual ~TextComponent() {}
    virtual void update(int) {}
    virtual void render(const Transform4x4f&) {}
    void setText(const std::string& t) { mText = t; ++sets; }
    virtual std::string getValue() const { return mText; }
    std::string mText; int sets = 0;
};
struct GcmV3Summary { std::string update = "UNKNOWN"; std::string dlc = "UNKNOWN"; };
static void gcmV75Log(const std::string&, const std::string&) {}
static std::atomic<int> reads{0};
static std::atomic<int> finishAfter{3};
static GcmV3Summary gcmV3ReadSummary(const std::string&, const std::string&) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    GcmV3Summary s; int n = ++reads;
    if (n == 1) return s;                       // a failed read (UNKNOWN) keeps waiting
    if (n < finishAfter) { s.update = "ASSESSING"; s.dlc = "ASSESSING"; return s; }
    s.update = "01.05 ACTIVE"; s.dlc = "12 ENABLED"; return s;
}
#include "live_summary.inc"  // extracted from GuiGcmNative.h by run_tests.sh
int fails = 0;
void check(const char* n, bool c) { std::printf("%s %s\n", c ? "PASS" : "FAIL", n); if (!c) ++fails; }
void run(std::shared_ptr<TextComponent> dlc, int ms) {
    auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
    while (std::chrono::steady_clock::now() < end) { dlc->update(16); dlc->render(Transform4x4f{}); std::this_thread::sleep_for(std::chrono::milliseconds(16)); }
}
int main() {
    Window w;
    auto up = std::make_shared<TextComponent>(&w, "ASSESSING", nullptr, 0);
    auto dlc = gcmV78LiveSummary(&w, "rom", "shadps4", "ASSESSING", up);
    run(dlc, 300);
    check("no read in the first 2 seconds", reads == 0 && dlc->getValue() == "ASSESSING");
    run(dlc, 2300);
    check("first read after 2 s; a failed read keeps ASSESSING", reads == 1 && dlc->getValue() == "ASSESSING" && up->getValue() == "ASSESSING");
    run(dlc, 4400);
    check("result shown when the check finishes", up->getValue() == "01.05 ACTIVE" && dlc->getValue() == "12 ENABLED");
    int after = reads; run(dlc, 4500);
    check("polling stops once nothing says ASSESSING", reads == after);
    // only the update value waiting still triggers polling
    reads = 1; finishAfter = 2;
    auto up2 = std::make_shared<TextComponent>(&w, "ASSESSING", nullptr, 0);
    auto dlc2 = gcmV78LiveSummary(&w, "rom", "shadps4", "3 ENABLED", up2);
    run(dlc2, 2400);
    check("an ASSESSING update value alone also refreshes", up2->getValue() == "01.05 ACTIVE");
    // a value that is not waiting never polls
    int before = reads; auto still = gcmV78LiveSummary(&w, "rom", "shadps4", "3 ENABLED", std::make_shared<TextComponent>(&w, "1.0 (BASE)", nullptr, 0));
    run(still, 2500);
    check("finished values never start a read", reads == before);
    // closing the menu mid-read is safe
    auto up3 = std::make_shared<TextComponent>(&w, "ASSESSING", nullptr, 0);
    { auto dlc3 = gcmV78LiveSummary(&w, "rom", "shadps4", "ASSESSING", up3); run(dlc3, 2050); }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    check("closing the menu during a read is safe", true);
    std::printf(fails ? "FAILED\n" : "ALL LIVE SUMMARY FIXTURES PASS\n");
    return fails;
}
