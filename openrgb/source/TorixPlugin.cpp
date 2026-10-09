#include "TorixPlugin.h"
#include <windows.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <cstdarg>
#include <cstdio>
#include <QLabel>
#include "LogManager.h"
#include "torix_keys.h"

#pragma comment(lib, "setupapi.lib")
#pragma comment(lib, "hid.lib")

static const unsigned int NA = 0xFFFFFFFF;
static const int KEY_COUNT = sizeof(TORIX_KEYS) / sizeof(TORIX_KEYS[0]);
static const LONGLONG GAP_US = 2000;       // between per-key packets: clean with the patched firmware (stock needs 8000+)

// Sub-millisecond sleep: plain Sleep() rounds up to the 15.6 ms system tick.
static void SleepUs(LONGLONG us)
{
    static thread_local HANDLE t = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    LARGE_INTEGER due; due.QuadPart = -us * 10;
    SetWaitableTimer(t, &due, 0, nullptr, nullptr, FALSE);
    WaitForSingleObject(t, INFINITE);
}

// Find the vendor channel (USB 145F:0339, interface 2, usage page 1 / usage 0) of a connected keyboard.
static std::wstring FindKeyboard()
{
    GUID hid; HidD_GetHidGuid(&hid);
    HDEVINFO set = SetupDiGetClassDevsW(&hid, nullptr, nullptr, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    std::wstring found;
    SP_DEVICE_INTERFACE_DATA ifd{sizeof(ifd)};
    for(DWORD i = 0; found.empty() && SetupDiEnumDeviceInterfaces(set, nullptr, &hid, i, &ifd); i++)
    {
        BYTE buf[1024]; auto det = (SP_DEVICE_INTERFACE_DETAIL_DATA_W*)buf; det->cbSize = sizeof(*det);
        if(!SetupDiGetDeviceInterfaceDetailW(set, &ifd, det, sizeof(buf), nullptr, nullptr)) continue;
        std::wstring path = det->DevicePath; for(auto& c : path) c = towlower(c);
        if(path.find(L"vid_145f&pid_0339") == std::wstring::npos || path.find(L"mi_02") == std::wstring::npos) continue;
        HANDLE h = CreateFileW(det->DevicePath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
        if(h == INVALID_HANDLE_VALUE) continue;
        PHIDP_PREPARSED_DATA pp; HIDP_CAPS caps{};
        if(HidD_GetPreparsedData(h, &pp)) { HidP_GetCaps(pp, &caps); HidD_FreePreparsedData(pp); }
        CloseHandle(h);
        if(caps.UsagePage == 0x0001 && caps.Usage == 0x0000) found = det->DevicePath;
    }
    SetupDiDestroyDeviceInfoList(set);
    return found;
}

void TorixPlugin::Log(unsigned int level, const char* fmt, ...)
{
    char text[256]; va_list a; va_start(a, fmt); vsnprintf(text, sizeof(text), fmt, a); va_end(a);
    if(api) api->LogEntry(__FILE__, __LINE__, level, "[Trust Torix] %s", text);
}

OpenRGBPluginInfo TorixPlugin::GetPluginInfo()
{
    OpenRGBPluginInfo info;
    info.Name           = "Trust GXT 868 Torix";
    info.Description    = "Per-key lighting for the Trust GXT 868 Torix keyboard with the TorixPatch firmware (USB cable)";
    info.Version        = "1.0";
    info.Commit         = "";
    info.URL            = "";
    info.Location       = OPENRGB_PLUGIN_LOCATION_INFORMATION;
    info.Label          = "Trust Torix";
    info.TabIconString  = "";
    info.ProtocolVersion = 0;
    return info;
}

QWidget* TorixPlugin::GetWidget()
{
    QLabel* label = new QLabel("Trust GXT 868 Torix: shows up as a device once the keyboard is connected by USB cable.\n"
                               "Needs the patched firmware (TorixPatch). Lighting is released when OpenRGB closes.");
    label->setAlignment(Qt::AlignCenter);
    return label;
}

void TorixPlugin::Load(OpenRGBPluginAPIInterface* plugin_api_ptr)
{
    api = plugin_api_ptr;
    frame.assign(384, 0);
    stop = false;
    worker = std::thread(&TorixPlugin::Run, this);
}

void TorixPlugin::Unload()
{
    stop = true; cv.notify_all();
    if(worker.joinable()) worker.join();
    if(dev) { CloseHandle((HANDLE)dev); dev = nullptr; }
    if(controller && api)
    {
        api->UnregisterVirtualRGBController(controller);
        api->DeleteVirtualRGBController(controller);
        controller = nullptr;
    }
    api = nullptr;
}

void TorixPlugin::Register()
{
    RGBController_Setup setup = {};
    setup.description   = "Trust GXT 868 Torix keyboard";
    setup.location      = "USB";
    setup.name          = "Trust GXT 868 Torix";
    setup.vendor        = "Trust";
    setup.flags         = CONTROLLER_FLAG_VIRTUAL;
    setup.type          = DEVICE_TYPE_KEYBOARD;
    setup.active_mode   = 0;

    mode direct;
    direct.name         = "Direct";
    direct.value        = 0;
    direct.flags        = MODE_FLAG_HAS_PER_LED_COLOR;
    direct.color_mode   = MODE_COLORS_PER_LED;
    setup.modes.push_back(direct);

    zone keys;
    keys.name           = "Keyboard";
    keys.type           = ZONE_TYPE_MATRIX;
    keys.leds_count     = keys.leds_min = keys.leds_max = KEY_COUNT;
    unsigned int map[TORIX_ROWS * TORIX_COLS];
    for(unsigned int& cell : map) cell = NA;
    for(int i = 0; i < KEY_COUNT; i++)
    {
        led l; l.name = std::string("Key: ") + TORIX_KEYS[i].name; l.value = TORIX_KEYS[i].code;
        setup.leds.push_back(l);
        map[(TORIX_KEYS[i].code % TORIX_ROWS) * TORIX_COLS + TORIX_KEYS[i].code / TORIX_ROWS] = i;
    }
    keys.matrix_map.Set(TORIX_ROWS, TORIX_COLS, map);
    setup.zones.push_back(keys);

    setup.object_ptr            = this;
    setup.DeviceUpdateLEDs      = &TorixPlugin::UpdateLEDsCallback;
    setup.DeviceUpdateZoneLEDs  = [](void* self, int) { UpdateLEDsCallback(self); };
    setup.DeviceUpdateSingleLED = [](void* self, int) { UpdateLEDsCallback(self); };

    controller = api->CreateVirtualRGBController(&setup);
    api->RegisterVirtualRGBControllerInThread(controller);
    Log(LL_INFO, "keyboard found, device registered");
}

void TorixPlugin::Unregister()
{
    if(!controller) return;
    api->UnregisterVirtualRGBControllerInThread(controller);
    api->DeleteVirtualRGBController(controller);
    controller = nullptr;
    Log(LL_INFO, "keyboard gone, device removed");
}

void TorixPlugin::UpdateLEDsCallback(void* self) { ((TorixPlugin*)self)->OnColours(); }

void TorixPlugin::OnColours()
{
    RGBControllerInterface* c = controller;
    if(!c) return;
    RGBColor* colors = c->GetColorsPointer();
    if(!colors) return;
    std::lock_guard<std::mutex> l(m);
    for(int i = 0; i < KEY_COUNT; i++)
    {
        unsigned char* px = &frame[TORIX_KEYS[i].code * 3];
        px[0] = RGBGetRValue(colors[i]); px[1] = RGBGetGValue(colors[i]); px[2] = RGBGetBValue(colors[i]);
    }
    dirty = haveFrame = true;
    cv.notify_one();
}

// Per-key frame: DD packets (55 DD, checksum, length, offset, up to 56 bytes of the 384-byte frame). Each one reloads the keyboard's ~1 s
// hold. Only changed chunks are sent; a full frame goes out after a pause (hold expired -> firmware re-inits) and once a second
// (heals a lost packet); with nothing changed one chunk still goes out as keepalive.
bool TorixPlugin::Write(const std::vector<unsigned char>& buf, bool&)
{
    ULONGLONG now = GetTickCount64();
    bool full = now - sendT > 400 || now - fullT > 1000;
    int chunks[7], n = 0;
    for(int c = 0; c < 7; c++) if(full || memcmp(&buf[c * 56], sent + c * 56, 56)) chunks[n++] = c;
    if(!n) chunks[n++] = 0;
    for(int k = 0; k < n; k++)
    {
        int off = chunks[k] * 56; BYTE len = 56;
        BYTE p[65] = {0x00, 0x55, 0xDD, 0x00, 0x00, len, BYTE(off), BYTE(off >> 8), 0x00};
        memcpy(p + 9, &buf[off], len);
        BYTE chk = 0; for(int j = 5; j < 65; j++) chk += p[j];        // sum of report bytes 5..64
        p[4] = chk;
        DWORD w;
        if(!WriteFile((HANDLE)dev, p, 65, &w, nullptr) || w != 65) { sendT = 0; return false; }
        SleepUs(GAP_US);
        memcpy(sent + off, &buf[off], len);
    }
    sendT = now; if(full) fullT = now;
    return true;
}

void TorixPlugin::Run()
{
    while(!stop)
    {
        std::wstring path = dev ? std::wstring() : FindKeyboard();
        if(!dev && path.empty())
        {
            if(controller) Unregister();
            std::unique_lock<std::mutex> l(m); cv.wait_for(l, std::chrono::seconds(1), [this] { return stop.load(); });
            continue;
        }
        if(!dev)
        {
            HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
            if(h == INVALID_HANDLE_VALUE) { Log(LL_WARNING, "cannot open the keyboard (error %lu)", GetLastError()); Sleep(1000); continue; }
            dev = h; memset(sent, 0, sizeof(sent)); sendT = fullT = 0;
            if(!controller) Register();
        }
        std::vector<unsigned char> f;
        { std::unique_lock<std::mutex> l(m);
          cv.wait_for(l, std::chrono::milliseconds(150), [this] { return dirty || stop; });
          dirty = false; f = frame; }
        if(stop) break;
        if(!haveFrame) continue;                 // nothing from OpenRGB yet: leave the keyboard on its own effect
        bool full = false;
        if(!Write(f, full))
        {
            CloseHandle((HANDLE)dev); dev = nullptr;
            Log(LL_WARNING, "write to the keyboard failed (error %lu)", GetLastError());
            Unregister();
        }
    }
}
