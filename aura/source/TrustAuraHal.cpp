// TrustAuraHal - ASUS Aura (Armoury Crate / LightingService) HAL plugin for
//   Trust GXT 868 Torix keyboard (USB-C cable, ZXW LED channel).
// Interface per ASUS AacHalSample (github.com/andy15531316/AacHalSample): in-proc COM,
// IAacLedDeviceHal::Enumerate(IAacLedDevice**, ULONG*) two-call pattern, capability XML.
// Protocol: the keyboard's own per-key frame command (reverse-engineered).
#include <windows.h>
#include <shlobj.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <string>
#include <vector>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <chrono>
#include <algorithm>
#include "torix_layout.h"
#pragma comment(lib, "setupapi.lib")
#pragma comment(lib, "hid.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")

// {28E19014-8549-4CFC-9DEA-6087F445C737}
static const CLSID CLSID_TrustAuraHal = {0x28e19014, 0x8549, 0x4cfc, {0x9d, 0xea, 0x60, 0x87, 0xf4, 0x45, 0xc7, 0x37}};
static const IID IID_IAacLedDeviceHal = {0xF2C8D5B4, 0x3854, 0x4325, {0x8A, 0x4F, 0xFD, 0x7C, 0x50, 0x72, 0xE3, 0xB9}};
static const IID IID_IAacLedDevice    = {0x61711778, 0xAB59, 0x4026, {0x89, 0xE8, 0x7A, 0x63, 0x42, 0x2C, 0x29, 0xC2}};
static const IID IID_IAacLedDeviceOpt = {0x68f0c6e1, 0x7469, 0x40b3, {0x84, 0xd5, 0xe0, 0x79, 0x3f, 0x44, 0x9e, 0x4d}};

struct IAacLedDevice : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetCapability(BSTR* capability) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetEffect(ULONG effectId, ULONG* colors, ULONG numberOfColors) = 0;
    virtual HRESULT STDMETHODCALLTYPE Synchronize(ULONG effectId, ULONGLONG milliseconds) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetEffectOptSpeed(ULONG effectId, ULONG* colors, ULONG n, ULONG speed, ULONG direction) = 0;
};
struct IAacLedDeviceHal : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE Enumerate(IAacLedDevice** devices, ULONG* count) = 0;
};

static HMODULE g_module;

// Data folder for the log and settings: <ProgramData>\TrustRGB, looked up from Windows (never assumed to be on C:).
static std::wstring DataDir() {
    static std::wstring dir = [] {
        std::wstring base = _wgetenv(L"ProgramData") ? _wgetenv(L"ProgramData") : L".";     // only if the Windows lookup below fails
        PWSTR p = nullptr;
        if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_ProgramData, 0, nullptr, &p)) && p) base = p;
        if (p) CoTaskMemFree(p);
        return base + L"\\TrustRGB";
    }();
    return dir;
}

static void Log(const char* fmt, ...) {
    static std::mutex m; std::lock_guard<std::mutex> l(m);
    CreateDirectoryW(DataDir().c_str(), nullptr);
    FILE* f = _wfopen((DataDir() + L"\\hal.log").c_str(), L"a");
    if (!f) return;
    SYSTEMTIME t; GetLocalTime(&t);
    fprintf(f, "%02d:%02d:%02d.%03d [%u] ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds, GetCurrentProcessId());
    va_list a; va_start(a, fmt); vfprintf(f, fmt, a); va_end(a);
    fputc('\n', f); fclose(f);
}

// Sub-millisecond sleep: plain Sleep() rounds up to the 15.6 ms system tick.
static void SleepUs(LONGLONG us) {
    if (us <= 0) return;
    static thread_local HANDLE t = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    LARGE_INTEGER due; due.QuadPart = -us * 10;
    SetWaitableTimer(t, &due, 0, nullptr, nullptr, FALSE);
    WaitForSingleObject(t, INFINITE);
}

// Torix packet gaps in microseconds, tunable live via <ProgramData>\TrustRGB\torix_gap_us.txt as
// "<between DD packets> <around 55 01 / 55 02>" (re-read once a second). 0 between DD packets glitched;
// 4 ms everywhere, 2/8 and 4/8 all glitched occasionally; 8 ms everywhere is clean (2026-09-22).
struct TorixGaps { LONGLONG dd = 2000, edge = 2000; };   // us between packets: 2000 is clean with the patched firmware; stock needs 8000+ (<ProgramData>\TrustRGB\torix_gap_us.txt overrides)
static TorixGaps GetTorixGaps() {
    static TorixGaps g; static ULONGLONG next = 0;
    if (GetTickCount64() >= next) {
        next = GetTickCount64() + 1000;
        if (FILE* f = _wfopen((DataDir() + L"\\torix_gap_us.txt").c_str(), L"r")) {
            long long a, b; int n = fscanf(f, "%lld %lld", &a, &b);
            TorixGaps v = g;
            if (n >= 1 && a >= 0) v.dd = v.edge = a;
            if (n == 2 && b >= 0) v.edge = b;
            if (v.dd != g.dd || v.edge != g.edge) { g = v; Log("torix gaps -> dd %lld us, edge %lld us", g.dd, g.edge); }
            fclose(f);
        }
    }
    return g;
}

// Find a HID collection by VID/PID, interface ("mi_xx" in path) and top-level usage.
static std::wstring FindHid(USHORT vid, USHORT pid, const wchar_t* mi, USHORT page, USHORT usage) {
    GUID hid; HidD_GetHidGuid(&hid);
    HDEVINFO set = SetupDiGetClassDevsW(&hid, nullptr, nullptr, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    std::wstring found;
    SP_DEVICE_INTERFACE_DATA ifd{sizeof(ifd)};
    for (DWORD i = 0; found.empty() && SetupDiEnumDeviceInterfaces(set, nullptr, &hid, i, &ifd); i++) {
        BYTE buf[1024]; auto det = (SP_DEVICE_INTERFACE_DETAIL_DATA_W*)buf; det->cbSize = sizeof(*det);
        if (!SetupDiGetDeviceInterfaceDetailW(set, &ifd, det, sizeof(buf), nullptr, nullptr)) continue;
        std::wstring path = det->DevicePath; for (auto& c : path) c = towlower(c);
        wchar_t id[32]; swprintf(id, 32, L"vid_%04x&pid_%04x", vid, pid);
        if (path.find(id) == std::wstring::npos || path.find(mi) == std::wstring::npos) continue;
        HANDLE h = CreateFileW(det->DevicePath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) continue;
        PHIDP_PREPARSED_DATA pp; HIDP_CAPS caps{};
        if (HidD_GetPreparsedData(h, &pp)) { HidP_GetCaps(pp, &caps); HidD_FreePreparsedData(pp); }
        CloseHandle(h);
        if (caps.UsagePage == page && caps.Usage == usage) found = det->DevicePath;
    }
    SetupDiDestroyDeviceInfoList(set);
    return found;
}

struct Rgb { BYTE r, g, b; bool operator==(const Rgb& o) const { return r == o.r && g == o.g && b == o.b; } };

// One Aura device. SetEffect only stores the latest frame; a worker thread writes it to
// the hardware, dropping stale frames, so LightingService never blocks on USB.
class Device : public IAacLedDevice {
public:
    // width x height grid, LEDs row-major. keepaliveMs re-sends the last frame that often.
    Device(const char* name, ULONG type, std::vector<const char*> leds, size_t width, DWORD minIntervalMs, DWORD keepaliveMs = INFINITE)
        : name_(name), type_(type), leds_(leds), width_(width), interval_(minIntervalMs), keepalive_(keepaliveMs), frame_(leds.size()) {
        std::thread([this] { Worker(); }).detach();
    }
    virtual bool Present() = 0;

    // Devices live for the process lifetime (DllCanUnloadNow always refuses), so no delete.
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IAacLedDevice || riid == IID_IAacLedDeviceOpt) { *ppv = this; return S_OK; }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }

    HRESULT STDMETHODCALLTYPE GetCapability(BSTR* cap) override {
        std::string x = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\" ?>\n<root><version>1</version><type>"
            + std::to_string(type_) + "</type><device><name>" + name_ + "</name><id>0</id><layout><led_count>"
            + std::to_string(leds_.size()) + "</led_count><size><width>" + std::to_string(width_)
            + "</width><height>" + std::to_string(leds_.size() / width_) + "</height></size><led_name>";
        for (auto l : leds_) x += std::string("<led>") + l + "</led>";
        x += "</led_name></layout><supported_effect>";
        for (auto e : {std::make_pair("Manual", 0), std::make_pair("Static", 1)})
            x += std::string("<effect><name>") + e.first + "</name><id>" + std::to_string(e.second)
               + "</id><synchronizable>0</synchronizable><customized_color>1</customized_color>"
                 "<speed_supported>0</speed_supported><direction_supported>0</direction_supported></effect>";
        x += "</supported_effect></device></root>";
        *cap = SysAllocString(std::wstring(x.begin(), x.end()).c_str());
        Log("%s GetCapability", name_);
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE SetEffect(ULONG effectId, ULONG* colors, ULONG n) override {
        if (calls_++ < 20 || calls_ % 1000 == 0)
            Log("%s SetEffect #%lu effect=%lu n=%lu c0=%08lx", name_, calls_, effectId, n, n ? colors[0] : 0);
        if (!colors || !n) return E_INVALIDARG;
        if (effectId > 1) return S_OK;   // only Manual/Static are advertised; 0xFF arrives with junk colours
        std::lock_guard<std::mutex> l(m_);
        for (size_t i = 0; i < frame_.size(); i++) {
            ULONG c = colors[i < n ? i : n - 1];   // 0x00BBGGRR
            frame_[i] = {BYTE(c), BYTE(c >> 8), BYTE(c >> 16)};
        }
        dirty_ = true; cv_.notify_one();
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetEffectOptSpeed(ULONG effectId, ULONG* colors, ULONG n, ULONG, ULONG) override {
        return SetEffect(effectId, colors, n);
    }
    HRESULT STDMETHODCALLTYPE Synchronize(ULONG, ULONGLONG) override { return S_OK; }

protected:
    virtual bool Write(const std::vector<Rgb>& frame) = 0;
    HANDLE h_ = INVALID_HANDLE_VALUE;

    bool Open(const std::wstring& path, DWORD access) {
        if (h_ != INVALID_HANDLE_VALUE) return true;
        if (path.empty()) return false;
        h_ = CreateFileW(path.c_str(), access, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
        return h_ != INVALID_HANDLE_VALUE;
    }
    void Close() { if (h_ != INVALID_HANDLE_VALUE) CloseHandle(h_); h_ = INVALID_HANDLE_VALUE; }

private:
    void Worker() {
        std::vector<Rgb> last;
        ULONGLONG lastFresh = 0;
        for (;;) {
            std::vector<Rgb> f;
            bool fresh;
            { std::unique_lock<std::mutex> l(m_);
              fresh = cv_.wait_for(l, std::chrono::milliseconds(keepalive_), [this] { return dirty_; });
              dirty_ = false; f = frame_; }
            if (fresh) lastFresh = GetTickCount64();
            // Keepalive only while Aura is driving us: stop 2 s after its last frame (device unticked,
            // service idle) so the device falls back to its own effect instead of freezing.
            if (fresh ? f == last : last.empty() || GetTickCount64() - lastFresh > 2000) continue;
            // Test hook: <ProgramData>\TrustRGB\pause present -> leave the hardware alone.
            if (GetFileAttributesW((DataDir() + L"\\pause").c_str()) != INVALID_FILE_ATTRIBUTES) { last.clear(); continue; }
            if (Write(f)) {
                last = f;
                if (++frames_ % 200 == 0) { ULONGLONG now = GetTickCount64(); Log("%s %.1f fps", name_, 200000.0 / (now - t0_)); t0_ = now; }
            } else {
                // Device absent (e.g. plugged in after boot) or write error: drop the handle and retry
                // slowly; log once per outage so a missing keyboard doesn't flood the log.
                if (!absent_) Log("%s write failed (%lu), retrying every 1 s", name_, GetLastError());
                absent_ = true; Close(); Sleep(1000); continue;
            }
            if (absent_) { absent_ = false; Log("%s back", name_); }
            Sleep(interval_);
        }
    }
    const char* name_; ULONG type_; std::vector<const char*> leds_; size_t width_; DWORD interval_, keepalive_;
    std::mutex m_; std::condition_variable cv_; bool dirty_ = false, absent_ = false; std::vector<Rgb> frame_; ULONG calls_ = 0, frames_ = 0; ULONGLONG t0_ = GetTickCount64();
};

// Trust GXT 868 Torix (flashed ZA68 firmware, v10+ - the gate patch lets frames in without a Caps Lock tap): per-key frame: 55 01, 7x 55 DD, 55 02.
// 384-byte buffer, RGB per key_index; grid cell (row, col) -> key_index = col*6 + row. The keyboard
// falls back to its saved effect when frames stop, hence the 150 ms keepalive.
class Torix : public Device {
public:
    Torix() : Torix("Trust GXT 868 Torix", {std::begin(TORIX_KEY_NAME), std::end(TORIX_KEY_NAME)}, TORIX_COLS, 150) {}
    bool Present() override { return !Path().empty(); }
protected:
    Torix(const char* name, std::vector<const char*> leds, size_t width, DWORD keepalive)
        : Device(name, 0x80000 /*KEYBOARD_RGB_LIGHTING*/, leds, width, 0, keepalive) {}
    static std::wstring Path() { return FindHid(0x145F, 0x0339, L"mi_02", 0x0001, 0x0000); }
    bool Send(std::vector<BYTE> body, LONGLONG gapUs) {
        body.insert(body.begin(), 0x00); body.resize(65); DWORD w;
        bool ok = WriteFile(h_, body.data(), 65, &w, nullptr) && w == 65;
        SleepUs(gapUs);   // back-to-back packets glitch keys; vendor app uses ~16 ms
        return ok;
    }
private:
    // DD packets need no 55 01 / 55 02 on this firmware, and each one reloads the ~1 s hold. Only the 56-byte
    // chunks that changed are sent. A full frame goes out after a pause (hold expired -> firmware re-inits)
    // and once a second (heals a lost packet); with nothing changed one chunk still goes out as keepalive.
    bool Write(const std::vector<Rgb>& f) override {
        if (!Open(Path(), GENERIC_WRITE)) return false;
        TorixGaps g = GetTorixGaps();
        BYTE buf[384] = {};
        for (size_t i = 0; i < f.size(); i++) {
            size_t idx = (i % TORIX_COLS) * TORIX_ROWS + i / TORIX_COLS;
            if (idx < 128) { buf[idx * 3] = f[i].r; buf[idx * 3 + 1] = f[i].g; buf[idx * 3 + 2] = f[i].b; }
        }
        ULONGLONG now = GetTickCount64();
        bool full = now - sendT_ > 400 || now - fullT_ > 1000;
        int chunks[7], n = 0;
        for (int c = 0; c < 7; c++) if (full || memcmp(buf + c * 56, sent_ + c * 56, min(56, 384 - c * 56))) chunks[n++] = c;
        if (!n) chunks[n++] = 0;
        for (int k = 0; k < n; k++) {
            int off = chunks[k] * 56; BYTE len = BYTE(min(56, 384 - off));
            std::vector<BYTE> p = {0x55, 0xDD, 0x00, 0x00, len, BYTE(off), BYTE(off >> 8), 0x00};
            p.insert(p.end(), buf + off, buf + off + len); p.resize(64);
            BYTE chk = 0; for (size_t j = 4; j < 64; j++) chk += p[j];   // sum of report bytes 5..64
            p[3] = chk;
            if (!Send(p, g.dd)) { sendT_ = 0; return false; }
            memcpy(sent_ + off, buf + off, len);
        }
        sendT_ = now; if (full) fullT_ = now;
        return true;
    }
    BYTE sent_[384] = {}; ULONGLONG sendT_ = 0, fullT_ = 0;
};

class Hal : public IAacLedDeviceHal {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IAacLedDeviceHal) { *ppv = this; return S_OK; }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE Enumerate(IAacLedDevice** out, ULONG* count) override {
        static Torix torix;
        // Always export the device: LightingService enumerates once, ~10 s after boot, before the keyboard's
        // wired interface is up. Write() opens lazily and retries, so a late device just joins.
        std::vector<Device*> present = {(Device*)&torix};
        ULONG room = *count; *count = ULONG(present.size());
        Log("Enumerate out=%p room=%lu torix=%d", out, room, torix.Present());
        if (!out) return S_OK;
        if (room && room < present.size()) return E_BOUNDS;
        for (size_t i = 0; i < present.size(); i++) out[i] = present[i];
        return S_OK;
    }
};

class Factory : public IClassFactory {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_IClassFactory) { *ppv = this; return S_OK; }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        static Hal hal;
        Log("CreateInstance");
        return outer ? CLASS_E_NOAGGREGATION : hal.QueryInterface(riid, ppv);
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL) override { return S_OK; }
};

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) { g_module = h; DisableThreadLibraryCalls(h); }
    return TRUE;
}
STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    static Factory factory;
    if (clsid != CLSID_TrustAuraHal) return CLASS_E_CLASSNOTAVAILABLE;
    return factory.QueryInterface(riid, ppv);
}
STDAPI DllCanUnloadNow() { return S_FALSE; }   // worker threads run for the process lifetime
