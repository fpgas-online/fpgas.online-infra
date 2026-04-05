# Emulating the VideoCore Bootloader's Exact TFTP Sequence in U-Boot

This document shows how to make U-Boot issue the **same TFTP reads in the same order** as the Raspberry Pi 3B+ VideoCore GPU bootloader, including speculative probes that normally 404, serial-number-prefixed paths, and re-reads of the same file at different stages.

## Source trace

The sequence below is taken from a tcpdump capture of a real Pi 3B+ performing a netboot with a mostly-empty TFTP root, documented at metebalci.com. The empty-root case is what forces the bootloader to exhaust every fallback — if a matching `kernel8.img`, `start.elf`, etc. is present, the bootloader stops probing as soon as it has what it needs, so the *maximal* sequence is what you want to emulate for a faithful server-side test.

## The exact VideoCore TFTP request sequence (Pi 3B+)

Two files are fetched from the **TFTP root** (no serial prefix) because at that point the bootloader hasn't identified itself yet:

1. `bootcode.bin`
2. `bootsig.bin`

Then `bootcode.bin` runs, learns the board serial (`f393a191` in the trace), and all subsequent requests are prefixed with `<serial>/`:

3. `f393a191/start.elf`
4. `f393a191/autoboot.txt`
5. `f393a191/config.txt`
6. `f393a191/recovery.elf`
7. `f393a191/start.elf`
8. `f393a191/fixup.dat`
9. `f393a191/recovery.elf`
10. `f393a191/config.txt`
11. `f393a191/dt-blob.bin`
12. `f393a191/recovery.elf`
13. `f393a191/config.txt`
14. `f393a191/bootcfg.txt`
15. `f393a191/cmdline.txt`
16. `f393a191/recovery8.img`
17. `f393a191/recovery8-32.img`
18. `f393a191/recovery7.img`
19. `f393a191/recovery.img`
20. `f393a191/kernel8.img`
21. `f393a191/kernel8-32.img`
22. `f393a191/kernel7.img`
23. `f393a191/kernel.img`
24. `f393a191/armstub8.bin`
25. `f393a191/armstub8-32.bin`
26. `f393a191/armstub7.bin`
27. `f393a191/armstub.bin`
28. `f393a191/bcm2710-rpi-3-b-plus.dtb`
29. `f393a191/bcm2710-rpi-3-b.dtb`

Note: `start.elf` is requested twice (#3 and #7), `config.txt` three times (#5, #10, #13), and `recovery.elf` three times (#6, #9, #12). This is not a bug — the VideoCore firmware re-reads these at different bootloader stages.

The trace was captured on a Pi 3B+ with a specific firmware vintage (circa 2019). Newer bootloader firmware changes the probe set — particularly for Pi 4, which bypasses `bootcode.bin` (it lives in SPI EEPROM) and adds `start4.elf`/`fixup4.dat`/`bcm2711-*.dtb` variants. If you need a different target, capture your own trace.

## U-Boot script reproducing the sequence faithfully

To actually behave like the VideoCore, the script needs to (a) send the same DHCP options so dnsmasq sees a PXEClient-style request, (b) stop probing once it has what it needs, (c) parse `config.txt` between stages and branch on its contents the way the real firmware does, and (d) pace reads with the delays the real GPU introduces. U-Boot can do all of those.

### DHCP: match the VideoCore's option set before sending DISCOVER

```
# DHCP option 60 (vendor-class-identifier) — what makes dnsmasq's
# pxe-service=0,"Raspberry Pi Boot " line tag-match.
setenv bootp_vci "PXEClient:Arch:00000:UNDI:002001"

# DHCP option 93 (client architecture) — 0 = Intel x86PC in the real trace,
# which is what the VideoCore actually sends (yes, really).
setenv bootp_arch 0

# DHCP option 94 (network device interface) — UNDI 2.1
setenv bootp_ndi "1.2.1"

# Now DISCOVER. dnsmasq will see vendor-class "PXEClient:..." and reply
# with option 43 containing "Raspberry Pi Boot " if configured.
setenv autoload no
dhcp

# Option 43 gate: the VideoCore ROM will not proceed to TFTP unless the
# server's DHCP reply included option 43 with "Raspberry Pi Boot " in it.
# U-Boot parses option 43 into ${bootp_vendor} (some builds: ${vendor_option}).
# Mirror the VC's refusal-to-proceed behavior.
if test -z "${bootp_vendor}" ; then
    echo "No DHCP option 43 from server — VideoCore would stop here."
    reset
fi
# The string contains binary PXE sub-options; "Raspberry Pi Boot" appears as
# ASCII inside. A substring search via setexpr is awkward in pure hush, so
# the simplest faithful check is: option 43 present and non-empty.
# For a stricter check, compare a known prefix byte pattern via setexpr.
```

Exact env variable names vary slightly across U-Boot versions; on older builds you may need `bootp_vendor_class_identifier` instead of `bootp_vci`. Check `env print -a | grep bootp` after a build to see what your U-Boot exposes.

### Helpers: probe, load, config-parse, branch

```
# Placeholder for the board serial. On real hardware this comes from OTP.
setenv pi_serial f393a191

# Scratch address for speculative/throwaway fetches.
setenv scratch 0x02000000

# Flags that will be set as we discover what's on the server.
setenv have_start     0
setenv have_fixup     0
setenv have_config    0
setenv have_cmdline   0
setenv have_kernel    0
setenv have_dtb       0
setenv kernel_name    kernel8.img
setenv dtb_name       bcm2710-rpi-3-b-plus.dtb

# probe: try a file, set ${found} to 1 if it loaded, 0 otherwise.
# Takes ${path}. Writes ${found} and leaves data at ${scratch}.
setenv probe 'setenv found 0 ; tftpboot ${scratch} ${path} && setenv found 1 || true'

# parse_config: import config.txt as env vars, then apply overrides.
# The Pi's config.txt is key=value with # comments — compatible with env import -t.
setenv parse_config '
  env import -t ${scratch} ${filesize} ;
  test -n "${kernel}"    && setenv kernel_name ${kernel} ;
  test -n "${device_tree}" && setenv dtb_name  ${device_tree} ;
  true
'
```

### The 29-request sequence with real branching

```
# --- Root-level probes (pre-serial) ---
setenv path bootcode.bin                          ; run probe   #  1
setenv path bootsig.bin                           ; run probe   #  2
sleep 1   # VC delay between bootcode load and second-stage startup

# --- First start.elf probe (#3). If it exists, remember that. ---
setenv path ${pi_serial}/start.elf
run probe
test "${found}" = "1" && setenv have_start 1

setenv path ${pi_serial}/autoboot.txt             ; run probe   #  4

# --- config.txt (#5): parse it and pick up kernel=/device_tree= overrides ---
setenv path ${pi_serial}/config.txt
run probe
if test "${found}" = "1" ; then
    setenv have_config 1
    run parse_config
fi

setenv path ${pi_serial}/recovery.elf             ; run probe   #  6

# --- start.elf re-read (#7): VC reloads after parsing config.txt in case
#     a start_file= override pointed elsewhere. ---
setenv path ${pi_serial}/start.elf                ; run probe   #  7
sleep 1

# --- fixup.dat (#8) ---
setenv path ${pi_serial}/fixup.dat
run probe
test "${found}" = "1" && setenv have_fixup 1

setenv path ${pi_serial}/recovery.elf             ; run probe   #  9

# --- config.txt re-read (#10) after start.elf is loaded ---
setenv path ${pi_serial}/config.txt
run probe
test "${found}" = "1" && run parse_config

setenv path ${pi_serial}/dt-blob.bin              ; run probe   # 11
setenv path ${pi_serial}/recovery.elf             ; run probe   # 12

# --- config.txt third read (#13) after dt-blob attempt ---
setenv path ${pi_serial}/config.txt
run probe
test "${found}" = "1" && run parse_config

setenv path ${pi_serial}/bootcfg.txt              ; run probe   # 14

# --- cmdline.txt (#15) ---
setenv path ${pi_serial}/cmdline.txt
run probe
if test "${found}" = "1" ; then
    setenv have_cmdline 1
    # Grab it as a string for later use.
    setexpr cmdline_end ${scratch} + ${filesize}
    # (U-Boot: env set bootargs from buffer is board-specific; omitted here.)
fi

# --- recovery image probes (#16-19). VC walks these only if no kernel yet. ---
for r in recovery8.img recovery8-32.img recovery7.img recovery.img ; do
    setenv path ${pi_serial}/${r}
    run probe
done

# --- kernel probes (#20-23). Real load address; stop at first hit. ---
for k in kernel8.img kernel8-32.img kernel7.img kernel.img ; do
    tftpboot ${kernel_addr_r} ${pi_serial}/${k} && setenv have_kernel 1 && setenv kernel_name ${k} && break || true
done

# If config.txt specified a non-standard kernel= name and we haven't loaded
# it yet, try it now (matches VC override behavior).
if test "${have_kernel}" = "0" -a -n "${kernel}" ; then
    tftpboot ${kernel_addr_r} ${pi_serial}/${kernel} && setenv have_kernel 1 || true
fi

# --- armstub probes (#24-27). VC loads the first one that exists. ---
for a in armstub8.bin armstub8-32.bin armstub7.bin armstub.bin ; do
    setenv path ${pi_serial}/${a}
    run probe
    test "${found}" = "1" && break
done

# --- DTB probes (#28-29). Board-specific first, then generic. ---
tftpboot ${fdt_addr_r} ${pi_serial}/${dtb_name} && setenv have_dtb 1 || true
if test "${have_dtb}" = "0" ; then
    tftpboot ${fdt_addr_r} ${pi_serial}/bcm2710-rpi-3-b.dtb && setenv have_dtb 1 || true
fi

# --- Hand off only if we have the minimum the VC would have. ---
if test "${have_kernel}" = "1" -a "${have_dtb}" = "1" ; then
    booti ${kernel_addr_r} - ${fdt_addr_r}
else
    echo "VC-equivalent boot failed: kernel=${have_kernel} dtb=${have_dtb}"
    reset
fi
```

This version:
- Sends DHCP option 60/93/94 matching the VC's ROM.
- Parses `config.txt` via `env import -t` and applies `kernel=` / `device_tree=` overrides the way the real firmware does (including re-parsing on each re-read).
- Terminates kernel and DTB probing at the first successful load rather than blindly running all 29 reads.
- Falls through armstub/recovery candidates until one exists, matching VC's "first match wins" behavior for those groups.
- Still issues the speculative probes (recovery*, dt-blob.bin, autoboot.txt, bootcfg.txt) that the VC issues even when it doesn't need them, so the on-wire trace still matches for server-side testing.
- Refuses to `booti` without both kernel and DTB, same as VC.

## What this emulation still cannot do

A handful of VC behaviors genuinely can't be reproduced in U-Boot, because they require running GPU firmware:

- **Executing `start.elf`.** The real VideoCore loads `start.elf` into the VC4 GPU and runs it; that's what performs the memory-split configuration, HDMI initialization, and eventual ARM-core release. U-Boot can only fetch the file into RAM — it has no VC4 to execute it on. This is fundamental to QEMU's raspi machines not emulating the GPU.
- **Request timing at sub-second granularity.** `sleep` in U-Boot has 1-second resolution on most builds, and the VC's inter-stage gaps come from real work (EDID probing, SDRAM setup), not fixed delays. Our `sleep 1` calls are approximations.
- **OTP-derived serial number.** The `pi_serial` here is a static placeholder. A real Pi reads this from on-chip OTP; U-Boot on QEMU has no equivalent source unless you hardcode it.

## Capturing your own trace

If you need the sequence for a different board, firmware, or Pi 4:

```bash
sudo tcpdump -i <iface> -vv 'ether host <pi_mac>' -w pi-netboot.pcap
```

Open the pcap in Wireshark, filter `tftp.opcode == 1` (read requests), and read off the filenames in order. Plug that list into the script above.
