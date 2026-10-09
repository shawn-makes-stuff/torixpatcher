# Torix Patcher

A firmware patch for the **Trust GXT 868 Torix wireless keyboard**, installed by a small windowed installer. <br>The installer creates a backup of the stock firmware, applies the patch, and flashes the new firmware to the keyboard. Read [Risks](#risks) first. <br><br>No Trust code is included in this repo.

## So why use it?
This project started as an attempt to drive custom lighting over USB, however several problems exist in the stock firmware that made it less than ideal. So... I fixed it.

Three major issues fixed to make this happen:
1. Default firmware has a bug where several keys will not light in per-key lighting mode, fixed in our patch.
2. Default firmware dropped per/key frames sent less than 8ms apart, patched accepts 2ms gaps. Much smoother animation.
3. Default firmware left keyboard in a stuck state, needing a reset if unplugged while streaming frames from your pc. <br>Keyboard now automatically switches to USB mode when plugged in, and wifi when not.

Optional OpenRGB and Aura plugins will be offered by the installer if it detects either on your system.

## Set up

Download `TorixPatch.zip` from the [latest release](../../releases/latest), unzip it and run **`TorixInstaller.exe`** <br>(Windows will warn that it is unsigned; it asks for administrator rights once. `Install.bat` is a fallback that needs Python and `pip install hidapi`).

1. Unplug the keyboard's 2.4 GHz dongle, switch wireless **off**, connect the **USB-C cable**.
2. The installer finds the keyboard, checks it, and saves a **backup of your original firmware** to `Documents\TorixPatch\backups`.
3. Press **Install now** and leave it plugged in for about a minute.
4. Switch wireless back on and plug the dongle in.


## Optional plug-ins

* **OpenRGB** (version **1.0 or later**, from <https://openrgb.org>; run it once first). Copies `TorixOpenRGBPlugin.dll` into `%APPDATA%\OpenRGB\plugins`. Restart OpenRGB and *Trust GXT 868 Torix* appears as a keyboard with 104 keys in a 6x21 grid. For animated effects add OpenRGB's [Effects plugin](https://openrgb.org/plugin_effects.html).
* **ASUS Aura Sync**. Registers a plug-in with Aura's lighting service; then tick *Trust GXT 868 Torix* in Armoury Crate > Aura Sync.


## Undo

Run the installer again and choose **Restore the original firmware**, then pick your backup. Keep that backup file safe. If a flash is ever interrupted the keyboard waits in its USB update mode (it is not bricked): run the installer and choose Restore.

## Safety

* **Wired only**, and only the exact firmware build **"Jun 18 2024 17:56:18"** (USB 145F:0339, "GXT 868 TORIX Wireless"). The installer checks the build string, the original bytes at every patch site and the code around them; if anything differs it stops and changes nothing.
* Only the declared bytes change (123 bytes in 9 places) and the bootloader is never touched. A verified backup always comes first, and the written firmware is checked against the keyboard's own checksum before it starts.
* Aura's lighting service is paused and the Trust app closed while the installer runs; it asks you to close OpenRGB.
* Don't type on the keyboard being flashed while the installer runs.

## Risks

Flashing firmware can in principle make a keyboard unusable and is not supported by Trust; it may void your warranty. You use this at your own risk. I have no affiliation with Trust International and am not responsible for any damages to your keyboard in case of a failed install.

## Files

* `TorixInstaller.exe` / `installer.py` (GUI), `torixlib.py` (engine), `torix_patch.py` (command line: `status`, `backup`, `patch`, `restore`, `selftest`).
* `patches/gxt868_20240618.json`: the patch (hashes of the originals, our new bytes, what each change does).
* `aura/`, `openrgb/`: the optional plug-ins (source; the release zip also has the built DLLs).

## License

GPL-3.0 (`LICENSE`).
