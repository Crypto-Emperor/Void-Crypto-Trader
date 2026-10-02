# Building a real Windows installer with Inno Setup

This turns Void Crypto Trader into a normal **Setup exe** — the person you
send it to double-clicks it, gets Start Menu + Desktop shortcuts, and never
sees a browser, IP address or port. The app itself is the native Tkinter
desktop program (`gui_app.py`), packaged as one `VoidCryptoTrader.exe`.

```
VoidCryptoTrader-Release\
├── app\                      the Python app (bundled into the exe)
├── build_installer.bat       ONE-CLICK: builds exe + installer
└── installer\
    ├── VoidCryptoTrader.iss  Inno Setup script (edit name/version here)
    ├── VoidCryptoTrader.ico  app icon (used by exe + installer + shortcuts)
    ├── files\                put VoidCryptoTrader.exe here before compiling
    └── Output\               final installer lands here after compiling
```

## What you need (one time, on your Windows PC)

1. **Python 3.10+** — from python.org; tick *"Add python.exe to PATH"*.
2. **Inno Setup 6** — free: <https://jrsoftware.org/isdl.php> (defaults are fine).

## The easy way (recommended)

Double-click **`build_installer.bat`** (or run `Void.bat` → menu → option 6).

It automatically:

1. installs PyInstaller + the app's dependencies,
2. builds `VoidCryptoTrader.exe` (`--onefile --windowed`, with the Void icon —
   no console box, opens straight into the desktop app),
3. stages the exe into `installer\files\`,
4. compiles `VoidCryptoTrader.iss` with Inno Setup.

Result:

```
installer\Output\VoidCryptoTrader-Setup-1.4.1.exe   ← send/distribute this
```

If Inno Setup isn't installed yet, the script opens its download page for
you; the exe is already built at that point, so afterwards just re-run the
bat (or double-click the `.iss` file in the Inno Setup Compiler).

## The manual way

```bat
:: 1) build the exe
cd VoidCryptoTrader-Release\app
py -3 -m pip install pyinstaller -r requirements.txt
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
   --name VoidCryptoTrader --icon ..\installer\VoidCryptoTrader.ico ^
   void_launcher.py
copy dist\VoidCryptoTrader.exe ..\installer\files\

:: 2) compile the installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ..\installer\VoidCryptoTrader.iss
```

## What the installer does

- Installs to `C:\Program Files\VoidCryptoTrader` (per-user if no admin).
- Creates **Start Menu → Void Crypto Trader** and an optional **Desktop** icon.
- Seeds `keys\`, config and portfolio state next to the exe (existing user
  data is never overwritten — those files use `onlyifdoesntexist`).
- Offers "Launch Void Crypto Trader" at the end of setup.
- Registers a normal entry in *Apps & Features* for clean uninstall
  (your `keys\api_keys.env` is kept even after uninstall).

## Changing version / name / icon

- Version and names: edit the `#define` lines at the top of
  `installer\VoidCryptoTrader.iss` (keep `AppVersion` in sync with
  `app\VERSION`).
- Icon: replace `installer\VoidCryptoTrader.ico` (any 256×256 .ico works);
  rebuild so PyInstaller picks up the new icon.

## Troubleshooting

| Problem | Fix |
|---|---|
| `PyInstaller failed` | Close any running `VoidCryptoTrader.exe`, re-run the bat. Antivirus sometimes locks `dist\` — add an exclusion. |
| `Inno was not found` | Install Inno Setup 6, then re-run. Or double-click the `.iss` directly. |
| Installer complains `files\VoidCryptoTrader.exe` missing | Run `build_installer.bat` (it stages the exe), or copy `app\dist\VoidCryptoTrader.exe` into `installer\files\` yourself. |
| Charts don't render in the installed app | Matplotlib is optional — the GUI falls back to canvas-drawn candles. To force bundle it: add `--collect-all matplotlib` to the PyInstaller line in `build_installer.bat` (makes the exe bigger). |
| SmartScreen warning when sharing | Normal for unsigned exes; click *More info → Run anyway*, or buy a code-signing cert for wide distribution. |
