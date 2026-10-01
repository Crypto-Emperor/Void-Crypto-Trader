#!/usr/bin/env bash
# ============================================================================
#   VOID CRYPTO TRADER - the only launcher you need.  (Linux / macOS)
#
#   ./Void.sh                 -> web dashboard (recommended)
#   ./Void.sh trade           -> run the bot in this terminal
#   ./Void.sh status          -> portfolio snapshot
#   ./Void.sh gui             -> desktop chart terminal (Tkinter)
#   ./Void.sh menu            -> interactive launcher menu
#   ./Void.sh build           -> build a single-file executable (PyInstaller)
#
#   First run: finds Python, installs dependencies (one time), then launches.
#   Windows users: double-click Void.bat instead. Same commands, same app.
# ============================================================================
set -u

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/VoidCryptoTrader-Release/app"
REQ="$APP/requirements.txt"
MARKER="$APP/.void_deps_ok"

B=$'\033[1;34m'; C=$'\033[0m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'

banner() {
    echo
    echo "  ${B}====  V O I D   T R A D E  ==============================${C}"
    echo "       paper-first crypto trader  |  freqtrade-inspired"
    echo "  ${B}==========================================================${C}"
    echo
}

find_python() {
    PY=""
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then
            PY="$c"; return 0
        fi
    done
    return 1
}

install_python() {
    echo "  ${Y}[!]${C} Python was not found."
    if command -v apt-get >/dev/null 2>&1; then
        echo "      Try: sudo apt-get install -y python3 python3-pip"
    elif command -v dnf >/dev/null 2>&1; then
        echo "      Try: sudo dnf install -y python3 python3-pip"
    elif command -v pacman >/dev/null 2>&1; then
        echo "      Try: sudo pacman -S --noconfirm python python-pip"
    elif command -v brew >/dev/null 2>&1; then
        echo "      Try: brew install python"
    fi
    return 1
}

ensure_deps() {
    [ -f "$MARKER" ] && return 0
    echo "  ${Y}[*]${C} Installing dependencies (first run only, about a minute)..."
    if ! "$PY" -m pip install --quiet --disable-pip-version-check -r "$REQ"; then
        echo "  ${Y}[!]${C} Pip failed - retrying with --user..."
        if ! "$PY" -m pip install --quiet --disable-pip-version-check --user -r "$REQ"; then
            echo "  ${R}[X]${C} Dependency install failed. Common fixes:"
            echo "      - Check your internet connection"
            echo "      - Run: $PY -m pip install --upgrade pip"
            echo "      - Or manually: $PY -m pip install -r $REQ"
            return 1
        fi
    fi
    : > "$MARKER"
    echo "  ${G}[OK]${C} Dependencies installed."
    echo
    return 0
}

run_web() {
    echo "  ${G}[+]${C} Launching the VoidTrade dashboard - your browser will open shortly."
    echo "      Keep this terminal open while trading. Ctrl+C to stop."
    echo
    ( cd "$APP" && exec "$PY" voidtrade.py web )
}

run_trade()  { ( cd "$APP" && exec "$PY" voidtrade.py trade ); }
run_status() { ( cd "$APP" && exec "$PY" voidtrade.py status ); }
run_gui()    { ( cd "$APP" && exec "$PY" gui_app.py ); }

build_exe() {
    local root os arch out
    root="$(cd "$APP/.." && pwd)"
    os="$(uname -s | tr 'A-Z' 'a-z')"
    case "$os" in linux*) os=linux ;; darwin*) os=macos ;; *) os="$os" ;; esac
    arch="$(uname -m)"; case "$arch" in x86_64) arch=x64 ;; arm64|aarch64) arch=arm ;; esac
    out="VoidCryptoTrader-$os-$arch"

    echo "  ${Y}[*]${C} Making sure PyInstaller is available..."
    "$PY" -m pip install --quiet --disable-pip-version-check pyinstaller || {
        echo "  ${R}[X]${C} Could not install pyinstaller."; return 1; }

    echo "  ${Y}[*]${C} Building one-file executable: $out"
    ( cd "$APP" && "$PY" -m PyInstaller --noconfirm --onefile \
        --name "$out" --distpath "$root/dist" void_launcher.py ) || {
        echo "  ${R}[X]${C} Build failed. Scroll up for the error message."; return 1; }

    cp -f "$root/dist/$out" "$(dirname "$APP")/../$out" 2>/dev/null || true
    echo
    echo "  ${G}[OK]${C} Done! ./$out now sits in the repo folder."
    echo "        Run it any time - no Python needed to RUN it."
}

show_menu() {
    echo "     ----------------------------------------------"
    echo "      [1] Web dashboard         (recommended)"
    echo "      [2] Trading desk          (terminal bot)"
    echo "      [3] Portfolio status"
    echo "      [4] Desktop charts        (Tkinter)"
    echo "      [5] Build single-file executable"
    echo "      [0] Exit"
    echo "     ----------------------------------------------"
    read -rp "    Pick one: " pick
    case "$pick" in
        1) run_web ;; 2) run_trade ;; 3) run_status ;;
        4) run_gui ;; 5) build_exe ;; 0|q) exit 0 ;;
        *) echo "  ${R}[X]${C} Invalid choice." ; exit 1 ;;
    esac
}

main() {
    banner
    find_python || install_python || exit 1
    echo "  ${G}[OK]${C} Python found: $PY"
    echo
    ensure_deps || exit 1

    local cmd="${1:-web}"
    case "$cmd" in
        trade)  run_trade ;;
        status) run_status ;;
        gui)    run_gui ;;
        menu)   show_menu ;;
        build)  build_exe ;;
        web|"") run_web ;;
        *) echo "usage: $0 [web|trade|status|gui|menu|build]"; exit 1 ;;
    esac
}

main "$@"
