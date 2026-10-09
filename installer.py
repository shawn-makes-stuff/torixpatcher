"""Torix firmware patch installer (windowed). Run installer.py, or TorixInstaller.exe. Nothing here is specific to one machine.

Safety rules the flow enforces (see torixlib.py): wired keyboards only, dongle unplugged, wireless switch off; a verified backup before any
write; refuses unless the firmware is exactly the supported build; one last re-verification right before flashing; restore at any time."""
import ctypes, datetime, os, queue, subprocess, sys, textwrap, threading, time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import torixlib as T

VERSION = '1.0'
FONT = 'Segoe UI'
C = dict(side='#0e1a33', side_fg='#e8eefb', side_dim='#8190b0', accent='#2563eb', accent_d='#1d4ed8', accent_l='#dbe7ff', ok='#15803d', bad='#c62828',
         warn='#b45309', text='#16213a', muted='#5b6577', card='#f5f7fb', border='#d5dbe7', tint='#eaf1ff', white='#ffffff', footer='#f3f5f9',
         red_bg='#fdecec', red_fg='#8a1f1f', amb_bg='#fff4dc', amb_fg='#7a4a00', grn_bg='#e8f6ed', grn_fg='#14532d')
INSTALL_STEPS = ['Disclaimer', 'Welcome', 'Prepare', 'Find keyboard', 'Checks', 'Backup', 'Install', 'Done']
RESTORE_STEPS = ['Disclaimer', 'Welcome', 'Choose backup', 'Prepare', 'Find keyboard', 'Restore', 'Done']
S = lambda px: px          # scaled in main() once the DPI is known
SUMMARY = ['Faster per-key lighting from your PC.', 'Fixes keys that stayed dark in per-key lighting (1, F7, T, I, K, Enter, numpad 0).',
           'Better wired/wireless switching: USB takes over when you plug the cable in.']


def elevate_if_needed():
    """Re-launch with administrator rights (needed to pause Aura's service and close the Trust app). One UAC prompt."""
    if T.is_admin() or os.name != 'nt' or os.environ.get('TORIX_NO_ELEVATE'): return
    frozen = getattr(sys, 'frozen', False)
    args = ' '.join('"%s"' % a for a in (sys.argv[1:] if frozen else sys.argv))
    if ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, args, None, 1) > 32: sys.exit(0)
    messagebox.showerror('Administrator rights needed', 'The installer needs administrator rights to pause the lighting service. Please allow the prompt.')
    sys.exit(1)


class Check(tk.Frame):
    """Blue box with a white tick; click the box or the text."""
    def __init__(self, parent, text, var, command=None, bg=None, size=10):
        super().__init__(parent, bg=bg or parent.cget('bg'), cursor='hand2')
        self.var, self.command = var, command
        self.cv = tk.Canvas(self, width=S(22), height=S(22), bg=self.cget('bg'), highlightthickness=0); self.cv.pack(side='left')
        self.lb = tk.Label(self, text=text, bg=self.cget('bg'), fg=C['text'], font=(FONT, size), anchor='w'); self.lb.pack(side='left', padx=(S(10), 0))
        for w in (self, self.cv, self.lb): w.bind('<Button-1>', self.toggle)
        var.trace_add('write', lambda *a: self.draw() if self.cv.winfo_exists() else None); self.draw()

    def draw(self):
        on = bool(self.var.get()); self.cv.delete('all')
        self.cv.create_rectangle(S(2), S(2), S(20), S(20), outline=C['accent'] if on else '#7d899d', fill=C['accent'] if on else 'white', width=2)
        if on: self.cv.create_text(S(11), S(11), text='\u2713', fill='white', font=(FONT, 11, 'bold'))

    def toggle(self, e=None):
        self.var.set(0 if self.var.get() else 1)
        if self.command: self.command()


class Radio(tk.Frame):
    """Round radio with a bold label that matches the card it sits on."""
    def __init__(self, parent, text, var, value, command=None, size=11):
        super().__init__(parent, bg=parent.cget('bg'), cursor='hand2')
        self.var, self.value, self.command = var, value, command
        self.cv = tk.Canvas(self, width=S(22), height=S(22), bg=self.cget('bg'), highlightthickness=0); self.cv.pack(side='left')
        self.lb = tk.Label(self, text=text, bg=self.cget('bg'), fg=C['text'], font=(FONT, size, 'bold'), anchor='w'); self.lb.pack(side='left', padx=(S(10), 0))
        for w in (self, self.cv, self.lb): w.bind('<Button-1>', self.pick)
        self.draw()

    def draw(self):
        on = self.var.get() == self.value; self.cv.delete('all')
        self.cv.create_oval(S(2), S(2), S(20), S(20), outline=C['accent'] if on else '#7d899d', fill='white', width=2)
        if on: self.cv.create_oval(S(6), S(6), S(16), S(16), outline='', fill=C['accent'])

    def pick(self, e=None):
        self.var.set(self.value)
        if self.command: self.command()


class App:
    def __init__(self, root):
        self.root = root
        root.title('Unofficial firmware patch for the Trust GXT 868 Torix')
        w, h = S(920), S(660)
        root.geometry(f'{w}x{h}+{max(0, (root.winfo_screenwidth() - w) // 2)}+{max(0, (root.winfo_screenheight() - h) // 3)}'); root.resizable(False, False)
        root.configure(bg=C['white'])
        self.style(); self.meta = T.load_patch()
        self.mode = tk.StringVar(value='install'); self.kbs, self.kb, self.sel = [], None, tk.IntVar(value=0)
        self.backup_path = self.image = self.restore_image = self.restore_file = None
        self.ok = tk.IntVar(value=0); self.service_stopped = False; self.busy = False; self.poll = None; self.steps = INSTALL_STEPS
        self.q = queue.Queue(); os.makedirs(T.log_dir(), exist_ok=True)
        self.log = open(os.path.join(T.log_dir(), f'installer_{datetime.datetime.now():%Y%m%d-%H%M%S}.log'), 'a', encoding='utf-8')
        # layout: sidebar | (content / footer)
        self.side = tk.Frame(root, bg=C['side'], width=S(250)); self.side.pack(side='left', fill='y'); self.side.pack_propagate(False)
        right = tk.Frame(root, bg=C['white']); right.pack(side='left', fill='both', expand=True)
        self.footer = tk.Frame(right, bg=C['footer']); self.footer.pack(side='bottom', fill='x')
        tk.Frame(right, bg=C['border'], height=1).pack(side='bottom', fill='x')
        self.body = tk.Frame(right, bg=C['white']); self.body.pack(fill='both', expand=True, padx=S(40), pady=(S(34), S(14)))
        self.brand()
        root.protocol('WM_DELETE_WINDOW', self.on_close); root.after(100, self.pump)
        self.page_disclaimer()

    # ---------------------------------------------------------------- look & feel
    def style(self):
        st = ttk.Style(); st.theme_use('clam')
        st.configure('.', font=(FONT, 10), background=C['white'], foreground=C['text'])
        st.configure('TButton', font=(FONT, 10), padding=(S(18), S(8)), background=C['white'], foreground=C['text'], bordercolor=C['border'], focuscolor=C['white'], relief='solid', borderwidth=1)
        st.map('TButton', background=[('active', C['card']), ('disabled', C['footer'])], foreground=[('disabled', '#9aa3b2')], bordercolor=[('focus', C['accent'])])
        st.configure('Primary.TButton', font=(FONT, 10, 'bold'), background=C['accent'], foreground='#ffffff', bordercolor=C['accent'], padding=(S(22), S(8)))
        st.map('Primary.TButton', background=[('active', C['accent_d']), ('disabled', '#a9bdea')], foreground=[('disabled', '#ffffff')], bordercolor=[('disabled', '#a9bdea')])
        st.configure('TCheckbutton', background=C['white'], font=(FONT, 10), focuscolor=C['white'])
        st.configure('Card.TRadiobutton', background=C['card'], font=(FONT, 11, 'bold'), focuscolor=C['card'])
        st.map('TCheckbutton', background=[('active', C['white'])]); st.map('Card.TRadiobutton', background=[('active', C['card'])])
        st.configure('Acc.Horizontal.TProgressbar', troughcolor='#e3e8f1', background=C['accent'], bordercolor='#e3e8f1', lightcolor=C['accent'], darkcolor=C['accent'], thickness=S(10))

    def brand(self):
        f = tk.Frame(self.side, bg=C['side']); f.pack(fill='x', padx=S(26), pady=(S(34), S(26)))
        cv = tk.Canvas(f, width=S(40), height=S(40), bg=C['side'], highlightthickness=0); cv.pack(anchor='w')
        cv.create_rectangle(0, 0, S(40), S(40), fill=C['accent'], outline=''); cv.create_text(S(20), S(20), text='T', fill='white', font=(FONT, 18, 'bold'))
        tk.Label(f, text='Torix Patch', bg=C['side'], fg='white', font=(FONT, 17, 'bold')).pack(anchor='w', pady=(S(12), 0))
        tk.Label(f, text=f'Unofficial firmware patch  •  v{VERSION}', bg=C['side'], fg=C['side_dim'], font=(FONT, 9)).pack(anchor='w')
        self.step_box = tk.Frame(self.side, bg=C['side']); self.step_box.pack(fill='x', padx=S(22))

    def step(self, name):
        for w in self.step_box.winfo_children(): w.destroy()
        idx = self.steps.index(name)
        for n, s in enumerate(self.steps):
            row = tk.Frame(self.step_box, bg=C['side']); row.pack(fill='x', pady=S(5))
            cv = tk.Canvas(row, width=S(26), height=S(26), bg=C['side'], highlightthickness=0); cv.pack(side='left')
            done, cur = n < idx, n == idx
            cv.create_oval(S(2), S(2), S(24), S(24), fill=C['ok'] if done else C['accent'] if cur else C['side'], outline=C['ok'] if done else C['accent'] if cur else C['side_dim'], width=1)
            cv.create_text(S(13), S(13), text='✓' if done else str(n + 1), fill='white' if (done or cur) else C['side_dim'], font=(FONT, 9, 'bold'))
            tk.Label(row, text=s, bg=C['side'], fg=C['side_fg'] if cur else (C['side_dim'] if not done else '#b8c4de'), font=(FONT, 10, 'bold' if cur else 'normal')).pack(side='left', padx=S(10))

    def clear(self):
        if self.poll: self.root.after_cancel(self.poll); self.poll = None
        for w in self.body.winfo_children(): w.destroy()

    # ---------------------------------------------------------------- building blocks
    def lab(self, parent, text, size=10, bold=False, fg=None, mono=False, wrap=None, **pk):
        if mono: text = '\n'.join(textwrap.wrap(text, 66, break_long_words=True, break_on_hyphens=False)) or text      # long paths get real line breaks
        l = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=fg or C['text'], anchor='w', justify='left', wraplength=wrap or S(565),
                     font=('Consolas' if mono else FONT, size, 'bold' if bold else 'normal')); l.pack(fill='x', **pk); return l

    def head(self, title, sub=None):
        self.lab(self.body, title, size=21, bold=True, pady=(0, S(4)))
        if sub: self.lab(self.body, sub, size=11, fg=C['muted'], pady=(0, S(14)))
        else: tk.Frame(self.body, bg=C['white'], height=S(10)).pack()

    def bullets(self, items, size=10):
        for t in items:
            r = tk.Frame(self.body, bg=C['white']); r.pack(fill='x', pady=0)
            tk.Label(r, text='•', bg=C['white'], fg=C['accent'], font=(FONT, size, 'bold'), pady=0).pack(side='left', anchor='n', padx=(S(2), S(10)))
            tk.Label(r, text=t, bg=C['white'], fg=C['text'], font=(FONT, size), justify='left', anchor='w', wraplength=S(530)).pack(side='left', fill='x')

    def callout(self, text, kind='bad', bold=True, pady=(S(12), S(8))):
        bg, fg, bar = {'bad': (C['red_bg'], C['red_fg'], C['bad']), 'warn': (C['amb_bg'], C['amb_fg'], C['warn']), 'ok': (C['grn_bg'], C['grn_fg'], C['ok']),
                       'info': (C['tint'], C['text'], C['accent'])}[kind]
        f = tk.Frame(self.body, bg=bg); f.pack(fill='x', pady=pady)
        tk.Frame(f, bg=bar, width=S(5)).pack(side='left', fill='y')
        tk.Label(f, text=text, bg=bg, fg=fg, anchor='w', justify='left', wraplength=S(515), font=(FONT, 10, 'bold' if bold else 'normal')).pack(side='left', fill='x', padx=S(16), pady=S(12))
        return f

    def card(self, selected=False, pady=S(6)):
        f = tk.Frame(self.body, bg=C['tint'] if selected else C['card'], highlightthickness=2 if selected else 1, highlightbackground=C['accent'] if selected else C['border'])
        f.pack(fill='x', pady=pady); inner = tk.Frame(f, bg=f.cget('bg')); inner.pack(fill='x', padx=S(16), pady=S(12)); return f, inner

    def icon(self, parent, kind):
        col = {'ok': C['ok'], 'bad': C['bad'], 'warn': C['warn']}[kind]
        cv = tk.Canvas(parent, width=S(22), height=S(22), bg=parent.cget('bg'), highlightthickness=0)
        cv.create_oval(S(1), S(1), S(21), S(21), fill=col, outline=''); cv.create_text(S(11), S(11), text={'ok': '✓', 'bad': '✕', 'warn': '!'}[kind], fill='white', font=(FONT, 10, 'bold'))
        return cv

    def status_row(self, kind, text, detail=None):
        r = tk.Frame(self.body, bg=C['white']); r.pack(fill='x', pady=S(5))
        self.icon(r, kind).pack(side='left', anchor='n', padx=(0, S(12)))
        col = tk.Frame(r, bg=C['white']); col.pack(side='left', fill='x')
        tk.Label(col, text=text, bg=C['white'], fg=C['text'], font=(FONT, 11), anchor='w').pack(fill='x')
        if detail: tk.Label(col, text=detail, bg=C['white'], fg=C['muted'], font=(FONT, 9), anchor='w', justify='left', wraplength=S(500)).pack(fill='x')

    def progress(self):
        self.pb = ttk.Progressbar(self.body, style='Acc.Horizontal.TProgressbar', mode='determinate', maximum=1000); self.pb.pack(fill='x', pady=(S(18), S(10)))
        self.pt = tk.Label(self.body, text='', bg=C['white'], fg=C['muted'], anchor='w', justify='left', wraplength=S(565), font=(FONT, 10)); self.pt.pack(fill='x')

    def nav(self, back=None, nxt=None, label='Next', enabled=True, left=(), cancel=True, primary=True):
        """Footer: extra actions on the left; Back, the main action and Cancel on the right."""
        for w in self.footer.winfo_children(): w.destroy()
        inner = tk.Frame(self.footer, bg=C['footer']); inner.pack(fill='x', padx=S(40), pady=S(16))
        for text, cmd in left: ttk.Button(inner, text=text, command=cmd).pack(side='left', padx=(0, S(8)))
        if cancel: ttk.Button(inner, text='Cancel', command=self.on_close).pack(side='right', padx=(S(8), 0))
        if nxt: ttk.Button(inner, text=label, command=nxt, state='normal' if enabled else 'disabled', style='Primary.TButton' if primary else 'TButton').pack(side='right')
        if back: ttk.Button(inner, text='Back', command=back).pack(side='right', padx=S(8))

    # ---------------------------------------------------------------- plumbing
    def note(self, text): self.log.write(f'{datetime.datetime.now():%H:%M:%S} {text}\n'); self.log.flush()

    def run(self, work, done):
        def target():
            try: self.q.put(('done', done, work()))
            except T.TorixError as e: self.q.put(('err', str(e)))
            except Exception as e: self.q.put(('err', f'Unexpected problem: {e!r}'))
        self.busy = True; threading.Thread(target=target, daemon=True).start()

    def report(self, stage, frac, text): self.q.put(('prog', frac, text))

    def pump(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == 'prog':
                    _, frac, text = m; self.note(text)
                    if hasattr(self, 'pt') and self.pt.winfo_exists():
                        self.pt.config(text=text)
                        if frac is not None: self.pb.stop(); self.pb.config(mode='determinate'); self.pb['value'] = int(frac * 1000)
                elif m[0] == 'done': self.busy = False; m[1](m[2])
                elif m[0] == 'err': self.busy = False; self.note('ERROR ' + m[1]); self.page_error(m[1])
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def on_close(self):
        if self.busy and not messagebox.askyesno('Please wait', 'The keyboard is being worked on. Closing now could leave it in update mode (recoverable with the restore option).\n\nClose anyway?'): return
        self.finish_env(); self.root.destroy()

    def finish_env(self):
        if self.service_stopped: T.start_lighting_service(); self.service_stopped = False

    def restart(self): self.page_welcome()

    # ---------------------------------------------------------------- 1. disclaimer
    def page_disclaimer(self):
        self.clear(); self.steps = INSTALL_STEPS; self.step('Disclaimer')
        self.head('Before you continue')
        self.lab(self.body, "This installs an unofficial patch for the firmware of the Trust GXT 868 Torix wireless mechanical gaming keyboard. "
                            "Not made, approved or supported by Trust.", size=11, pady=(0, S(14)))
        self.lab(self.body, "Improvements", size=11, bold=True, pady=(0, S(2)))
        self.bullets(SUMMARY)
        self.callout("May void your warranty.\n"
                     "A failed or interrupted install could leave your keyboard unusable.\n"
                     "Only for the Trust GXT 868 Torix. Do not use it on any other keyboard or model.\n"
                     "The authors accept no responsibility for any damage or problems.", 'bad', pady=(S(16), S(10)))
        Check(self.body, "I understand the risks and wish to proceed.", self.ok, self.disclaimer_refresh, size=11).pack(anchor='w')
        self.disclaimer_refresh()

    def disclaimer_refresh(self): self.nav(nxt=self.page_welcome, label='Continue', enabled=bool(self.ok.get()))

    # ---------------------------------------------------------------- 2. welcome (what to do)
    def page_welcome(self):
        if not self.ok.get(): self.page_disclaimer(); return          # the disclaimer cannot be skipped
        self.clear(); self.steps = INSTALL_STEPS; self.step('Welcome')
        self.head('What would you like to do?')
        for text, val, sub in (('Install the patch', 'install', 'Recommended. Your original firmware is backed up first.'),
                               ('Restore the original firmware', 'restore', 'Puts back a backup made by this installer, undoing the patch.')):
            f, inner = self.card(self.mode.get() == val)
            Radio(inner, text, self.mode, val, self.page_welcome_refresh).pack(anchor='w')
            tk.Label(inner, text=sub, bg=inner.cget('bg'), fg=C['muted'], font=(FONT, 10), anchor='w', justify='left', wraplength=S(500)).pack(fill='x', padx=(S(32), 0), pady=(S(4), 0))
            for w in (f, inner): w.bind('<Button-1>', lambda e, v=val: (self.mode.set(v), self.page_welcome_refresh()))
        self.nav(back=self.page_disclaimer, nxt=self.after_welcome, label='Continue')

    def page_welcome_refresh(self): self.page_welcome()

    def after_welcome(self):
        if self.mode.get() == 'restore': self.steps = RESTORE_STEPS; self.page_choose_backup()
        else: self.steps = INSTALL_STEPS; self.page_prepare()

    # ---------------------------------------------------------------- restore: choose backup
    def page_choose_backup(self):
        self.clear(); self.step('Choose backup'); self.head('Choose a backup', 'Saved by this installer in:')
        f, inner = self.card(); self.lab(inner, T.backup_dir(), size=9, mono=True, fg=C['text'])
        self.items = T.list_backups()
        box = tk.Frame(self.body, bg=C['border'], padx=1, pady=1); box.pack(fill='x', pady=S(10))
        self.lb = tk.Listbox(box, height=7, font=('Consolas', 10), activestyle='none', exportselection=False, relief='flat', bd=0, highlightthickness=0, selectbackground=C['accent_l'], selectforeground=C['text'])
        self.lb.pack(fill='x')
        for path, when, _ in self.items: self.lb.insert('end', f'  {when:%Y-%m-%d  %H:%M}    {os.path.basename(path)}')
        if not self.items: self.lb.insert('end', '  (none found - use Browse to pick a file)')
        else: self.lb.selection_set(0)
        self.restore_file = self.items[0][0] if self.items else None
        self.lb.bind('<<ListboxSelect>>', lambda e: setattr(self, 'restore_file', self.items[self.lb.curselection()[0]][0] if self.items and self.lb.curselection() else None) or self.pick_refresh())
        self.pick_refresh()

    def pick_refresh(self): self.nav(back=self.page_welcome, nxt=self.after_pick, label='Continue', enabled=bool(self.restore_file), left=[('Browse...', self.browse_backup)])

    def browse_backup(self):
        f = filedialog.askopenfilename(title='Choose a Torix firmware backup', initialdir=T.backup_dir() if os.path.isdir(T.backup_dir()) else None, filetypes=[('Torix firmware backup', '*.bin')])
        if f: self.restore_file = f; self.after_pick()

    def after_pick(self):
        try: self.restore_image = T.check_backup_file(self.restore_file, self.meta)
        except T.TorixError as e: messagebox.showerror('That backup cannot be used', str(e)); return
        self.page_prepare()

    # ---------------------------------------------------------------- prepare (both modes)
    def page_prepare(self):
        self.clear(); self.step('Prepare'); self.head('Prepare your keyboard', 'Do these three things, then continue.')
        self.prep = {}
        for key, num, label in (('dongle', 1, 'Unplug the keyboard\'s 2.4 GHz dongle'), ('switch', 2, 'Switch the keyboard\'s wireless switch off'), ('cable', 3, 'Connect the USB-C cable')):
            f, inner = self.card(); row = tk.Frame(inner, bg=C['card']); row.pack(fill='x')
            cv = tk.Canvas(row, width=S(30), height=S(30), bg=C['card'], highlightthickness=0); cv.pack(side='left', anchor='n', padx=(0, S(14)))
            cv.create_oval(S(2), S(2), S(28), S(28), fill=C['accent'], outline=''); cv.create_text(S(15), S(15), text=str(num), fill='white', font=(FONT, 11, 'bold'))
            col = tk.Frame(row, bg=C['card']); col.pack(side='left', fill='x')
            tk.Label(col, text=label, bg=C['card'], fg=C['text'], font=(FONT, 11, 'bold'), anchor='w').pack(fill='x')
            if key == 'dongle':
                self.dg = tk.IntVar(value=0)
                Check(col, 'The dongle is unplugged', self.dg, self.prepare_tick, bg=C['card']).pack(anchor='w', pady=(S(6), 0))
            elif key == 'switch':
                self.sw = tk.IntVar(value=0)
                Check(col, 'The wireless switch is off', self.sw, self.prepare_tick, bg=C['card']).pack(anchor='w', pady=(S(6), 0))
            else:
                st = tk.Label(col, text='', bg=C['card'], font=(FONT, 10), anchor='w'); st.pack(fill='x', pady=(S(2), 0)); self.prep[key] = st
        self.lab(self.body, 'Checked again right before anything is written.', size=9, fg=C['muted'], pady=(S(10), 0))
        self.prepare_tick()

    def prepare_tick(self):
        if not self.prep['cable'].winfo_exists(): return          # page was left
        if self.poll: self.root.after_cancel(self.poll)           # one refresh chain only (every checkbox click also calls this)
        cable = T.cable_connected_quiet()
        self.prep['cable'].config(text='✓  Keyboard detected' if cable else '✕  No keyboard detected', fg=C['ok'] if cable else C['bad'])
        self.nav(back=self.page_welcome if self.mode.get() == 'install' else self.page_choose_backup, nxt=self.page_find, label='Next',
                 enabled=cable and bool(self.dg.get()) and bool(self.sw.get()))
        self.poll = self.root.after(1000, self.prepare_tick)

    # ---------------------------------------------------------------- find
    def page_find(self):
        if T.wired_blockers(): self.page_prepare(); return
        self.clear(); self.step('Find keyboard'); self.head('Looking for your keyboard')
        self.progress(); self.pt.config(text='Searching ...'); self.pb.config(mode='indeterminate'); self.pb.start(12)
        self.nav(cancel=True)
        def work():
            if T.trust_app_running(): subprocess.run(['taskkill', '/F', '/IM', 'DeviceDriver.exe'], capture_output=True, creationflags=T.NO_WINDOW); time.sleep(1)
            if T.in_bootloader(): return 'bootloader'
            kbs = T.find_keyboards()
            self.service_stopped = T.stop_lighting_service() or self.service_stopped
            for k in kbs: T.identify(k, self.meta, self.report)
            return kbs
        self.run(work, self.found)

    def found(self, kbs):
        if hasattr(self, 'pb') and self.pb.winfo_exists(): self.pb.stop()
        self.clear(); self.step('Find keyboard'); restore = self.mode.get() == 'restore'
        if kbs == 'bootloader':
            self.kb = None; self.head('Keyboard in update mode', 'An earlier update was interrupted. The keyboard is not damaged.')
            if restore: self.nav(back=self.page_prepare, nxt=self.page_confirm_restore, label='Continue')
            else: self.callout('Choose "Restore the original firmware" to put your backup back.', 'info'); self.nav(nxt=self.restart, label='Start over')
            return
        self.kbs = kbs
        self.head('Choose your keyboard' if len(kbs) > 1 else 'Keyboard found' if kbs else 'No keyboard found')
        if not kbs:
            self.callout('No keyboard found. Connect the USB-C cable, switch wireless off, unplug the dongle, then rescan.', 'warn', bold=False)
            self.nav(back=self.page_prepare, nxt=self.page_find, label='Rescan'); return
        usable = False
        self.sel.set(next((i for i, k in enumerate(kbs) if k.status == 'ok' or restore), 0))
        for i, k in enumerate(kbs):
            ok = k.status == 'ok' or (restore and k.status in ('patched', 'unsupported', 'silent'))
            tag, col = {'ok': ('Ready to patch', C['ok']), 'patched': ('Already patched', C['warn']), 'unsupported': ('Firmware not supported', C['bad']), 'silent': ('Not responding', C['bad'])}[k.status]
            f, inner = self.card(self.sel.get() == i)
            top = tk.Frame(inner, bg=inner.cget('bg')); top.pack(fill='x')
            Radio(top, k.label, self.sel, i, self.found_refresh).pack(side='left')
            tk.Label(top, text=tag, bg=inner.cget('bg'), fg=col, font=(FONT, 10, 'bold')).pack(side='right')
            tk.Label(inner, text=k.detail, bg=inner.cget('bg'), fg=C['muted'] if ok else C['bad'], font=(FONT, 10), anchor='w', justify='left', wraplength=S(500)).pack(fill='x', padx=(S(32), 0), pady=(S(4), 0))
            usable = usable or ok
        if len(kbs) > 1: self.lab(self.body, 'Not sure which is which? Unplug the others and rescan.', size=9, fg=C['muted'], pady=(S(8), 0))
        self._found_args = (kbs, usable, restore); self.found_nav()

    def found_nav(self):
        kbs, usable, restore = self._found_args
        self.nav(back=self.page_prepare, nxt=self.page_confirm_restore if restore else self.page_checks, label='Next', enabled=usable, left=[('Rescan', self.page_find)])

    def found_refresh(self):
        kbs = self._found_args[0]; self.clear(); self.step('Find keyboard'); self.found(kbs)

    # ---------------------------------------------------------------- install: checks, backup, install
    def page_checks(self):
        self.kb = self.kbs[self.sel.get()]
        if self.kb.status != 'ok': messagebox.showinfo('Cannot continue', self.kb.detail); return
        self.clear(); self.step('Checks'); self.head('Checking everything is ready')
        rows = [('Keyboard answers over USB (wired mode)', True), ('Supported firmware', True),
                ('Trust keyboard app closed', not T.trust_app_running()), ('Aura lighting service paused', T.lighting_service() != 'RUNNING'), ('OpenRGB closed', not T.openrgb_running()), ('Administrator rights', T.is_admin())]
        for label, ok in rows: self.status_row('ok' if ok else 'bad', label)
        self.nav(back=self.page_find, nxt=self.page_backup, label='Next', enabled=all(ok for _, ok in rows), left=[('Recheck', self.page_checks)])

    def page_backup(self):
        self.clear(); self.step('Backup'); self.head('Backing up your firmware', 'Read-only: nothing on the keyboard is changed.')
        self.lab(self.body, 'Saving to:', size=10, fg=C['muted'], pady=(S(2), S(4)))
        f, inner = self.card(pady=(0, 0)); self.lab(inner, T.backup_dir(), size=9, mono=True)
        self.progress(); self.nav(nxt=None, cancel=False)
        def work():
            path, img = T.backup(self.kb, self.meta, self.report)
            T.apply_patches(img, self.meta)          # prove the patch fits (firmware + surroundings match) before anything is written
            return path, img
        self.run(work, self.backup_done)

    def backup_done(self, res):
        self.backup_path, self.image = res; self.pb.pack_forget(); self.pt.pack_forget()
        self.callout('Backup saved and verified. Keep it safe: it is the only copy of your original firmware.', 'ok', bold=False, pady=(S(10), S(6)))
        f, inner = self.card(pady=(0, 0)); self.lab(inner, self.backup_path, size=9, mono=True)
        self.nav(nxt=self.page_install, label='Next', left=[('Open backup folder', lambda: os.startfile(T.backup_dir()))])

    def page_install(self):
        self.clear(); self.step('Install'); self.head('Ready to install', 'The patch matches your keyboard\'s firmware.')
        self.lab(self.body, 'Improvements', size=11, bold=True, pady=(0, S(2)))
        self.bullets(SUMMARY)
        self.callout('The keyboard restarts. Do not unplug it for about a minute.', 'warn', pady=(S(16), 0))
        self.nav(back=self.page_backup_info, nxt=self.do_install, label='Install now')

    def page_backup_info(self): self.page_install()

    def do_install(self):
        self.clear(); self.step('Install'); self.head('Installing', 'Do not unplug the keyboard.'); self.progress(); self.nav(cancel=False)
        def work():
            self.report('flash', 0.0, 'Re-checking the keyboard one last time ...')
            T.final_verify(self.kb, self.meta)                     # same keyboard, wired, no dongle, still supported + stock
            return T.flash(self.kb, T.apply_patches(self.image, self.meta), self.report)
        self.run(work, self.install_done)

    def install_done(self, back):
        # optional last steps, each offered only when the program is found and its plug-in file is bundled
        self.extra = ([('Aura plug-in', self.page_aura)] if T.aura_status() == 'ready' else []) + ([('OpenRGB plug-in', self.page_openrgb)] if T.openrgb_status() == 'ready' else [])
        offer = bool(self.extra)
        self.steps = INSTALL_STEPS + [n for n, _ in self.extra]
        self.clear(); self.step('Done'); cv = tk.Canvas(self.body, width=S(56), height=S(56), bg=C['white'], highlightthickness=0); cv.pack(anchor='w', pady=(0, S(10)))
        cv.create_oval(S(2), S(2), S(54), S(54), fill=C['ok'], outline=''); cv.create_text(S(28), S(28), text='✓', fill='white', font=(FONT, 24, 'bold'))
        self.head('Patch installed', 'Switch wireless back on and plug the dongle in to use the keyboard wirelessly. Plugging in the cable switches to USB.')
        if not back: self.callout('The keyboard has not reappeared yet. Wait a few seconds or replug it.', 'warn', bold=False)
        self.lab(self.body, 'Backup of your original firmware (choose Restore to undo the patch):', size=10, fg=C['muted'], pady=(S(4), S(4)))
        f, inner = self.card(pady=(0, S(12))); self.lab(inner, self.backup_path or '', size=9, mono=True)
        self.nav(nxt=self.next_extra if offer else self.finish, label='Continue' if offer else 'Finish', cancel=False, left=[('Open backup folder', lambda: os.startfile(T.backup_dir()))])

    # ---------------------------------------------------------------- optional last steps (Aura, OpenRGB)
    def next_extra(self, cur=None):
        names = [n for n, _ in self.extra]; i = names.index(cur) + 1 if cur else 0
        (self.extra[i][1] if i < len(names) else self.finish)()

    def extra_nav(self, cur, **kw):
        more = [n for n, _ in self.extra].index(cur) + 1 < len(self.extra)
        self.nav(nxt=lambda: self.next_extra(cur), label='Continue' if more else 'Finish', cancel=False, **kw)

    def page_aura(self):
        self.clear(); self.step('Aura plug-in'); self.head('ASUS Aura Sync plug-in', 'Optional. The firmware patch is already installed and does not need it.')
        self.bullets(['Adds the keyboard to Aura Sync in Armoury Crate, with per-key lighting.',
                      "Installs a small plug-in for ASUS's lighting service and restarts that service.",
                      'To remove it later, run uninstall_aura_plugin.bat from the downloaded folder.'])
        self.nav(nxt=self.do_aura, label='Install plug-in', cancel=False, left=[('Skip', lambda: self.next_extra('Aura plug-in'))])

    def do_aura(self):
        self.clear(); self.step('Aura plug-in'); self.head('Installing the Aura plug-in', 'This takes a few seconds.'); self.progress()
        self.pb.config(mode='indeterminate'); self.pb.start(12); self.pt.config(text='Registering the plug-in ...'); self.nav(cancel=False)
        def work():
            try: T.install_aura_plugin(); return None
            except T.TorixError as e: return str(e)
        self.run(work, self.aura_done)

    def aura_done(self, err):
        if hasattr(self, 'pb') and self.pb.winfo_exists(): self.pb.stop()
        self.clear(); self.step('Aura plug-in')
        if err:
            self.head('The plug-in was not installed'); self.callout(err, 'warn', bold=False, pady=(S(4), S(10)))
            self.lab(self.body, 'The keyboard firmware patch is installed and is not affected.', size=10, fg=C['muted'])
            self.extra_nav('Aura plug-in', left=[('Try again', self.do_aura)]); return
        self.head('Plug-in installed', 'Open Armoury Crate, go to Aura Sync and tick "Trust GXT 868 Torix".')
        self.lab(self.body, 'If the keyboard is not listed, restart Armoury Crate once.', size=10, fg=C['muted'])
        self.extra_nav('Aura plug-in')

    def page_openrgb(self):
        self.clear(); self.step('OpenRGB plug-in'); self.head('OpenRGB plug-in', 'Optional. The firmware patch is already installed and does not need it.')
        self.bullets(['Adds the keyboard to OpenRGB (version 1.0 or later) with per-key lighting, as a normal device.',
                      "Copies one file, %s, into OpenRGB's plugins folder (%s). Nothing else changes." % (T.OPENRGB_PLUGIN, os.path.join(T.openrgb_dir(), 'plugins')),
                      'Works with the keyboard on its USB cable. To remove it later, delete that file (or run uninstall_openrgb_plugin.bat).'])
        self.nav(nxt=self.do_openrgb, label='Install plug-in', cancel=False, left=[('Skip', lambda: self.next_extra('OpenRGB plug-in'))])

    def do_openrgb(self):
        def work():
            try: T.install_openrgb_plugin(); return None
            except T.TorixError as e: return str(e)
        self.clear(); self.step('OpenRGB plug-in'); self.head('Installing the OpenRGB plug-in', 'This takes a moment.'); self.nav(cancel=False)
        self.run(work, self.openrgb_done)

    def openrgb_done(self, err):
        self.clear(); self.step('OpenRGB plug-in')
        if err:
            self.head('The plug-in was not installed'); self.callout(err, 'warn', bold=False, pady=(S(4), S(10)))
            self.lab(self.body, 'The keyboard firmware patch is installed and is not affected.', size=10, fg=C['muted'])
            self.extra_nav('OpenRGB plug-in', left=[('Try again', self.do_openrgb)]); return
        self.head('Plug-in installed', 'Start (or restart) OpenRGB. "Trust GXT 868 Torix" appears in its device list while the keyboard is connected by USB cable.')
        self.lab(self.body, "For animated effects, add OpenRGB's Effects plugin (see the README).", size=10, fg=C['muted'])
        self.extra_nav('OpenRGB plug-in')

    def finish(self):
        self.finish_env(); self.root.destroy()

    # ---------------------------------------------------------------- restore: confirm + flash
    def page_confirm_restore(self):
        self.kb = None if T.in_bootloader() else self.kbs[self.sel.get()]
        self.clear(); self.step('Restore'); self.head('Ready to restore')
        self.lab(self.body, 'Restoring from:', size=10, fg=C['muted'], pady=(S(2), S(4)))
        f, inner = self.card(pady=(0, S(8))); self.lab(inner, self.restore_file or '', size=9, mono=True)
        self.callout('The keyboard restarts. Do not unplug it for about a minute.', 'warn', pady=(S(10), 0))
        self.nav(back=self.page_find, nxt=self.do_restore, label='Restore now')

    def do_restore(self):
        self.clear(); self.step('Restore'); self.head('Restoring', 'Do not unplug the keyboard.'); self.progress(); self.nav(cancel=False)
        def work():
            if self.kb is not None: T.final_verify_restore(self.kb)
            elif T.wired_blockers(): raise T.TorixError(T.wired_blockers()[0])
            return T.flash(self.kb, self.restore_image, self.report)
        def done(back):
            self.clear(); self.step('Done'); cv = tk.Canvas(self.body, width=S(56), height=S(56), bg=C['white'], highlightthickness=0); cv.pack(anchor='w', pady=(0, S(10)))
            cv.create_oval(S(2), S(2), S(54), S(54), fill=C['ok'], outline=''); cv.create_text(S(28), S(28), text='✓', fill='white', font=(FONT, 24, 'bold'))
            self.head('Original firmware restored', 'Switch wireless back on and plug the dongle in to use the keyboard wirelessly.')
            if not back: self.callout('The keyboard has not reappeared yet. Wait a few seconds or replug it.', 'warn', bold=False)
            self.nav(nxt=self.finish, label='Close', cancel=False)
        self.run(work, done)

    # ---------------------------------------------------------------- errors
    def page_error(self, msg):
        self.clear(); self.head('Stopped')
        self.callout(msg, 'bad', bold=False, pady=(S(4), S(10)))
        self.lab(self.body, 'If an update was interrupted, start again and choose "Restore the original firmware".', size=10, fg=C['muted'], pady=(S(4), S(4)))
        self.nav(nxt=self.restart, label='Start over', left=[('Open log folder', lambda: os.startfile(T.log_dir()))])


def main():
    global S
    elevate_if_needed()
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)         # crisp text on high-DPI screens
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass
    root = tk.Tk()
    scale = max(1.0, root.winfo_fpixels('1i') / 96.0)
    root.tk.call('tk', 'scaling', scale * 96 / 72)
    S = lambda px: int(px * scale)
    App(root); root.mainloop()


if __name__ == '__main__':
    main()
