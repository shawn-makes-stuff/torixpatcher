#pragma once
// OpenRGB plugin for the PATCHED Trust GXT 868 Torix keyboard (USB cable). Registers the keyboard as an OpenRGB device
// (6 x 21 key matrix, so the Effects plugin works in 2D) and streams its colours to the keyboard's per-key vendor channel.
// Licensed GPL-2.0-or-later like OpenRGB, whose plugin headers it is built against.
#include <QObject>
#include <QtPlugin>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <vector>
#include "OpenRGBPluginInterface.h"

class TorixPlugin : public QObject, public OpenRGBPluginInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID OpenRGBPluginInterface_IID FILE "TorixPlugin.json")
    Q_INTERFACES(OpenRGBPluginInterface)

public:
    OpenRGBPluginInfo   GetPluginInfo() override;
    unsigned int        GetPluginAPIVersion() override { return OPENRGB_PLUGIN_API_VERSION; }
    void                Load(OpenRGBPluginAPIInterface* plugin_api_ptr) override;
    QWidget*            GetWidget() override;
    QMenu*              GetTrayMenu() override { return nullptr; }
    void                Unload() override;
    void                OnProfileAboutToLoad() override {}
    void                OnProfileLoad(nlohmann::json) override {}
    nlohmann::json      OnProfileSave() override { return nlohmann::json(); }
    unsigned char*      OnSDKCommand(unsigned int, unsigned char*, unsigned int*) override { return nullptr; }
    void                ProfileManagerUpdated(unsigned int) override {}
    void                ResourceManagerUpdated(unsigned int) override {}
    void                SettingsManagerUpdated(unsigned int) override {}

private:
    static void         UpdateLEDsCallback(void* self);          // OpenRGB -> plugin: new colours
    void                OnColours();
    void                Run();                                   // worker: finds the keyboard, registers the device, streams frames
    bool                Write(const std::vector<unsigned char>& buf, bool& full);
    void                Register();
    void                Unregister();
    void                Log(unsigned int level, const char* fmt, ...);

    OpenRGBPluginAPIInterface*  api = nullptr;
    RGBControllerInterface*     controller = nullptr;
    std::thread                 worker;
    std::atomic<bool>           stop{false};
    std::mutex                  m;
    std::condition_variable     cv;
    std::vector<unsigned char>  frame;                           // 384 bytes, R,G,B per keyboard LED index
    bool                        dirty = false, haveFrame = false;
    void*                       dev = nullptr;                   // HANDLE of the open keyboard channel
    unsigned char               sent[384] = {};
    unsigned long long          sendT = 0, fullT = 0;
};
