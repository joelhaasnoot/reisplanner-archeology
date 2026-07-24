"""Run a real query in REISPLAN.EXE and capture the planner's own answer screen,
so the decoded timetable can be checked against the program that shipped in 1990.

The debugger console only accepts commands while the guest is STOPPED, and the
guest must run FREE while AUTOTYPE types (an armed INT 16 breakpoint eats the
keystrokes). So every breakpoint has to be armed before the free run. The trick
is a two-stage break:

  1. BP at the result-display routine -- fires only when a search SUCCEEDS,
     which regains control without disturbing the typing.
  2. With control back, arm BPINT 16 and resume: the finished result screen
     waits for a keypress, so the next INT 16 stops the guest with the answer
     already drawn in video memory.

Usage:  python3 tools/verify_query.py ["autotype key sequence"]
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work")
DUMP = os.path.join(REPO, "MEMDUMP.BIN")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]")

DG_BASE, DG_COUNT, DG_STATTBL = 0x1A80, 0x1AF4, 0x1AFC
N_RECORDS = 469
DGROUP_FRAME = 0x135A
RESULT_DISPLAY = 0x1F41        # module offset, frame-0 segment
COLS, ROWS = 80, 25

# The LEADING SPACE dismisses "Alles ingelezen. Druk op een toets."; without it
# the first real keystroke is eaten and the station name is corrupted.
KEYS = sys.argv[1] if len(sys.argv) > 1 else (
    "space a m s t e r d a m space c s tab u t r e c h t space c s tab 0 9 0 0 f9")
# Optional follow-up keys pressed once the answer is on screen (e.g. F8 for the
# price screen), issued as a SECOND AUTOTYPE with a long initial wait -- the
# debugger console cannot inject keys, so everything must be queued up front.
KEYS2 = sys.argv[2] if len(sys.argv) > 2 else None
# Optional substring to wait for before capturing, so we grab the follow-up
# screen rather than the result screen it replaced.
WANT = sys.argv[3] if len(sys.argv) > 3 else None

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


def memdump(addr, length, dest=None, wait=6.0):
    """None means the guest is running -- the console ignores commands then."""
    if os.path.exists(DUMP):
        os.unlink(DUMP)
    send(f"MEMDUMPBIN {addr} {length:X}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        if os.path.exists(DUMP) and os.path.getsize(DUMP) >= length:
            time.sleep(0.15)
            with open(DUMP, "rb") as f:
                data = f.read()
            if dest:
                with open(os.path.join(WORK, dest), "wb") as f:
                    f.write(data)
            return data
    return None


def ev(expr, wait=3.0):
    marker = f"EV of '{expr.upper()}' is:"
    before = ANSI.sub(b"", buf).count(marker.encode())
    send(f"EV {expr}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        txt = ANSI.sub(b"", buf).decode("utf-8", "replace")
        if txt.count(marker) > before:
            m = re.search(r"[0-9A-Fa-f]+", txt.rsplit(marker, 1)[1])
            if m:
                return int(m.group(0), 16)
    return None


def render(vram):
    out = []
    for r in range(ROWS):
        row = "".join(chr(vram[(r * COLS + c) * 2])
                      if 32 <= vram[(r * COLS + c) * 2] < 127 else " "
                      for c in range(COLS))
        out.append(row.rstrip())
    return out


def main():
    global master, proc
    os.makedirs(WORK, exist_ok=True)
    master_fd, slave = pty.openpty()
    globals()["master"] = master_fd
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    cmds = ["-c", f"AUTOTYPE -w 25 -p 0.25 {KEYS}"]
    if KEYS2:
        cmds += ["-c", f"AUTOTYPE -w 120 -p 0.5 {KEYS2}"]
    cmds += ["-c", "REISPLAN.EXE"]
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "tools/dosbox-x", "-conf", "tools/dosbox-x.conf",
         "-break-start", "-set", "cpu cycles=fixed 50000"] + cmds,
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"), close_fds=True)
    os.close(slave)

    if not expect("TYPE HELP", 90):
        print("!! no debugger prompt"); return 1
    send("BPINT 16\r"); drain(1.0)
    print(f"[+] debugger up; query = {KEYS}")

    for attempt in range(1, 400):
        dg = memdump(f"ds:{DG_BASE:X}", 0x100, wait=4.0)
        if dg is None or len(dg) < 0x100:
            send("\x1b[15~"); time.sleep(0.2); continue
        if struct.unpack_from("<H", dg, DG_COUNT - DG_BASE)[0] == N_RECORDS and \
           struct.unpack_from("<H", dg, DG_STATTBL - DG_BASE + 2)[0]:
            print(f"[+] schedule loaded (after {attempt} breaks)")
            break
        send("\x1b[15~"); time.sleep(0.2)
    else:
        print("!! never reached loaded state"); return 1

    ds = ev("ds")
    if ds is None:
        print("!! could not read DS"); return 1
    load_base = ds - DGROUP_FRAME
    print(f"[+] DS={ds:04X} -> load_base={load_base:04X}")

    # Stage 1: only the result-display breakpoint, so typing runs undisturbed.
    send("BPDEL *\r"); drain(1.0)
    send(f"BP {load_base:04X}:{RESULT_DISPLAY:04X}\r"); drain(1.0)
    print("[+] running the query free ...")
    send("\x1b[15~")
    for _ in range(150):
        if memdump("B800:0000", 0x10, wait=3.0) is not None:
            break
    else:
        print("!! the search never reached the result display")
        with open(os.path.join(WORK, "verify_query.log"), "wb") as f:
            f.write(buf)
        return 2
    print("[+] search succeeded; letting it draw the answer")

    # Stage 2: back in control -- arm INT 16 and resume so the finished screen,
    # waiting for a keypress, stops the guest for us.
    send("BPINT 16\r"); drain(1.0)
    send("\x1b[15~")
    vram = None
    for _ in range(200):
        vram = memdump("B800:0000", COLS * ROWS * 2, "verify_screen.bin", wait=4.0)
        if vram is not None:
            rows = render(vram)
            text = "\n".join(rows).lower()
            if WANT:
                if WANT.lower() in text:
                    break
            elif any("vertrek" in r and ":" in r for r in rows[3:]):
                break
            send("\x1b[15~")
    if vram is None:
        print("!! could not capture the result screen"); return 2

    rows = render(vram)
    print("\n===== the planner's own answer =====")
    for r in rows:
        if r.strip():
            print("  " + r)
    with open(os.path.join(WORK, "verify_screen.txt"), "w") as f:
        f.write("\n".join(rows))
    print(f"\n[*] screen -> {WORK}/verify_screen.txt")
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
