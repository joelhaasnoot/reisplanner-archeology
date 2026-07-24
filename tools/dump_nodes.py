"""Dump REISPLAN.EXE's in-memory route-graph nodes via the DOSBox-X heavy debugger.

Implements docs/PLAN.md approach (1), step 3: run the program under a debugger,
break once the schedule is loaded, and dump the fixed-up station record, the
field[22] route-graph node, and the node+0xA packed-time sub-table for known
stations -- so the packing can be resolved against ground truth.

Strategy notes:
  * We never parse registers off the ncurses screen: MEMDUMPBIN accepts a segment
    register ("ds:1af4"), and writes raw bytes to MEMDUMP.BIN in the host cwd.
  * "Are we inside loaded REISPLAN.EXE?" is decided by data, not timing: DGROUP
    0x1af4 must read 469 (the record count from the file header) and the station
    table far pointer at 0x1afc must be non-null. Breaks in COMMAND.COM/BIOS fail
    this test and are resumed past.

Usage (from the repository root):  python3 tools/dump_nodes.py
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work")
DUMP = os.path.join(REPO, "MEMDUMP.BIN")
LOG = open(os.path.join(WORK, "dbg_session.log"), "wb")

# DGROUP globals (docs/BINARY.md).
DG_BASE = 0x1A80          # start of the block we snapshot
DG_COUNT = 0x1AF4         # u16  record count, must be 469
DG_STATTBL = 0x1AFC       # far ptr to the 469 x 34 station record table
REC_SIZE = 0x22           # 34 bytes per station record
N_RECORDS = 469
F22 = 22                  # station record +22 -> route-graph node (far ptr after fixup)

TARGETS = {255: "leeuwarden", 264: "mantgum", 355: "sneek", 369: "stavoren"}
ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]")

master = None
proc = None
buf = b""


def drain(timeout=0.3):
    """Read whatever the debugger has emitted."""
    global buf
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([master], [], [], max(0, end - time.time()))
        if not r:
            break
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        LOG.write(chunk)
        LOG.flush()
    return buf


def send(s):
    os.write(master, s.encode() if isinstance(s, str) else s)


def clear():
    global buf
    buf = b""


def expect(pattern, timeout=30.0):
    """Wait for pattern in the (ANSI-stripped) output."""
    pat = pattern.encode() if isinstance(pattern, str) else pattern
    end = time.time() + timeout
    while time.time() < end:
        if pat in ANSI.sub(b"", buf):
            return True
        drain(0.3)
    return False


def memdump(addr, length, dest=None, wait=8.0):
    """MEMDUMPBIN addr length -> bytes (and optionally save to work/<dest>).

    Success is detected by the dump FILE appearing at full size, never by the
    debugger's "binary success" message: the Output pane retains old messages and
    re-emits them on every ncurses redraw, so message-matching yields false
    positives. Unlinking first makes the file's reappearance unambiguous.

    Returns None if nothing was written -- normally because the guest is still
    running and the debugger is not accepting commands.
    """
    if os.path.exists(DUMP):
        os.unlink(DUMP)
    send(f"MEMDUMPBIN {addr} {length:X}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        if os.path.exists(DUMP) and os.path.getsize(DUMP) >= length:
            with open(DUMP, "rb") as f:
                data = f.read()
            if dest:
                with open(os.path.join(WORK, dest), "wb") as f:
                    f.write(data)
            return data
    return None


def u16(b, off):
    return struct.unpack_from("<H", b, off)[0]


def main():
    global master, proc
    os.makedirs(WORK, exist_ok=True)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))

    proc = subprocess.Popen(
        ["xvfb-run", "-a", "tools/dosbox-x", "-conf", "tools/dosbox-x.conf",
         "-break-start",
         "-set", "cpu cycles=fixed 50000",     # load 251 KB in reasonable wall time
         "-c", "REISPLAN.EXE"],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"), close_fds=True,
    )
    os.close(slave)

    print("[*] waiting for debugger prompt ...")
    if not expect("TYPE HELP", timeout=90):
        print("!! debugger prompt never appeared"); return 1
    print("[+] debugger up")

    # Break whenever the guest waits on the keyboard. COMMAND.COM and the BIOS
    # hit this too, so the data check below decides when we are in REISPLAN.
    clear(); send("BPINT 16\r"); drain(1.0)
    print("[+] BPINT 16 armed; running")

    seg = off = None
    # We are already stopped at the startup breakpoint, so probe first and only
    # resume if this break is not the one we want. A failed probe just means the
    # guest is still running; F5 again and retry.
    for attempt in range(1, 400):
        dg = memdump(f"ds:{DG_BASE:X}", 0x100, wait=4.0)
        if dg is None or len(dg) < 0x100:
            send("\x1b[15~")                    # F5 = run
            time.sleep(0.3)
            continue
        count = u16(dg, DG_COUNT - DG_BASE)
        p_off = u16(dg, DG_STATTBL - DG_BASE)
        p_seg = u16(dg, DG_STATTBL - DG_BASE + 2)
        if count == N_RECORDS and p_seg:
            seg, off = p_seg, p_off
            print(f"[+] loaded REISPLAN found after {attempt} break(s): "
                  f"count={count} stationtable={seg:04X}:{off:04X}")
            with open(os.path.join(WORK, "dgroup.bin"), "wb") as f:
                f.write(dg)
            break
        if attempt % 25 == 0:
            print(f"    ... {attempt} breaks (count={count})")
        send("\x1b[15~")                        # not the loaded program yet: resume
        time.sleep(0.3)
    else:
        print("!! never reached loaded state"); return 1

    # Station record table, fully fixed up.
    tbl = memdump(f"{seg:04X}:{off:04X}", REC_SIZE * N_RECORDS, "station_table.bin")
    if tbl is None or len(tbl) < REC_SIZE * N_RECORDS:
        print("!! station table dump failed"); return 1
    print(f"[+] station table dumped ({len(tbl)} bytes)")

    # Each target's route-graph node (field[22] is a far pointer after fixup).
    results = {}
    for idx, name in sorted(TARGETS.items()):
        rec = tbl[idx * REC_SIZE:(idx + 1) * REC_SIZE]
        n_off, n_seg = u16(rec, F22), u16(rec, F22 + 2)
        with open(os.path.join(WORK, f"rec_{name}.bin"), "wb") as f:
            f.write(rec)
        node = memdump(f"{n_seg:04X}:{n_off:04X}", 0x200, f"node_{name}.bin")
        if node is None:
            print(f"!! node dump failed for {name}"); continue
        results[name] = (n_seg, n_off, node)
        print(f"[+] {name:12s} idx={idx:3d} node={n_seg:04X}:{n_off:04X} "
              f"first words={[u16(node, i * 2) for i in range(6)]}")

    # Ground-truth check from docs/BINARY.md: Mantgum's node starts [255, 355].
    if "mantgum" in results:
        w = [u16(results["mantgum"][2], i * 2) for i in range(2)]
        print(f"[{'+' if w == [255, 355] else '!'}] mantgum node[0:2]={w} "
              f"(expected [255, 355] = Leeuwarden + Sneek)")

    print(f"[*] dumps written to {WORK}/")
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
        LOG.close()
    sys.exit(rc)
