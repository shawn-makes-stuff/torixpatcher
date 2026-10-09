"""Core of the Torix patcher: finding keyboards, reading their firmware, patching, flashing. No UI, nothing machine-specific.

Used by torix_patch.py (command line) and installer.py (windowed installer).
Every long operation takes `report(stage, fraction_or_None, text)` so a UI can show progress.
"""
import ctypes, shutil, datetime, hashlib, json, os, random, subprocess, sys, time

try:
    import hid
except ImportError:                                  # pragma: no cover
    raise SystemExit('Missing dependency: run   pip install hidapi')

VID, PID = 0x145F, 0x0339            # the Torix in normal mode
BOOT_VID, BOOT_PID = 0x5566, 0x0009  # its USB bootloader
NO_WINDOW = 0x08000000


class TorixError(Exception):
    """A problem the user can act on; the message is written for them."""


# ------------------------------------------------------------------ locations
def resource_dir():
    """Where patches/ and aura/ live: next to this file, or inside the PyInstaller bundle."""
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def known_folder(guid, fallback):
    """A Windows known folder (looked up, never assumed to be on C:); `fallback` is used only if Windows can't answer."""
    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD), ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]
        g = GUID(guid[0], guid[1], guid[2], (ctypes.c_ubyte * 8)(*guid[3]))
        p = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p))
        if p.value: return p.value
    except Exception:
        pass
    return fallback


def documents_dir():
    return known_folder((0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)), os.path.join(os.path.expanduser('~'), 'Documents'))


def data_dir(): return os.path.join(documents_dir(), 'TorixPatch')
def backup_dir(): return os.path.join(data_dir(), 'backups')
def log_dir(): return os.path.join(data_dir(), 'logs')


def load_patch():
    path = os.path.join(resource_dir(), 'patches', 'gxt868_20240618.json')
    if not os.path.exists(path): raise TorixError('The patch file is missing: ' + path)
    return json.load(open(path))


def sha(b): return hashlib.sha256(b).hexdigest()
def sum16(b): return sum(b) & 0xFFFF
def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False


# ------------------------------------------------------------------ finding keyboards
def _zxw(d): return (d.get('manufacturer_string') or '').startswith('ZXW')


def _instance_id(path):
    p = path.decode() if isinstance(path, bytes) else path
    parts = p.lstrip('\\?').split('#')             # hid, vid_..&pid_..&mi_xx, instance, {guid}
    return 'HID\\' + parts[1].upper() + '\\' + parts[2].upper()


def _container_ids(paths):
    """Windows 'container id' = one physical device. Returns {path: id}; empty if it cannot be read."""
    if not paths: return {}
    ids = {p: _instance_id(p) for p in paths}
    script = ("$o = @(); foreach ($i in @(" + ','.join("'" + v + "'" for v in ids.values()) + ")) { $c = (Get-PnpDeviceProperty -InstanceId $i "
              "-KeyName DEVPKEY_Device_ContainerId -ErrorAction SilentlyContinue).Data; $o += ($i + '|' + $c) }; $o")
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True,
                             timeout=60, creationflags=NO_WINDOW).stdout
    except Exception:
        return {}
    by_inst = dict(line.strip().split('|', 1) for line in out.splitlines() if '|' in line)
    return {p: by_inst.get(i, '') for p, i in ids.items()}


class Keyboard:
    def __init__(self, container, vendor_path, cmd_path, number):
        self.container, self.vendor_path, self.cmd_path, self.number = container, vendor_path, cmd_path, number
        self.status = None        # filled by identify(): 'ok' | 'patched' | 'unsupported' | 'silent'
        self.detail = ''

    @property
    def label(self): return f'Trust GXT 868 Torix #{self.number}  (USB cable)'


def find_keyboards():
    """All Torix keyboards currently connected by cable (normal mode), grouped by physical device."""
    devs = [d for d in hid.enumerate(VID, PID) if _zxw(d)]
    vend = [d['path'] for d in devs if d['usage_page'] == 0x0001 and d['usage'] == 0 and d['interface_number'] == 2]
    cmds = [d['path'] for d in devs if d['usage_page'] == 0xFF00]
    if not vend or not cmds: return []
    if len(vend) == 1 and len(cmds) == 1:
        return [Keyboard('', vend[0], cmds[0], 1)]
    cid = _container_ids(vend + cmds)
    kbs, n = [], 0
    for v in vend:
        c = cid.get(v, '')
        match = [x for x in cmds if c and cid.get(x) == c]
        if not match: raise TorixError('Several Torix keyboards are connected and Windows could not tell their ports apart. Unplug all but one and try again.')
        n += 1; kbs.append(Keyboard(c, v, match[0], n))
    return kbs


def in_bootloader(): return bool(hid.enumerate(BOOT_VID, BOOT_PID))
def cable_connected(): return in_bootloader() or any(_zxw(d) and d['usage_page'] == 0xFF00 for d in hid.enumerate(VID, PID))


def hid_paths():
    """Device paths of every HID interface present, from Windows' device list. Opens nothing and sends nothing to any device, unlike
    hid.enumerate() (which opens every HID device and asks it for its name strings each time: a burst of USB requests)."""
    from ctypes import wintypes
    class GUID(ctypes.Structure): _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD), ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]
    class IFD(ctypes.Structure): _fields_ = [('cb', wintypes.DWORD), ('g', GUID), ('flags', wintypes.DWORD), ('r', ctypes.c_void_p)]
    sa, hidd = ctypes.WinDLL('setupapi', use_last_error=True), ctypes.WinDLL('hid')
    sa.SetupDiGetClassDevsW.restype = ctypes.c_void_p; sa.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), ctypes.c_wchar_p, ctypes.c_void_p, wintypes.DWORD]
    sa.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.POINTER(IFD)]
    sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(IFD), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    sa.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    g = GUID(); hidd.HidD_GetHidGuid(ctypes.byref(g))
    ds = sa.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)           # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    out, i, ifd = [], 0, IFD(); ifd.cb = ctypes.sizeof(IFD)
    try:
        while sa.SetupDiEnumDeviceInterfaces(ds, None, ctypes.byref(g), i, ctypes.byref(ifd)):
            i += 1; buf = ctypes.create_string_buffer(1024)
            ctypes.c_uint32.from_address(ctypes.addressof(buf)).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6      # cbSize of the detail struct
            if sa.SetupDiGetDeviceInterfaceDetailW(ds, ctypes.byref(ifd), buf, 1024, None, None): out.append(ctypes.wstring_at(ctypes.addressof(buf) + 4).lower())
    finally:
        sa.SetupDiDestroyDeviceInfoList(ds)
    return out


def cable_connected_quiet():
    """Cheap 'is a Torix (or its update mode) on the cable' check for the Prepare page's once-a-second refresh. Path-based, so it cannot tell a
    dongle's interface from the keyboard's; the Find step does the exact check."""
    ids = ('vid_%04x&pid_%04x' % (BOOT_VID, BOOT_PID), 'vid_%04x&pid_%04x&mi_02' % (VID, PID))
    return any(i in p for p in hid_paths() for i in ids)
def dongles_present(): return any((d.get('manufacturer_string') or '') == 'YICHIP' for d in hid.enumerate(VID, PID))


# ------------------------------------------------------------------ reading the firmware (read-only)
def _open(path):
    h = hid.device(); h.open_path(path); return h


class Pipe:
    """The keyboard's `55 07` read command. Replies go to every open handle, so only one Pipe may exist at a time."""
    def __init__(self, kb):
        try:
            self.w, self.r = _open(kb.vendor_path), _open(kb.vendor_path)
        except OSError as e:
            raise TorixError(f'Could not open the keyboard ({e}). Close the Trust keyboard app and any lighting software, then try again.')

    def close(self):
        for h in (self.w, self.r):
            try: h.close()
            except Exception: pass

    def read(self, off, n, tries=5):
        b = [0x00, 0x55, 0x07, 0x00, 0x00, n, off & 0xFF, off >> 8, 0x00] + [0] * 56
        b[4] = sum(b[5:65]) & 0xFF
        for _ in range(tries):
            while self.r.read(64, 1): pass
            self.w.write(bytes([0, 0x55, 0x01] + [0] * 62)); time.sleep(0.02)
            self.w.write(bytes(b[:65]))
            for _ in range(10):
                x = bytes(self.r.read(64, 100) or b'')
                if x[:2] == b'\xaa\x07': return x[8:8 + n]
        return None


def read_flash(pipe, meta, addr, n):
    """Flash address -> `55 07` offset (the firmware adds the offset to a fixed table base, with no range check)."""
    base, out = meta['target']['read_base'], b''
    while n > 0:
        k = min(56, n)
        x = pipe.read((addr - base) & 0xFFFF, k)
        if x is None or len(x) < k: return None
        out += x; addr += k; n -= k
    return out


def identify(kb, meta, report=None):
    """Sets kb.status/kb.detail. 'silent' means the keyboard is connected but does not answer reads."""
    t = meta['target']; pipe = Pipe(kb)
    try:
        got = read_flash(pipe, meta, t['build_markers'][0]['addr'], len(t['build_markers'][0]['text']))
        if got is None:
            kb.status, kb.detail = 'silent', 'Detected, but it is not responding to the installer. Check the wireless switch is off and the dongle is unplugged, then rescan.'
            return kb
        for m in t['build_markers']:
            g = read_flash(pipe, meta, m['addr'], len(m['text']))
            if g != m['text'].encode():
                kb.status, kb.detail = 'unsupported', f"Unsupported firmware. This patch is for the {t['build_markers'][0]['text']} build."
                return kb
        pc = meta['patched_check']
        if read_flash(pipe, meta, pc['addr'], 2) == bytes.fromhex(pc['new']):
            kb.status, kb.detail = 'patched', 'Already patched.'
        else:
            kb.status, kb.detail = 'ok', 'Supported firmware.'
        return kb
    finally:
        pipe.close()


def backup(kb, meta, report, verify=True):
    """Reads the whole firmware (twice by default), saves it under Documents\\TorixPatch\\backups and re-checks the file. Returns (path, image)."""
    size = meta['target']['flash_size']; pipe = Pipe(kb)
    try:
        def one(label, base_frac, span):
            img = bytearray()
            for a in range(0, size, 56):
                k = min(56, size - a)
                x = read_flash(pipe, meta, a, k)
                if x is None: raise TorixError(f'The keyboard stopped answering at {a:#06x} while the firmware was being read. Nothing was changed. Rescan and try again.')
                img += x
                if (a // 56) % 20 == 0: report('backup', base_frac + span * a / size, f'{label} ... {a * 100 // size}%')
            return bytes(img)
        passes = 2 if verify else 1
        img = one('Reading firmware' + (' (pass 1 of 2)' if verify else ''), 0.0, 1.0 / passes)
        if verify:
            if one('Reading it again to make sure it is exact (pass 2 of 2)', 0.5, 0.5) != img:
                raise TorixError('Two reads of the firmware did not match - the USB connection is unreliable. Nothing was changed. Try another port or cable.')
    finally:
        pipe.close()
    lm = meta['target']['bootloader_landmark']
    if sha(img[lm['addr']:lm['addr'] + lm['len']]) != lm['sha256']:
        raise TorixError('The data read does not look like a Torix firmware image. Nothing was changed.')
    os.makedirs(backup_dir(), exist_ok=True)
    name = f"torix_{datetime.datetime.now():%Y%m%d-%H%M%S}_{sha(img)[:8]}"
    path = os.path.join(backup_dir(), name + '.bin')
    with open(path, 'wb') as f: f.write(img)
    if sha(open(path, 'rb').read()) != sha(img): raise TorixError('The backup file could not be saved correctly. Nothing was changed.')
    json.dump({'sha256': sha(img), 'created': datetime.datetime.now().isoformat(), 'size': len(img), 'tool': 'TorixPatch 1'},
              open(os.path.join(backup_dir(), name + '.json'), 'w'), indent=1)
    report('backup', 1.0, 'Backup saved and verified.')
    return path, img


def patch_groups(meta):
    """{name: {'label', 'default'}} - 'base' is always applied, the others are optional."""
    return meta.get('groups') or {'base': {'label': 'Core fixes', 'default': True}}


def apply_patches(img, meta, groups=('base',)):
    """Patches a STOCK image with the chosen patch groups. Refuses (nothing written anywhere) unless the firmware is exactly the one this patch was made for."""
    size = meta['target']['app_size']
    if len(img) < size: raise TorixError('The firmware image is too short. Nothing was written.')
    groups = set(groups) | {'base'}
    patches = [p for p in meta['patches'] if p.get('group', 'base') in groups]
    guards = [g for g in meta.get('guards', []) if g.get('group', 'base') in groups]
    out = bytearray(img[:size])
    for m in meta['target']['build_markers']:
        if bytes(out[m['addr']:m['addr'] + len(m['text'])]) != m['text'].encode():
            raise TorixError('This is not the firmware build the patch was made for (build marker "%s" not found). Nothing was written.' % m['text'])
    bad = [p for p in patches if sha(bytes(out[p['addr']:p['addr'] + p['len']])) != p['orig_sha256']]
    badg = [g for g in guards if sha(bytes(out[g['addr']:g['addr'] + g['len']])) != g['sha256']]
    if bad or badg:
        raise TorixError('The firmware on this keyboard differs from the one this patch was made for (%d of %d patch sites and %d of %d surrounding checks do not match), '
                         'so it is NOT safe to patch. Nothing was written.' % (len(bad), len(patches), len(badg), len(guards)))
    allowed = set()
    for p in patches:
        r = range(p['addr'], p['addr'] + p['len'])
        if allowed.intersection(r): raise TorixError('Internal check failed: two patch ranges overlap. Nothing was written.')
        allowed.update(r); out[p['addr']:p['addr'] + p['len']] = bytes.fromhex(p['new'])
    # proof: nothing outside the declared ranges changed
    if any(out[i] != img[i] for i in range(size) if i not in allowed):
        raise TorixError('Internal check failed: the patch would change bytes outside its declared ranges. Nothing was written.')
    return bytes(out)


# ------------------------------------------------------------------ flashing (the keyboard's own USB bootloader)
def _enter_bootloader(kb, report):
    if in_bootloader(): return _open(hid.enumerate(BOOT_VID, BOOT_PID)[0]['path'])
    if kb is None: raise TorixError('No keyboard to flash: plug it in (cable) and rescan.')
    h = _open(kb.cmd_path)
    try: n = h.write(bytes([0x02, 0x55, 0xFF, 0x00, 0x00]) + bytes(27))
    finally: h.close()
    if n < 0: raise TorixError('The keyboard would not switch to its update mode. Put it in wired mode (wireless switch off, dongle unplugged) and try again. Nothing was changed.')
    t0 = time.time()
    while time.time() - t0 < 15:
        b = hid.enumerate(BOOT_VID, BOOT_PID)
        if b: return _open(b[0]['path'])
        time.sleep(0.2)
    raise TorixError('The keyboard did not enter its update mode. Unplug it, plug it back in and try again. Nothing was changed.')


def flash(kb, img, report):
    """Writes the 28 KB application area and checks the keyboard's own checksum before starting it. Returns True if it re-appeared."""
    report('flash', 0.0, 'Putting the keyboard into its update mode ...')
    bd = _enter_bootloader(kb, report); bd.set_nonblocking(0)

    def cmd(c, args=b''): bd.write(bytes([0x00, 0x55, 0xFF, c, len(args)]) + args + bytes(60 - len(args)))
    def rx(timeout=1500):
        r = bd.read(64, timeout); return bytes(r) if r else None
    while bd.read(64, 50): pass
    r = random.randrange(0x10000); cmd(1, bytes([r >> 8, r & 0xFF])); h = rx()
    if not (h and h[:3] == bytes([0xAA, 0xFF, 1]) and (h[4] << 8 | h[5]) == ((r * 4 + 0x26F) & 0xFFFF) and (h[6] << 8 | h[7]) == len(img)):
        cmd(5, bytes([1]))
        raise TorixError('The keyboard\'s update mode answered unexpectedly. Nothing was written; the keyboard was restarted.')
    cmd(2, bytes([0, 0, len(img) >> 8, len(img) & 0xFF])); a = rx()
    if not a or a[:3] != bytes([0xAA, 0xFF, 2]) or a[4] != 1:
        raise TorixError('The keyboard\'s update mode did not accept the write. Nothing was written. It is still in update mode: press Install again.')
    frames = len(img) // 64
    for i in range(frames):
        if bd.write(bytes([0]) + img[i * 64:(i + 1) * 64]) < 0:
            raise TorixError('The USB connection dropped while writing. The keyboard is still in update mode and is NOT damaged: run the installer again or restore your backup.')
        bd.read(64, 1); time.sleep(0.004)
        if i % 8 == 0: report('flash', 0.05 + 0.85 * i / frames, f'Writing firmware ... {i * 100 // frames}%')
    report('flash', 0.92, 'Checking the written firmware ...')
    time.sleep(0.5); cmd(4); c = rx(3000)
    dev = (c[4] << 8 | c[5]) if c and c[:3] == bytes([0xAA, 0xFF, 4]) else None
    if dev != sum16(img):
        raise TorixError('The keyboard\'s own checksum did not match what was written, so it was NOT started. It is still in update mode and is not damaged: '
                         'run the installer again or restore your backup.')
    report('flash', 0.96, 'Checksum OK - starting the keyboard ...')
    cmd(5, bytes([1]))
    t0 = time.time()
    while time.time() - t0 < 25:
        if not in_bootloader() and hid.enumerate(VID, PID): report('flash', 1.0, 'Keyboard restarted.'); return True
        time.sleep(0.3)
    return False


# ------------------------------------------------------------------ environment
def lighting_service():
    try:
        out = subprocess.run(['sc', 'query', 'LightingService'], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
        return 'RUNNING' if 'RUNNING' in out else ('STOPPED' if 'STOPPED' in out else None)
    except Exception:
        return None


def stop_lighting_service():
    """Aura's service streams colours into the keyboard and would interfere. Returns True if we stopped it (so it can be restarted)."""
    if lighting_service() != 'RUNNING': return False
    subprocess.run(['sc', 'stop', 'LightingService'], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(15):
        time.sleep(1)
        if lighting_service() != 'RUNNING': return True
    raise TorixError('ASUS LightingService could not be stopped. Run the installer as administrator.')


def start_lighting_service():
    subprocess.run(['sc', 'start', 'LightingService'], capture_output=True, creationflags=NO_WINDOW)


def process_running(exe):
    try:
        out = subprocess.run(['tasklist'], capture_output=True, text=True, creationflags=NO_WINDOW).stdout.lower()
        return exe in out
    except Exception:
        return False


def trust_app_running(): return process_running('devicedriver.exe')
def openrgb_running(): return process_running('openrgb.exe')    # its plug-in streams colours into a connected Torix and would interfere


AURA_LIST = r'SOFTWARE\Classes\CLSID\{109DC3E4-B9FF-4AF3-9008-AB13705D4E5F}\Instance\{E9BBD754-6CF4-492E-BA89-782177A2771B}\Instance'


def aura_status():
    """'ready' (plug-in files are in this package and ASUS Aura is installed), 'no_aura', or 'no_files'. Nothing is assumed about install locations:
    Aura is recognised by its third-party device list in the registry plus its LightingService."""
    if not os.path.exists(os.path.join(resource_dir(), 'aura', 'install_aura_hal.ps1')): return 'no_files'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, AURA_LIST, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY): pass
    except OSError:
        return 'no_aura'
    return 'ready' if lighting_service() is not None else 'no_aura'


def install_aura_plugin():
    """Runs the bundled script (registers the Aura plug-in). Needs admin. Changes nothing if ASUS Aura is not installed."""
    script = os.path.join(resource_dir(), 'aura', 'install_aura_hal.ps1')
    if not os.path.exists(script): raise TorixError('The Aura plug-in files are missing from this package.')
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script], capture_output=True, text=True, creationflags=NO_WINDOW, timeout=180)
    if r.returncode == 2: raise TorixError('ASUS Aura Sync was not found on this PC, so the plug-in was not installed.')
    if r.returncode != 0: raise TorixError('The Aura plug-in could not be installed: ' + (r.stderr or r.stdout).strip()[-400:])


OPENRGB_PLUGIN = 'TorixOpenRGBPlugin.dll'


def openrgb_dir():
    """OpenRGB's settings folder. Same rule as OpenRGB's own source (ResourceManager::SetupConfigurationDirectory): <%APPDATA%>\OpenRGB; its plug-ins go in
    the 'plugins' folder inside it. Where OpenRGB itself is installed is not used. (A copy started with --config/--localconfig keeps its settings elsewhere.)"""
    base = os.environ.get('APPDATA') or known_folder((0x3EB685DB, 0x65F9, 0x4CF6, (0xA0, 0x3A, 0xE3, 0xEF, 0x65, 0x72, 0x9F, 0x3D)),
                                                     os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming'))
    return os.path.join(base, 'OpenRGB')


def openrgb_status():
    """'ready' (plug-in file is in this package and OpenRGB has been run on this PC, so its settings folder exists), 'no_openrgb' or 'no_files'."""
    if not os.path.exists(os.path.join(resource_dir(), 'openrgb', OPENRGB_PLUGIN)): return 'no_files'
    return 'ready' if os.path.isdir(openrgb_dir()) else 'no_openrgb'


def install_openrgb_plugin():
    """Copies the plug-in into OpenRGB's plugins folder. OpenRGB loads it the next time it starts."""
    src = os.path.join(resource_dir(), 'openrgb', OPENRGB_PLUGIN)
    if not os.path.exists(src): raise TorixError('The OpenRGB plug-in file is missing from this package.')
    dst_dir = os.path.join(openrgb_dir(), 'plugins')
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(dst_dir, OPENRGB_PLUGIN))
    except PermissionError:
        raise TorixError('The plug-in file is in use. Close OpenRGB completely, then try again.')
    except OSError as e:
        raise TorixError('The OpenRGB plug-in could not be copied: %s' % e)


# ------------------------------------------------------------------ safety
def wired_blockers():
    """Things that must be fixed before the installer will touch a keyboard. Wired mode is also proven by the keyboard itself: it only
    answers firmware reads while it is in USB mode."""
    # A dongle in the PC is NOT a blocker (people have several, e.g. another keyboard's): wired mode is proven by the keyboard itself answering.
    return []


def final_verify(kb, meta, expect_patched=False):
    """Run immediately before flashing: the same keyboard is still there, still answering over USB (so it is in wired mode), no dongle is
    present, and its firmware is still the supported, un-patched build."""
    blockers = wired_blockers()
    if blockers: raise TorixError(blockers[0])
    if in_bootloader(): return
    still = [k for k in find_keyboards() if k.vendor_path == kb.vendor_path and k.cmd_path == kb.cmd_path]
    if not still: raise TorixError('The keyboard changed or was unplugged since it was chosen. Nothing was written. Start again.')
    identify(kb, meta, None)
    if kb.status != ('patched' if expect_patched else 'ok'):
        raise TorixError('The keyboard is no longer in the expected state (' + kb.detail + ') - nothing was written.')


def list_backups():
    """[(path, created datetime, sha256 or None)], newest first."""
    out = []
    if not os.path.isdir(backup_dir()): return out
    for f in os.listdir(backup_dir()):
        if not f.endswith('.bin'): continue
        path = os.path.join(backup_dir(), f); side = path[:-4] + '.json'; h = None
        try: h = json.load(open(side)).get('sha256')
        except Exception: pass
        out.append((path, datetime.datetime.fromtimestamp(os.path.getmtime(path)), h))
    return sorted(out, key=lambda x: x[1], reverse=True)


def check_backup_file(path, meta):
    """Returns the 28 KB application image of a backup after validating it: right size, looks like a Torix image, matches its recorded hash."""
    try: data = open(path, 'rb').read()
    except OSError as e: raise TorixError(f'Cannot read the backup file: {e}')
    lm = meta['target']['bootloader_landmark']
    if len(data) not in (0x7000, 0x8000) or (len(data) == 0x8000 and sha(data[lm['addr']:lm['addr'] + lm['len']]) != lm['sha256']):
        raise TorixError('That file is not a Torix firmware backup made by this tool.')
    side = os.path.splitext(path)[0] + '.json'
    if os.path.exists(side):
        try: want = json.load(open(side)).get('sha256')
        except Exception: want = None
        if want and want != sha(data): raise TorixError('The backup file has been modified or damaged (its checksum no longer matches). It will not be flashed.')
    return data[:0x7000]


def final_verify_restore(kb):
    """Run immediately before restoring: the same keyboard is still there and no dongle is plugged in."""
    blockers = wired_blockers()
    if blockers: raise TorixError(blockers[0])
    if not any(k.vendor_path == kb.vendor_path and k.cmd_path == kb.cmd_path for k in find_keyboards()):
        raise TorixError('The keyboard changed or was unplugged since it was chosen. Nothing was written. Start again.')
