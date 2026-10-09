# Torix Patcher

A firmware patch for the **Trust GXT 868 Torix wireless keyboard**, installed by a small windowed installer. It reflashes your keyboard, so read [Risks](#risks) first. No Trust code is included: the patch is only hashes and our replacement bytes, applied to your own keyboard's firmware.

## Why use it

The stock firmware has bugs that get in the way of lighting software:

| | stock | patched |
|---|---|---|
| Plug the cable in while on wireless | stays on the radio and ignores the cable until a power cycle | **USB takes over as soon as it is active**, wireless takes over again when unplugged |
| Per-key lighting from a PC | 7 keys (1, F7, T, I, K, Enter, numpad 0) stay dark; frames are dropped if sent faster than ~8 ms apart | **all keys light**; frames accepted at 2 ms gaps |
| Charging LED by the knob | may follow PC lighting | left to the keyboard |

Key mapping, the Trust app, the wireless code, the knob and the built-in effects are unchanged. Colours streamed from a PC are not saved on the keyboard: unplug and it returns to its own effect.

With the patch you can drive the keyboard per key from **[OpenRGB](https://openrgb.org)** or **ASUS Aura Sync** using the optional plug-ins below.

## Set up

Download `TorixPatch.zip` from the [latest release](../../releases/latest), unzip it and run **`TorixInstaller.exe`** (Windows will warn that it is unsigned; it asks for administrator rights once. `Install.bat` is a fallback that needs Python and `pip install hidapi`).

1. Unplug the keyboard's 2.4 GHz dongle, switch wireless **off**, connect the **USB-C cable**.
2. The installer finds the keyboard, checks it, and saves a **backup of your original firmware** to `Documents\TorixPatch\backups`.
3. Press **Install now** and leave it plugged in for about a minute.
4. Switch wireless back on and plug the dongle in.

If OpenRGB or ASUS Aura Sync is on your PC the installer then offers its plug-in as an optional last step. Skip the ones you don't use.

## Optional plug-ins (need the patched firmware, keyboard on USB cable)

* **OpenRGB** (version **1.0 or later**, from <https://openrgb.org>; run it once first). Copies `TorixOpenRGBPlugin.dll` into `%APPDATA%\OpenRGB\plugins`. Restart OpenRGB and *Trust GXT 868 Torix* appears as a keyboard with 104 keys in a 6x21 grid. For animated effects add OpenRGB's [Effects plugin](https://openrgb.org/plugin_effects.html). One Torix at a time. Remove: delete that file (or `openrgb\uninstall_openrgb_plugin.bat`).
* **ASUS Aura Sync**. Registers a plug-in with Aura's lighting service; then tick *Trust GXT 868 Torix* in Armoury Crate > Aura Sync. Remove: `aura\uninstall_aura_plugin.bat`.

Nothing is assumed about where programs are installed: Windows' own folders and each program's registry entry or settings folder are looked up, and a plug-in is only offered when its program is found.

## Undo

Run the installer again and choose **Restore the original firmware**, then pick your backup. Keep that backup file safe. If a flash is ever interrupted the keyboard waits in its USB update mode (it is not bricked): run the installer and choose Restore.

## Safety

* **Wired only**, and only the exact firmware build **"Jun 18 2024 17:56:18"** (USB 145F:0339, "GXT 868 TORIX Wireless"). The installer checks the build string, the original bytes at every patch site and the code around them; if anything differs it stops and changes nothing.
* Only the declared bytes change (123 bytes in 9 places) and the bootloader is never touched. A verified backup always comes first, and the written firmware is checked against the keyboard's own checksum before it starts.
* Aura's lighting service is paused and the Trust app closed while the installer runs; it asks you to close OpenRGB.
* Don't type on the keyboard being flashed while the installer runs.

## Risks

Flashing firmware can in principle make a keyboard unusable and is not supported by Trust; it may void your warranty. You use this at your own risk. No affiliation with Trust International or ASUS. **Tested on a single keyboard**; if it works on yours (or not), please open an issue.

## Files

* `TorixInstaller.exe` / `installer.py` (GUI), `torixlib.py` (engine), `torix_patch.py` (command line: `status`, `backup`, `patch`, `restore`, `selftest`).
* `patches/gxt868_20240618.json`: the patch (hashes of the originals, our new bytes, what each change does).
* `aura/`, `openrgb/`: the optional plug-ins (source; the release zip also has the built DLLs).

## License

GPL-3.0 (`LICENSE`). Trust's firmware is theirs and is not included.
