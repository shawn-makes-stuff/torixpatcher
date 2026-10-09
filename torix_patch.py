#!/usr/bin/env python3
"""Command-line version of the Torix patcher (the windowed installer is `installer.py` / TorixInstaller.exe).

  python torix_patch.py status          list the connected keyboards and what firmware they run
  python torix_patch.py backup          read-only copy of the firmware into Documents\\TorixPatch\\backups
  python torix_patch.py patch           backup, verify, patch and flash (asks first; --yes to skip the question)
  python torix_patch.py restore FILE    flash a backup file back
  python torix_patch.py selftest FILE   apply the patch to a dump file offline (no keyboard needed)

Add --keyboard N to choose when several are connected. Needs `pip install hidapi`; run from an administrator prompt.
"""
import argparse, sys
import torixlib as T


def say(msg): print(msg, flush=True)


def report(stage, frac, text):
    say(f'  [{stage}] ' + (f'{frac * 100:3.0f}% ' if frac is not None else '') + text)


def pick(a):
    kbs = T.find_keyboards()
    if not kbs: raise T.TorixError('No Torix keyboard found. Plug it in with the USB cable (wireless switch off, dongle unplugged).')
    if len(kbs) > 1 and not a.keyboard:
        raise T.TorixError('Several keyboards found: ' + ', '.join(k.label for k in kbs) + '. Choose one with --keyboard N.')
    return kbs[(a.keyboard or 1) - 1]


def prepare():
    if T.wired_blockers(): raise T.TorixError(T.wired_blockers()[0] + ' (then switch the keyboard wireless switch OFF and connect it with the USB cable)')
    if T.trust_app_running(): raise T.TorixError('Close the Trust keyboard app (DeviceDriver.exe) first.')
    return T.stop_lighting_service()


def cmd_status(a):
    meta = T.load_patch(); kbs = T.find_keyboards()
    if not kbs: return say('No Torix keyboard found.')
    stopped = prepare()
    try:
        for k in kbs:
            T.identify(k, meta, report); say(f'{k.label}: {k.status} - {k.detail}')
    finally:
        if stopped: T.start_lighting_service()


def cmd_patch(a):
    meta = T.load_patch(); kb = pick(a); stopped = prepare()
    try:
        T.identify(kb, meta, report); say(f'{kb.label}: {kb.status} - {kb.detail}')
        if kb.status != 'ok': raise T.TorixError(kb.detail)
        say(f'Making a backup first, saved in {T.backup_dir()}')
        path, img = T.backup(kb, meta, report, not a.fast)
        new = T.apply_patches(img, meta)
        say('Verified. The patch will change:')
        for p in meta['patches']: say(f"   {p['addr']:#06x}  {p['what']}")
        if not a.yes and input(f'\nBackup: {path}\nType FLASH to write the patched firmware (about 1 minute): ').strip() != 'FLASH':
            raise T.TorixError('Cancelled. Nothing was written.')
        ok = T.flash(kb, new, report)
        say('Done.' if ok else 'Flashed; replug the keyboard if it does not reappear.')
    finally:
        if stopped: T.start_lighting_service()


def cmd_backup(a):
    meta = T.load_patch(); kb = pick(a); stopped = prepare()
    try:
        T.identify(kb, meta, report)
        if kb.status in ('silent', 'unsupported'): raise T.TorixError(kb.detail)
        if kb.status == 'patched': raise T.TorixError('Already patched: its low flash can no longer be read back; use the backup you made before patching.')
        path, _ = T.backup(kb, meta, report, not a.fast); say('Saved: ' + path)
    finally:
        if stopped: T.start_lighting_service()


def cmd_restore(a):
    data = open(a.file, 'rb').read(); meta = T.load_patch()
    if len(data) not in (0x7000, 0x8000): raise T.TorixError('Unexpected file size; expected a 32 KB backup made by this tool.')
    lm = meta['target']['bootloader_landmark']
    if len(data) == 0x8000 and T.sha(data[lm['addr']:lm['addr'] + lm['len']]) != lm['sha256']:
        raise T.TorixError('That file does not look like a Torix firmware image.')
    kb = None if T.in_bootloader() else pick(a); stopped = prepare()
    try:
        if not a.yes and input('Type FLASH to restore this firmware onto the keyboard: ').strip() != 'FLASH': raise T.TorixError('Cancelled.')
        ok = T.flash(kb, data[:0x7000], report)
        say('Restored.' if ok else 'Restored; replug the keyboard if it does not reappear.')
    finally:
        if stopped: T.start_lighting_service()


def cmd_selftest(a):
    meta = T.load_patch(); new = T.apply_patches(open(a.file, 'rb').read(), meta)
    say(f'patched {len(meta["patches"])} sites; app sum16 {T.sum16(new):#06x}; sha256 {T.sha(new)}')
    if a.compare:
        same = new == open(a.compare, 'rb').read()[:0x7000]
        say('identical to reference' if same else 'DIFFERENT from reference'); sys.exit(0 if same else 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    for n, fn in (('status', cmd_status), ('backup', cmd_backup), ('patch', cmd_patch), ('restore', cmd_restore), ('selftest', cmd_selftest)):
        p = sp.add_parser(n); p.set_defaults(fn=fn)
        if n in ('restore', 'selftest'): p.add_argument('file')
        if n == 'selftest': p.add_argument('--compare')
        if n in ('backup', 'patch', 'restore'): p.add_argument('--keyboard', type=int)
        if n in ('backup', 'patch'): p.add_argument('--fast', action='store_true', help='read the firmware once instead of twice')
        if n in ('patch', 'restore'): p.add_argument('--yes', action='store_true')
    a = ap.parse_args()
    try:
        a.fn(a)
    except T.TorixError as e:
        say('\nSTOPPED: ' + str(e)); sys.exit(1)


if __name__ == '__main__':
    main()
