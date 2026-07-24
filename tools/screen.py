"""Read the guest's text-mode screen out of video memory under the debugger.

REISPLAN.EXE writes straight to video RAM, so `log console` never sees it. Dumping
B800:0000 (80x25 cells of char+attribute) gives the planner's own screen as text --
the anchor for checking that an AUTOTYPE'd query actually landed, and ground truth
for validating decoded timetable data.

Usage:  python3 tools/screen.py ["autotype key sequence"]
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work")
DUMP = os.path.join(REPO, "MEMDUMP.BIN")
DG_BASE, DG_COUNT, DG_STATTBL = 0x1A80, 0x1AF4, 0x1AFC
N_RECORDS = 469
COLS, ROWS = 80, 25

KEYS = sys.argv[1] if len(sys.argv) > 1 else (
    "a m s t e r d a m space c s tab u t r e c h t space c s tab 0 9 0 0 f9")

master = proc = None
buf = b""


def drain(t=0.3):
    global buf
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([master], [], [], max(0, end - time.time()))
        if not r:
            break
        try:
            c = os.read(master, 65536)
        except OSError:
            break
        if not c:
            break
        buf += c
    return buf


def send(s):
    os.write(master, s.encode() if isinstance(s, str) else s)


def expect(pat, timeout=90):
    pat = pat.encode() if isinstance(pat, str) else pat
    end = time.time() + timeout
    while time.time() < end:
        if pat in buf:
            return True
        drain(0.3)
    return False


def memdump(addr, length, wait=6.0):
    if os.path.exists(DUMP):
        os.unlink(DUMP)
    send(f"MEMDUMPBIN {addr} {length:X}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        if os.path.exists(DUMP) and os.path.getsize(DUMP) >= length:
            time.sleep(0.15)
            with open(DUMP, "rb") as f:
                return f.read()
    return None


def render(vram):
    """char+attr cells -> list of text rows (cp437, control chars blanked)."""
    out = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            ch = vram[(r * COLS + c) * 2]
            row.append(chr(ch) if 32 <= ch < 127 else
                       bytes([ch]).decode("cp437", "replace") if ch >= 32 else " ")
        out.append("".join(row).rstrip())
    return out


def main():
    global master, proc
    os.makedirs(WORK, exist_ok=True)
    master_fd, slave = pty.openpty()
    globals()["master"] = master_fd
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "tools/dosbox-x", "-conf", "tools/dosbox-x.conf",
         "-break-start", "-set", "cpu cycles=fixed 50000",
         "-c", f"AUTOTYPE -w 12 -p 0.2 {KEYS}",
         "-c", "REISPLAN.EXE"],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"), close_fds=True)
    os.close(slave)

    if not expect("TYPE HELP", 90):
        print("!! no debugger prompt"); return 1
    send("BPINT 16\r"); drain(1.0)
    print(f"[+] debugger up; AUTOTYPE = {KEYS}")

    for attempt in range(1, 400):
        dg = memdump(f"ds:{DG_BASE:X}", 0x100, wait=4.0)
        if dg is None or len(dg) < 0x100:
            send("\x1b[15~"); time.sleep(0.2); continue
        if struct.unpack_from("<H", dg, DG_COUNT - DG_BASE)[0] == N_RECORDS and \
           struct.unpack_from("<H", dg, DG_STATTBL - DG_BASE + 2)[0]:
            print(f"[+] program loaded (after {attempt} breaks)")
            break
        send("\x1b[15~"); time.sleep(0.2)
    else:
        print("!! never reached loaded state"); return 1

    # Typing must happen while the guest runs FREE: with BPINT 16 armed the guest
    # is stopped at nearly every keyboard poll and AUTOTYPE's keystrokes are
    # dropped (only the first few land). So clear all breakpoints, resume, and
    # leave it alone for the whole AUTOTYPE window.
    send("BPDEL *\r"); drain(1.0)
    print("[+] breakpoints cleared; running free while AUTOTYPE types ...")
    send("\x1b[15~")
    time.sleep(float(os.environ.get("FREERUN", "60")))

    # Regain control: re-arm an INT 16 breakpoint, which the idle UI hits at once.
    print("[*] re-arming BPINT 16 to regain control ...")
    send("BPINT 16\r")
    vram = None
    for _ in range(20):
        drain(0.5)
        vram = memdump("B800:0000", COLS * ROWS * 2, wait=6.0)
        if vram is not None:
            break
    if vram is None:
        print("!! could not regain control of the guest")
        with open(os.path.join(WORK, "screens.txt"), "wb") as f:
            f.write(buf)
        return 2

    rows = render(vram)
    print("\n===== guest screen after the query =====")
    for r in rows:
        if r.strip():
            print("  " + r)
    with open(os.path.join(WORK, "screens.txt"), "w") as f:
        f.write("\n".join(rows))
    print(f"\n[*] screen -> {WORK}/screens.txt")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    sys.exit(rc)
