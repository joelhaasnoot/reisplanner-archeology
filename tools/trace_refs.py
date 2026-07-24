"""Resolve REISPLAN.EXE's runtime load base under the debugger, and verify the
segment map in docs/BINARY.md against live memory.

Module offsets in BINARY.md are relative to the load module (EXE minus its
7168-byte MZ header). To place a breakpoint we need the load base paragraph. We
find it empirically: dump conventional memory, then locate the program's own code
bytes inside the dump. Relocated words differ from the file, so we vote across
several probe windows and take the consensus.

Usage (from the repository root):  python3 tools/trace_refs.py
"""
import fcntl, os, pty, select, struct, subprocess, sys, termios, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(REPO, "work")
DUMP = os.path.join(REPO, "MEMDUMP.BIN")
EXE = os.path.join(REPO, "input", "90-91", "REISPLAN.EXE")
MZ_HEADER = 7168

DG_BASE, DG_COUNT, DG_STATTBL = 0x1A80, 0x1AF4, 0x1AFC
N_RECORDS = 469
DGROUP_FRAME = 0x135A          # BINARY.md: DGROUP frame
CODE_FRAME = 0x028F            # BINARY.md: code segment holding the walker
TARGETS = {"walker": 0x6889, "edge_finder": 0x3BC9, "counter": 0x3C16}

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


def expect(pat, timeout=60):
    pat = pat.encode() if isinstance(pat, str) else pat
    end = time.time() + timeout
    while time.time() < end:
        if pat in buf:
            return True
        drain(0.3)
    return False


def memdump(addr, length, dest=None, wait=25.0):
    """Success is detected by the FILE appearing at full size (see dump_nodes.py)."""
    if os.path.exists(DUMP):
        os.unlink(DUMP)
    send(f"MEMDUMPBIN {addr} {length:X}\r")
    end = time.time() + wait
    while time.time() < end:
        drain(0.2)
        if os.path.exists(DUMP) and os.path.getsize(DUMP) >= length:
            time.sleep(0.2)
            with open(DUMP, "rb") as f:
                data = f.read()
            if dest:
                with open(os.path.join(WORK, dest), "wb") as f:
                    f.write(data)
            return data
    return None


def main():
    global master, proc
    os.makedirs(WORK, exist_ok=True)
    module = open(EXE, "rb").read()[MZ_HEADER:]
    print(f"[*] load module: {len(module)} bytes")

    master_fd, slave = pty.openpty()
    globals()["master"] = master_fd
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    proc = subprocess.Popen(
        ["xvfb-run", "-a", "tools/dosbox-x", "-conf", "tools/dosbox-x.conf",
         "-break-start", "-set", "cpu cycles=fixed 50000", "-c", "REISPLAN.EXE"],
        cwd=REPO, stdin=slave, stdout=slave, stderr=slave,
        env=dict(os.environ, TERM="xterm-256color"), close_fds=True)
    os.close(slave)

    if not expect("TYPE HELP", 90):
        print("!! no debugger prompt"); return 1
    send("BPINT 16\r"); drain(1.0)
    print("[+] debugger up, BPINT 16 armed")

    for attempt in range(1, 400):
        dg = memdump(f"ds:{DG_BASE:X}", 0x100, wait=4.0)
        if dg is None or len(dg) < 0x100:
            send("\x1b[15~"); time.sleep(0.3); continue
        if struct.unpack_from("<H", dg, DG_COUNT - DG_BASE)[0] == N_RECORDS and \
           struct.unpack_from("<H", dg, DG_STATTBL - DG_BASE + 2)[0]:
            print(f"[+] loaded program reached after {attempt} break(s)")
            break
        send("\x1b[15~"); time.sleep(0.3)
    else:
        print("!! never reached loaded state"); return 1

    print("[*] dumping 1 MB of conventional memory ...")
    mem = memdump("0000:0000", 0x100000, "lowmem.bin", wait=180.0)
    if mem is None:
        print("!! low memory dump failed"); return 1
    print(f"[+] {len(mem)} bytes dumped")

    # Vote for the load base across several probe windows. Relocated words differ
    # from the on-disk image, so no single window is guaranteed to match.
    votes = {}
    for probe in range(0x1000, 0xE000, 0x800):
        sig = module[probe:probe + 32]
        if len(sig) < 32 or sig.count(sig[0:1]) == len(sig):
            continue
        start = 0
        while True:
            i = mem.find(sig, start)
            if i < 0:
                break
            base_lin = i - probe
            if base_lin > 0 and base_lin % 16 == 0:
                votes[base_lin // 16] = votes.get(base_lin // 16, 0) + 1
            start = i + 1
    if not votes:
        print("!! could not locate the module in memory"); return 1
    load_base, n = max(votes.items(), key=lambda kv: kv[1])
    print(f"[+] load base = {load_base:04X}  ({n} probe windows agree; "
          f"{len(votes)} candidate(s))")

    # Verify BINARY.md's segment map against live memory.
    print("\n[*] verifying segment map from docs/BINARY.md")
    dgroup = load_base + DGROUP_FRAME
    code = load_base + CODE_FRAME
    print(f"    DGROUP frame {DGROUP_FRAME:04X} -> runtime {dgroup:04X}")
    print(f"    code   frame {CODE_FRAME:04X} -> runtime {code:04X}")
    ok = True
    for name, moff in sorted(TARGETS.items(), key=lambda kv: kv[1]):
        cs_off = moff - CODE_FRAME * 16
        lin = code * 16 + cs_off
        live = mem[lin:lin + 16]
        disk = module[moff:moff + 16]
        match = live == disk
        ok &= match
        print(f"    {name:12s} module 0x{moff:05x} -> {code:04X}:{cs_off:04X}  "
              f"{'MATCH' if match else 'differ'}")
        if not match:
            print(f"        live={live.hex(' ')}")
            print(f"        disk={disk.hex(' ')}")
    print(f"\n[{'+' if ok else '!'}] segment map {'verified' if ok else 'NOT confirmed'}")
    print(f"[*] breakpoint for the edge-finder: BP {code:04X}:{TARGETS['edge_finder'] - CODE_FRAME*16:04X}")
    with open(os.path.join(WORK, "loadbase.txt"), "w") as f:
        f.write(f"load_base={load_base:04X}\ncode={code:04X}\ndgroup={dgroup:04X}\n")
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
