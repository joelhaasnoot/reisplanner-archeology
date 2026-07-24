"""Break at the edge-finder (0x3bc9) while REISPLAN.EXE runs a real query, and
capture how it turns a station pair into event data -- the node->events link that
blocks per-station labelling (docs/PLAN.md).

The query (Leeuwarden -> Stavoren, 08:00) is typed into the guest with DOSBox-X's
AUTOTYPE, issued from autoexec with a delay: emulated time only advances while the
guest runs, so we have unlimited wall-clock time at breakpoints to arm things
before the keystrokes land.

Usage (from the repository root):  python3 tools/trace_query.py
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work")
DUMP = os.path.join(REPO, "MEMDUMP.BIN")
EXE = os.path.join(REPO, "input", "90-91", "REISPLAN.EXE")
MZ_HEADER = 7168
ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]")

DG_BASE, DG_COUNT, DG_STATTBL = 0x1A80, 0x1AF4, 0x1AFC
N_RECORDS = 469
CODE_FRAME = 0x028F
EDGE_FINDER = 0x3BC9                   # module offset (docs/BINARY.md)
EDGE_OFF = EDGE_FINDER - CODE_FRAME * 16     # -> 0x12d9, CS-relative

# Query typed into the guest: van / TAB / naar / TAB / tijd / F9.
# Fields are separated by TAB (not Enter). Default date is already
# zondag 27 mei 1990, the golden trip's date.
# The LEADING SPACE is essential: the program sits on "Alles ingelezen. Druk op
# een toets." and eats the first keystroke, which previously turned "amsterdam"
# into "msterdam" -- an invalid station, so no search ran at all.
KEYS = ("space a m s t e r d a m space c s tab "
        "u t r e c h t space c s tab "
        "0 9 0 0 f9")

RESULT_DISPLAY = 0x1F41   # module offset, frame-0 segment -> load_base:1F41

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
    """Returns None when the guest is running (debugger not accepting commands)."""
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
    """Read one value via the EV command (prints to the Output pane as text)."""
    global buf
    marker = f"EV of '{expr.upper()}' is:"
    before = ANSI.sub(b"", buf).count(marker.encode())
    send(f"EV {expr}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        txt = ANSI.sub(b"", buf).decode("utf-8", "replace")
        if txt.count(marker) > before:
            tail = txt.rsplit(marker, 1)[1]
            m = re.search(r"[0-9A-Fa-f]+", tail)
            if m:
                return int(m.group(0), 16)
    return None


def main():
    global master, proc
    os.makedirs(WORK, exist_ok=True)
    module = open(EXE, "rb").read()[MZ_HEADER:]

    master_fd, slave = pty.openpty()
    globals()["master"] = master_fd
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "tools/dosbox-x", "-conf", "tools/dosbox-x.conf",
         "-break-start", "-set", "cpu cycles=fixed 50000",
         "-c", f"AUTOTYPE -w 25 -p 0.25 {KEYS}",
         "-c", "REISPLAN.EXE"],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"), close_fds=True)
    os.close(slave)

    if not expect("TYPE HELP", 90):
        print("!! no debugger prompt"); return 1
    send("BPINT 16\r"); drain(1.0)
    print("[+] debugger up; reaching loaded program ...")

    for attempt in range(1, 400):
        dg = memdump(f"ds:{DG_BASE:X}", 0x100, wait=4.0)
        if dg is None or len(dg) < 0x100:
            send("\x1b[15~"); time.sleep(0.2); continue
        if struct.unpack_from("<H", dg, DG_COUNT - DG_BASE)[0] == N_RECORDS and \
           struct.unpack_from("<H", dg, DG_STATTBL - DG_BASE + 2)[0]:
            print(f"[+] loaded after {attempt} break(s)")
            break
        send("\x1b[15~"); time.sleep(0.2)
    else:
        print("!! never reached loaded state"); return 1

    # DS is DGROUP in this large-model program -> derive the code segment.
    ds = ev("ds")
    if ds is None:
        print("!! could not read DS"); return 1
    load_base = ds - 0x135A
    code = load_base + CODE_FRAME
    print(f"[+] DS={ds:04X} -> load_base={load_base:04X} code={code:04X}")

    live = memdump(f"{code:04X}:{EDGE_OFF:04X}", 0x10)
    if live is None or live != module[EDGE_FINDER:EDGE_FINDER + 0x10]:
        print("!! code bytes at the edge-finder do not match the module")
        if live:
            print("   live=", live.hex(" "))
            print("   disk=", module[EDGE_FINDER:EDGE_FINDER + 0x10].hex(" "))
        return 1
    print(f"[+] verified edge-finder code at {code:04X}:{EDGE_OFF:04X}")

    # Swap the INT 16 breakpoint for the edge-finder breakpoint, then let the
    # AUTOTYPE'd query run into it.
    # Arm the edge-finder AND the result-display routine. The guest must run FREE
    # while AUTOTYPE types (an INT 16 breakpoint freezes it and keystrokes are
    # dropped), so these are the only breakpoints. result-display also fires only
    # on a SUCCESSFUL search, which distinguishes "query never ran" from
    # "edge-finder is not on the query path".
    send("BPDEL *\r"); drain(1.0)
    send(f"BP {code:04X}:{EDGE_OFF:04X}\r"); drain(1.0)
    send(f"BP {load_base:04X}:{RESULT_DISPLAY:04X}\r"); drain(1.0)
    print(f"[+] BP at edge-finder {code:04X}:{EDGE_OFF:04X} "
          f"and result-display {load_base:04X}:{RESULT_DISPLAY:04X}")
    print("[+] running the query free ...")

    send("\x1b[15~")
    hit = None
    for i in range(120):
        probe = memdump(f"{code:04X}:{EDGE_OFF:04X}", 0x10, wait=3.0)
        if probe is not None:
            hit = i
            break
    if hit is None:
        print("!! edge-finder breakpoint never hit (query may not have run)")
        with open(os.path.join(WORK, "trace_query.log"), "wb") as f:
            f.write(buf)
        return 2

    print(f"[+] BREAKPOINT HIT at the edge-finder (probe {hit})")
    regs = {}
    for r in ("ax", "bx", "cx", "dx", "si", "di", "es", "ds", "ss", "sp", "bp"):
        regs[r] = ev(r)
    print("    " + "  ".join(f"{k.upper()}={v:04X}" if v is not None else f"{k.upper()}=?"
                             for k, v in regs.items()))
    if regs["ss"] is not None and regs["sp"] is not None:
        stack = memdump(f"{regs['ss']:04X}:{regs['sp']:04X}", 0x40, "edge_stack.bin")
        if stack:
            w = [struct.unpack_from("<H", stack, i * 2)[0] for i in range(16)]
            print("    stack:", " ".join(f"{x:04X}" for x in w))
    # The guest's own screen at the moment of the break: ground truth.
    vram = memdump("B800:0000", 80 * 25 * 2, "screen_at_break.bin")
    if vram:
        print("    --- guest screen ---")
        for r in range(25):
            line = "".join(chr(vram[(r * 80 + c) * 2]) if 32 <= vram[(r * 80 + c) * 2] < 127
                           else " " for c in range(80)).rstrip()
            if line.strip():
                print("    " + line)
    with open(os.path.join(WORK, "trace_query.log"), "wb") as f:
        f.write(buf)
    print(f"[*] session log -> {WORK}/trace_query.log")
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
