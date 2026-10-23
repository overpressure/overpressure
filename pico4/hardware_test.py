"""Bounded, serial-bound PICO 4 tests; RAM boot by default, explicit flash-run opt-in."""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import io
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from . import bootimg, compress, cpio, device, userspace

ROOT = Path(__file__).resolve().parent.parent


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_preflight(output: str) -> None:
    summaries = re.findall(r"^PREFLIGHT: (\d+) passed, (\d+) failed\s*$", output, re.M)
    require(len(summaries) == 1 and int(summaries[0][0]) > 0 and int(summaries[0][1]) == 0,
            "Preflight failed or did not produce a complete result")


def validate_storage(output: str, data: str = "") -> None:
    """Every physical block device read-only; on the installed system its data partition
    (@p data, e.g. sda16) is the one exception and must be writable: read-only under the
    mounted file system, its writeback fails and the data is lost."""
    rows = [line.split() for line in output.splitlines()]
    require(rows and all(len(r) == 3 and r[0] == "BLOCK" and r[2] in ("0", "1") for r in rows),
            "Storage protection failed: missing or invalid block-device inventory")
    physical = [r for r in rows if not r[1].startswith(("loop", "ram", "zram", "nbd"))]
    require(physical, "No physical storage detected")
    require(all(r[2] == "1" for r in physical if r[1] != data),
            "Storage protection failed: writable disk or partition")
    if data:
        require([r[2] for r in physical if r[1] == data] == ["0"],
                "The installed system's data partition is not writable")


def data_device(mounts: str) -> str:
    """The block device (sda16) mounted at /data, or empty."""
    for row in mounts.splitlines():
        fields = row.split()
        if len(fields) >= 2 and fields[1] == "/data" and fields[0].startswith("/dev/"):
            return fields[0][len("/dev/"):]
    return ""


def validate_mounts(output: str, installed: bool = False) -> None:
    rows = [row.split() for row in output.splitlines()]
    require(rows and all(len(r) == 6 for r in rows), "Invalid mount inventory")
    roots = [r for r in rows if r[1] == "/"]
    writable = [r for r in rows if r[0].startswith("/dev/") and not r[0].startswith("/dev/zram")
                and "rw" in r[3].split(",")]
    if installed:
        # The installed system: its image read-only; writable only the data partition, the device
        # at /data, wherever it is bound (/var, /home, SteamOS's offload binds of /srv, /var/log...).
        require(roots and all(r[0].startswith("/dev/") and "ro" in r[3].split(",") for r in roots),
                "The installed system's root is not mounted read-only")
        data = {r[0] for r in writable if r[1] == "/data"}
        require(len(data) == 1 and {r[0] for r in writable} == data,
                "A device-backed filesystem other than the data partition is mounted writable")
        return
    # The step-4 userspace runs from ext4 on zram: compressed RAM, not storage.
    require(roots and all(r[2] in ("rootfs", "tmpfs", "ramfs") or r[0].startswith("/dev/zram")
                          for r in roots), "Root filesystem is not RAM-backed")
    require(not writable, "A device-backed filesystem is mounted writable")


def validate_report(data: bytes, run_id: str) -> None:
    # Inspect members without extracting device-controlled archive paths.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("report/summary.txt", "report/system.txt", "report/dmesg.txt"):
            member = archive.getmember(name)
            require(member.isfile() and 0 < member.size <= 16 * 1024 * 1024,
                    f"Invalid report member: {name}")
            payload = archive.extractfile(member).read()
            if name == "report/system.txt":
                require(run_id.encode() in payload, "HTTP report is from a different boot")


def prepare_image(source: Path, folder: Path, run_id: str, public_key: bytes,
                  connect_timeout: int = 210, test_timeout: int = 900, userspace: bool = False) -> dict:
    original = source.read_bytes()
    image = bootimg.parse(original)
    require(image.id == image.compute_id(), "Source boot image checksum mismatch")
    words = image.full_cmdline.split()
    require(not any(w in ("pico4poc.rw", "pico4poc.selftest") or
                    w.startswith(("pico4poc.rw=", "pico4poc.selftest=")) and not w.endswith("=0")
                    for w in words), "Hardware tests require read-only mode and QEMU selftest disabled")
    tree = cpio.Tree(nodes=dict(cpio.read_cpio(compress.decompress(image.ramdisk)[0])))
    require("etc/pico4-poc/build-info" in tree.nodes, "Image is not a PICO 4 POC build")
    require("usr/libexec/pico4-poc/recovery-watch" in tree.nodes and
            "etc/inittab" in tree.nodes and b"svc recovery-watch" in tree.nodes["etc/inittab"].data,
            "Rebuild the POC image: independent recovery watchdog is missing")
    if userspace:
        require("usr/bin/pico4-userspace" in tree.nodes and "usr/bin/zstd" in tree.nodes
                and "usr/libexec/pico4-poc/pico4-init" in tree.nodes
                and b"::restart:/usr/libexec/pico4-poc/switch-userspace" in tree.nodes["etc/inittab"].data,
                "Rebuild the POC image: the userspace handoff is missing")
    info = json.loads(tree.nodes["etc/pico4-poc/build-info"].data)
    require(0 < connect_timeout < test_timeout, "Invalid device recovery deadlines")
    # Preflight runs the exact source userspace, excluding device nodes.
    with (folder / "rootfs.tar").open("wb") as stream:
        tree.to_tar(stream, skip_devices=True)
    tree.mkdir("root/.ssh", 0o700)
    tree.add_file("root/.ssh/authorized_keys", public_key.rstrip() + b"\n", 0o600)
    tree.add_file("etc/pico4-poc/test-run", run_id.encode() + b"\n")
    tree.add_file("etc/pico4-poc/test-connect-timeout", f"{connect_timeout}\n".encode())
    tree.add_file("etc/pico4-poc/test-timeout", f"{test_timeout}\n".encode())
    words = [w for w in words if not w.startswith("pico4poc.test_run=")]
    image = image.with_cmdline(" ".join(words + [f"pico4poc.test_run={run_id}"]))
    image.ramdisk = gzip.compress(tree.to_cpio(), mtime=0)
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    require(check.kernel == image.kernel and check.dtb == image.dtb and check.id == check.compute_id(),
            "Instrumented boot image failed validation")
    (folder / "test-boot.img").write_bytes(data)
    return {"source_image": str(source.resolve()), "source_sha256": hashlib.sha256(original).hexdigest(),
            "boot_sha256": hashlib.sha256(data).hexdigest(), "build_info": info,
            "device_recovery": {"connect_timeout": connect_timeout, "test_timeout": test_timeout}}


def userspace_state(archive: Path) -> dict:
    """The archive's identity and the features its manifest says it contains."""
    manifest = json.loads(archive.with_name("pico4-userspace.manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    require(manifest.get("archive", {}).get("sha256") == digest, "Userspace archive does not match its manifest")
    packages = manifest.get("packages", {})
    return {"archive": str(archive.resolve()), "sha256": digest, "bytes": archive.stat().st_size,
            "packages": len(packages), "systemd": packages.get("systemd"),
            "gpu_firmware": bool(manifest.get("gpu_firmware")),
            "sensor_data": bool(manifest.get("sensor_data")),
            "openxr_runtime": "pico4-monado" in packages,
            "vr_display": "pico4-vrdisplay" in packages,
            "steamvr": "deckard-steamvr-rel" in packages}


def prepare_restore_image(backup: Path, folder: Path, serial: str, identity: dict, image: Path) -> dict:
    require(backup.resolve() not in (folder.resolve(), *folder.resolve().parents),
            "Test results must be outside the immutable backup directory")
    manifest = device.verified_backup(backup)
    saved = manifest.get("identity", {})
    require(saved.get("serial") == serial and
            all(saved.get(key) == value for key, value in identity.items()),
            "Backup identity does not match this headset and firmware")
    names = [name for name in manifest["files"]
             if re.fullmatch(r"boot(?:_[ab])?", Path(name).stem) and Path(name).suffix in (".bin", ".img")]
    require(len(names) == 1 and Path(names[0]).stem == "boot",
            "Flash tests require exactly one original, non-slotted boot partition")
    original = backup / names[0]
    expected = manifest["files"][names[0]]
    require(image.stat().st_size <= expected["size"], "Test image exceeds the boot partition")
    stock, test = bootimg.parse(original.read_bytes()), bootimg.parse(image.read_bytes())
    require(stock.header_version <= 2 and stock.id == stock.compute_id(), "Stock boot checksum mismatch")
    for field in ("header_version", "page_size", "kernel", "second", "recovery_dtbo", "dtb", "name",
                  "os_version", "kernel_addr", "ramdisk_addr", "second_addr", "tags_addr", "dtb_addr"):
        require(getattr(stock, field) == getattr(test, field), f"POC differs from backed-up stock {field}")
    target = folder / "stock-boot.img"
    with original.open("rb") as source, target.open("xb") as output:
        shutil.copyfileobj(source, output)
    require(device.digest(target) == expected, "Restoration copy differs from the verified backup")
    return {"source": str(original.resolve()), "path": str(target.resolve()), **expected}


def fastboot_value(output: str, name: str) -> str:
    values = re.findall(r"^[ \t]*(?:\(bootloader\)[ \t]*)?" + re.escape(name)
                        + r":[ \t]*(\S+)[ \t]*\r?$", output, re.M)
    require(len(values) == 1, f"Missing or ambiguous fastboot variable: {name}")
    return values[0]


class Runner:
    def __init__(self, args):
        self.args = args
        self.folder = args.out.resolve()
        self.flash_mode = args.command == "flash-run"
        self.userspace = bool(getattr(args, "userspace", None))
        if self.flash_mode:
            require(args.backup.resolve() not in (self.folder, *self.folder.parents),
                    "Test results must be outside the immutable backup directory")
        self.folder.mkdir(parents=True, exist_ok=False)
        self.run_id = uuid.uuid4().hex
        self.result = {"schema": 1, "run_id": self.run_id, "started": utc(), "status": "running",
                       "serial": args.serial, "checks": [], "recovery": "not_needed"}
        self.count = 0
        self.moved = False
        self.boot_attempted = False
        self.authenticated = False
        self.ncm_initialized = False
        self.baseline = None
        self.boot_dirty = False
        self.result.update(boot_method="flash" if self.flash_mode else "ram" if args.command == "run" else "none",
                           boot_restore="not_needed", boot_restore_required=False)
        self.save()

    def save(self):
        temp = self.folder / "result.json.tmp"
        temp.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.folder / "result.json")

    def command(self, argv, timeout=30, stdin=None):
        self.count += 1
        prefix = self.folder / f"command-{self.count:04d}"
        Path(str(prefix) + ".json").write_text(json.dumps({"argv": list(map(str, argv)),
                    "timeout": timeout, "started": utc(),
                    "stdin": str(stdin.name) if stdin else None}, indent=2), encoding="utf-8")
        try:
            process = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     timeout=timeout, stdin=stdin)
            output = process.stdout
            Path(str(prefix) + ".log").write_bytes(output)
            text = output.decode("utf-8", errors="replace").strip()
            require(process.returncode == 0 and not re.search(r"\bFAILED\b", text),
                    f"Command failed ({process.returncode}); see {prefix.name}.log")
            return text
        except subprocess.TimeoutExpired as exc:
            Path(str(prefix) + ".log").write_bytes((exc.stdout or b"") + b"\nTIMEOUT\n")
            raise ValueError(f"Command timed out; see {prefix.name}.log") from exc

    def adb(self, *words, timeout=30):
        return self.command([self.args.adb, "-s", self.args.serial, *words], timeout)

    def fastboot(self, *words, timeout=30):
        return self.command([self.args.fastboot, "-s", self.args.fastboot_serial or self.args.serial, *words], timeout)

    def ssh(self, command, timeout=30, stdin=None):
        return self.command([self.args.ssh, "-F", self.folder / "ssh_config", "-i", self.folder / "id_ed25519",
                             "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
                             "-o", "StrictHostKeyChecking=accept-new", "-o",
                             f'UserKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"', "-o",
                             f'GlobalKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"',
                             f"root@{self.args.host}", command], timeout,
                            **({"stdin": stdin} if stdin is not None else {}))

    def step(self, name, operation):
        print(f">> {name}", flush=True)
        record = {"name": name, "started": utc(), "status": "running"}
        self.result["checks"].append(record)
        self.save()
        try:
            value = operation()
            record["status"] = "passed"
            return value
        except (Exception, KeyboardInterrupt) as exc:
            record.update(status="failed", error=str(exc) or "Interrupted")
            raise
        finally:
            record["finished"] = utc()
            self.save()

    def wait(self, name, seconds, operation):
        deadline = time.monotonic() + seconds
        last = "no response"
        while time.monotonic() < deadline:
            try:
                return operation(max(0.1, min(10, deadline - time.monotonic())))
            except (ValueError, OSError) as exc:
                last = str(exc)
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise ValueError(f"Timed out waiting for {name}: {last}")

    def identity(self, timeout=30):
        output = self.adb("shell", "getprop ro.build.fingerprint; getprop ro.oem.state; "
                          "cat /sys/devices/soc0/serial_number; getprop sys.boot_completed", timeout=timeout)
        lines = output.splitlines()
        require(len(lines) == 4 and lines[3].strip() == "1", "PICO OS has not completed boot")
        require(re.fullmatch(r"Pico/[Pp]hoenix(?:_ovs)?/PICOA8110:10/\S+", lines[0]),
                "Selected device is not an original PICO 4")
        return {"fingerprint": lines[0], "oem_state": lines[1], "chip_id": device.chip_id(lines[2])}

    def preflight(self, rootfs):
        remote = f"/data/local/tmp/pico4-poc-{self.run_id}"
        try:
            self.adb("shell", f"mkdir -p {remote}")
            self.adb("push", str(rootfs), remote + "/rootfs.tar", timeout=180)
            self.adb("push", str(ROOT / "scripts/preflight-device.sh"), remote + "/preflight.sh")
            output = self.adb("shell", f"PICO4_PREFLIGHT_DIR={remote} sh {remote}/preflight.sh", timeout=300)
            (self.folder / "preflight.txt").write_text(output + "\n", encoding="utf-8")
            validate_preflight(output)
        finally:
            self.adb("shell", f"rm -rf {remote}")

    def prepare(self):
        if getattr(self.args, "windows_ncm_python", None):
            readiness = json.loads(self.command([self.args.windows_ncm_python,
                                    ROOT / "pico4/windows_ncm.py", "--check"], timeout=15))
            require(readiness.get("status") == "ready", "Windows NCM helper is not ready")
            self.result["windows_ncm"] = readiness
        self.command([self.args.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", self.folder / "id_ed25519"])
        (self.folder / "ssh_config").write_text(
            f'Host pico4\n  HostName {self.args.host}\n  User root\n'
            f'  IdentityFile "{(self.folder / "id_ed25519").as_posix()}"\n'
            '  IdentitiesOnly yes\n  BatchMode yes\n  ConnectTimeout 5\n  StrictHostKeyChecking yes\n'
            f'  UserKnownHostsFile "{(self.folder / "known_hosts").as_posix()}"\n'
            f'  GlobalKnownHostsFile "{(self.folder / "known_hosts").as_posix()}"\n', encoding="utf-8")
        self.result.update(prepare_image(self.args.image, self.folder, self.run_id,
                                        (self.folder / "id_ed25519.pub").read_bytes(),
                                        self.args.boot_timeout + 30,
                                        self.args.boot_timeout + self.args.report_timeout + 300
                                        + getattr(self.args, "debug_session_seconds", 0)
                                        + (2 * self.args.userspace_timeout + 300 if self.userspace else 0),
                                        userspace=self.userspace))

    def prepare_restore(self):
        require(self.args.confirm_device == self.args.serial,
                "Flash tests require --confirm-device matching --serial")
        self.result["stock_boot"] = prepare_restore_image(self.args.backup, self.folder,
                self.args.serial, self.baseline, self.folder / "test-boot.img")
        base = [self.args.fastboot, "-s", self.args.fastboot_serial or self.args.serial]
        self.result["manual_restore_commands"] = [
            [*base, "oem", device.unlock_token(self.baseline["chip_id"]), "unlock"],
            [*base, "flash", "boot", self.result["stock_boot"]["path"]], [*base, "reboot"],
        ]
        self.save()

    def check_restore_image(self):
        saved = self.result["stock_boot"]
        require(device.digest(Path(saved["path"])) == {key: saved[key] for key in ("size", "sha256")},
                "Pinned stock restoration image changed; refusing to flash")

    def check_fastboot_unlock(self):
        try:
            output = self.fastboot("oem", "device-info")
        except ValueError:
            output = self.fastboot("getvar", "unlocked")
        require(device.unlocked(output), "Bootloader is locked or unlock state is unknown")

    def authorize_flash(self):
        self.check_restore_image()
        self.check_fastboot_unlock()
        # This engineering ABL lists every partition but lacks getvar has-slot.
        inventory = self.fastboot("getvar", "all")
        names = re.findall(r"^[ \t]*(?:\(bootloader\)[ \t]*)?partition-size:(boot(?:_[ab])?):",
                           inventory, re.M)
        require(names == ["boot"],
                "Flash tests only support a non-slotted boot partition")
        size = fastboot_value(inventory, "partition-size:boot")
        require(re.fullmatch(r"0x[0-9a-fA-F]+", size) and
                int(size, 16) == self.result["stock_boot"]["size"],
                "Live boot partition size differs from its original backup")
        # Engineering ABL's per-session command authorization; no flashing unlock/reset.
        self.fastboot("oem", device.unlock_token(self.baseline["chip_id"]), "unlock")

    def enter_fastboot(self):
        self.moved = True  # Even a command timeout may have rebooted the device.
        self.adb("reboot", "bootloader")
        serial = self.args.fastboot_serial or self.args.serial
        def detect(timeout):
            output = self.command([self.args.fastboot, "devices"], timeout)
            require(any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()),
                    "Selected fastboot serial is missing")
        self.wait("fastboot", self.args.boot_timeout, detect)
        self.check_fastboot_unlock()

    def boot(self):
        path = self.folder / "test-boot.img"
        require(device.digest(path)["sha256"] == self.result["boot_sha256"], "Test image changed before boot")
        if self.flash_mode:
            self.authorize_flash()
            # A failed or timed-out write may still have changed storage. Journal first.
            self.boot_dirty = True
            self.result["boot_restore"] = "required"
            self.result["boot_restore_required"] = True
            self.save()
            self.fastboot("flash", "boot", str(path), timeout=self.args.boot_timeout)
            self.boot_attempted = True
            self.fastboot("reboot")
            return
        self.boot_attempted = True
        self.fastboot("boot", str(path), timeout=self.args.boot_timeout)

    def connect(self, timeout):
        if getattr(self.args, "windows_ncm_python", None) and not self.ncm_initialized:
            serial = self.args.fastboot_serial or self.args.serial
            initialized = json.loads(self.command([self.args.windows_ncm_python,
                                     ROOT / "pico4/windows_ncm.py", "--serial", serial], timeout))
            require(initialized.get("status") == "initialized" and initialized.get("serial") == serial,
                    "Windows NCM initialization did not identify this headset")
            self.ncm_initialized = True
            self.result["windows_ncm"] = initialized
            self.save()
            # Return through wait() so initialization and SSH each receive a
            # fresh timeout bounded by the same connection deadline.
            raise ValueError("Windows NCM initialized; waiting for DHCP and SSH")
        self.authenticate(timeout)

    def authenticate(self, timeout):
        output = self.ssh("cat /etc/pico4-poc/test-run; cat /sys/devices/soc0/serial_number", timeout)
        lines = output.splitlines()
        require(len(lines) == 2 and lines[0] == self.run_id and device.chip_id(lines[1]) == self.baseline["chip_id"],
                "SSH endpoint is not the expected test run and headset")
        self.authenticated = True
        # Only a verified endpoint may cancel the connection deadline. The
        # independent maximum test duration still recovers a crashed runner.
        self.ssh("touch /run/pico4-poc/test-authenticated", timeout)

    def assertions(self, suffix=""):
        info = json.loads(self.ssh("cat /etc/pico4-poc/build-info"))
        require(info == self.result["build_info"], "Running build metadata differs from supplied image")
        require(self.ssh("uname -m") == "aarch64", "Linux is not running ARM64")
        release = self.ssh("uname -r")
        require(info.get("kernel", "").startswith(f"Linux version {release} "), "Unexpected running kernel")
        require(self.ssh("id -u") == "0", "SSH did not provide the POC root shell")
        require(self.ssh("printf 'SHELL-%s' $((6*7))") == "SHELL-42", "SSH shell execution failed")
        storage = self.ssh("for d in /sys/class/block/*; do [ -e \"$d\" ] || continue; "
                           "printf 'BLOCK %s %s\\n' \"${d##*/}\" \"$(cat \"$d/ro\")\"; done")
        (self.folder / f"storage{suffix}.txt").write_text(storage + "\n", encoding="utf-8")
        mounts = self.ssh("cat /proc/mounts")
        (self.folder / f"mounts{suffix}.txt").write_text(mounts + "\n", encoding="utf-8")
        installed = getattr(self, "installed", False)
        validate_mounts(mounts, installed)
        validate_storage(storage, data_device(mounts) if installed else "")
        if self.flash_mode:
            size = (self.folder / "test-boot.img").stat().st_size
            output = self.ssh(f"head -c {size} /dev/block/by-name/boot | sha256sum")
            (self.folder / f"boot-readback{suffix}.txt").write_text(output + "\n", encoding="utf-8")
            require(output.split() and output.split()[0] == self.result["boot_sha256"],
                    "Flashed test image failed readback verification")

    def http(self, endpoint, timeout=15):
        # The USB address must bypass desktop/environment HTTP proxies.
        url = f"http://{self.args.host}/{endpoint}"
        deadline = time.monotonic() + timeout
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=timeout) as response:
            require(response.status == 200 and response.url == url, "Unexpected HTTP response/redirect")
            data = bytearray()
            while True:
                require(time.monotonic() < deadline, "HTTP transfer exceeded its deadline")
                block = response.read1(65536)
                if not block:
                    return bytes(data)
                data.extend(block)
                require(len(data) <= 32 * 1024 * 1024, "HTTP response exceeds test limit")

    def diagnostics(self):
        status = self.http("cgi-bin/status")
        (self.folder / "http-status.txt").write_bytes(status)
        require(b"PICO 4 Linux POC" in status, "HTTP status page is not the POC")
        log = self.http("cgi-bin/log?boot")
        (self.folder / "boot.log").write_bytes(log)
        require(self.run_id.encode() in log, "HTTP log does not identify this boot")
        # Wait for the init-triggered probe instead of starting a competing probe.
        self.wait("hardware report", self.args.report_timeout,
                  lambda timeout: require(self.ssh("test -s /run/pico4-poc/report.tar.gz && echo READY", timeout)
                                          == "READY", "Report not ready"))
        report = self.http("cgi-bin/report")
        (self.folder / "report.tar.gz").write_bytes(report)
        validate_report(report, self.run_id)
        (self.folder / "dmesg.txt").write_text(self.ssh("dmesg"), encoding="utf-8")

    def graphics(self):
        from .graphics import validate_kms, validate_vulkan
        duration = self.args.graphics_seconds
        requested = self.args.graphics
        results, errors = {}, []
        for name in ("vulkan", "kms"):
            if requested not in (name, "all"):
                continue
            limit = 45 if name == "vulkan" else 2 * duration + 30
            command = ("VK_DRIVER_FILES=/usr/share/vulkan/icd.d/pico4-turnip.json "
                       f"timeout {limit} pico4-{name}-test" + (f" {duration}" if name == "kms" else ""))
            print(f">> graphics: {name}" + (f" ({duration} seconds per mode)" if name == "kms" else ""), flush=True)
            try:
                # Keep stderr separate so warnings cannot masquerade as JSON evidence.
                code = self.ssh(command + f" >/run/pico4-poc/{name}.json 2>/run/pico4-poc/{name}.log; echo EXIT=$?", limit + 10)
                for suffix in ("json", "log"):
                    content = self.ssh(f"cat /run/pico4-poc/{name}.{suffix}")
                    (self.folder / f"graphics-{name}.{suffix}").write_text(content, encoding="utf-8")
                value = json.loads((self.folder / f"graphics-{name}.json").read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    results[name] = value
                require(code == "EXIT=0", f"{name} test failed: {code}")
                validate_vulkan(value) if name == "vulkan" else validate_kms(value, duration)
                results[name] = value
            except (ValueError, OSError) as exc:
                results[name] = {**results.get(name, {}), "status": "failed", "error": str(exc)}
                errors.append(f"{name}: {exc}")
            self.result["graphics"] = results
            self.save()
        (self.folder / "graphics-dmesg.txt").write_text(self.ssh("dmesg"), encoding="utf-8")
        (self.folder / "graphics-display-state.txt").write_text(self.ssh(
            "cat /sys/class/drm/card0-DSI-1/enabled; cat /sys/class/backlight/panel0-backlight/actual_brightness"), encoding="utf-8")
        self.result["graphics_visual_confirmation"] = "not_recorded"
        # Graphics must preserve the same storage policy as the shell acceptance run.
        self.assertions()
        require(not errors, "; ".join(errors))

    def userspace_handoff(self):
        """Roadmap step 4: stream the userspace into zram, hand PID 1 to systemd, check it."""
        archive = self.args.userspace
        state = userspace_state(archive)
        digest = state["sha256"]
        self.result["userspace"] = state
        self.save()
        print(f">> userspace: sending {state['bytes'] >> 20} MiB", flush=True)
        started = time.monotonic()
        with archive.open("rb") as stream:
            output = self.ssh(f"pico4-userspace receive --sha256 {digest}", self.args.userspace_timeout, stdin=stream)
        require(output.splitlines()[-1:] == [f"received {digest}"], "Headset did not confirm the userspace archive")
        state["receive_seconds"] = round(time.monotonic() - started, 1)
        require(self.ssh("pico4-userspace switch", 30).splitlines()[-1:] == ["switching"], "Handoff was refused")
        state["switch"] = utc()
        self.save()

        def stage2(timeout):
            # Same pinned host key and test-run identity, but systemd as PID 1.
            # (Re-running the Windows NCM initialization here stalled UsbDk and
            # left the host's NCM and storage drivers in an error state.)
            self.authenticate(timeout)
            require(self.ssh("cat /proc/1/comm", timeout) == "systemd", "Stage 1 is still running")
            # Cancels stage 2's own connection deadline (recovery-policy.sh).
            self.ssh("touch /run/pico4-poc/userspace-authenticated", timeout)
        self.wait("userspace SSH", self.args.userspace_timeout, stage2)
        state["ssh_seconds"] = round(time.monotonic() - started, 1)
        self.userspace_checks(state)

    def userspace_checks(self, state):
        """Everything checked in a running stage 2, in the order each feature builds on the last."""
        self.installed = bool(state.get("installed"))
        output = self.ssh("pico4-userspace-check --wait 240; echo EXIT=$?", 300)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-check.json").write_text(report.strip() + "\n", encoding="utf-8")
        for name, command in (("journal", "journalctl -b --no-pager -o short-monotonic"),
                              ("units", "systemctl list-units --all --no-pager --plain"),
                              ("analyze", "systemd-analyze; systemd-analyze blame --no-pager | head -n 40"),
                              ("dmesg", "dmesg")):
            try:
                (self.folder / f"userspace-{name}.txt").write_text(self.ssh(command, 60), encoding="utf-8")
            except (ValueError, OSError) as exc:  # evidence is best effort, the check decides
                (self.folder / f"userspace-{name}.txt").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value = json.loads(report)
        state["check"] = {k: value.get(k) for k in ("status", "errors", "systemd", "versions", "vulkan", "fan", "power")}
        state["warnings"] = userspace.power_warnings(value.get("power"))
        self.save()
        for warning in state["warnings"]:
            print(f"   warning: {warning}", flush=True)
        require(code.strip() == "0", "Userspace check failed")
        userspace.validate_check(value, require_vulkan=state["gpu_firmware"], require_fan=True)
        # The storage and identity guarantees of stage 1 must hold in stage 2.
        self.assertions("-userspace")
        if state["sensor_data"]:
            self.head_imu(state)

    def head_imu(self, state):
        """Roadmap step 5: stream the head IMU through the sensor DSP (docs/xr.md)."""
        print(">> head IMU: sensor DSP and SSC stream", flush=True)
        for name, command in (("sensors-units", "systemctl status --no-pager -l pico4-qrtr-ns pico4-slpi "
                                                "pico4-pd-mapper pico4-sensorspd"),
                              ("sensors-list", "pico4-imu list accel gyro")):
            try:
                (self.folder / f"userspace-{name}.txt").write_text(self.ssh(command, 60), encoding="utf-8")
            except (ValueError, OSError) as exc:
                (self.folder / f"userspace-{name}.txt").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        output = self.ssh(f"pico4-imu stream --rate {userspace.IMU_RATE_HZ:g} --seconds 10 --check "
                          "--orientation; echo EXIT=$?", 120)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-imu.json").write_text(report.strip() + "\n", encoding="utf-8")
        value = json.loads(report)
        state["imu"] = {k: value.get(k) for k in ("status", "failures", "streams", "orientation")}
        self.save()
        require(code.strip() == "0", "Head IMU check failed")
        userspace.validate_imu(value)
        if state["openxr_runtime"]:
            self.openxr_pose(state, userspace.imu_tilt_deg(value))

    def openxr_pose(self, state, accel_tilt):
        """Head pose through the OpenXR loader and the Monado service with its PICO 4 driver (headless).

        Monado runs as a system service (docs/session.md); the root clients here
        reach its socket through MONADO_RUNTIME_DIR.
        """
        print(">> OpenXR head pose: Monado PICO 4 driver", flush=True)
        for name, command in (("session", "loginctl list-sessions --no-pager; loginctl user-status steamos --no-pager -l; "
                                          "runuser -u steamos -- env XDG_RUNTIME_DIR=/run/user/1000 "
                                          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
                                          "systemctl --user list-units --no-pager --plain"),):
            try:
                (self.folder / f"userspace-{name}.txt").write_text(self.ssh(command, 60), encoding="utf-8")
            except (ValueError, OSError) as exc:
                (self.folder / f"userspace-{name}.txt").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        # The client starts the service (socket activation), a fresh one.
        output = self.ssh(f"systemctl stop pico4-monado.service 2>/dev/null; XDG_RUNTIME_DIR={userspace.MONADO_RUNTIME_DIR} "
                          "pico4-openxr-pose 10 10 2>/run/pico4-poc/openxr-pose.log; "
                          "echo EXIT=$?", 180)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-openxr-pose.json").write_text(report.strip() + "\n", encoding="utf-8")
        try:
            (self.folder / "userspace-openxr-pose.log").write_text(
                self.ssh("cat /run/pico4-poc/openxr-pose.log", 30), encoding="utf-8")
        except (ValueError, OSError) as exc:
            (self.folder / "userspace-openxr-pose.log").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value = json.loads(report)
        state["openxr"] = {**{k: value.get(k) for k in (
            "status", "system", "runtime", "located", "orientation_tracked", "invalid_after_warmup",
            "final_tilt_from_vertical_deg", "heading_rate_deg_per_s")}, "accel_tilt_deg": round(accel_tilt, 3)}
        self.save()
        require(code.strip() == "0", "OpenXR head pose check failed")
        userspace.validate_openxr_pose(value, accel_tilt)
        self.openxr_display(state)

    def openxr_display(self, state):
        """Frames from an OpenXR client through Monado's compositor to both panels (KMS target).

        The client submits a projection and a quad layer, so Monado composes
        through its layer squasher, the heavier of its two paths.
        """
        print(">> OpenXR display: Monado compositor on the panels", flush=True)
        # A fresh service for this client: the KMS target's statistics cover the
        # service's lifetime, and an idle stretch before the client's frames
        # (the pose client above renders none) would count against the rates.
        output = self.ssh(f"systemctl stop pico4-monado.service 2>/dev/null; XDG_RUNTIME_DIR={userspace.MONADO_RUNTIME_DIR} "
                          f"pico4-openxr-display {userspace.DISPLAY_SECONDS} "
                          "2>/run/pico4-poc/openxr-display.log; echo EXIT=$?", 120)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-openxr-display.json").write_text(report.strip() + "\n", encoding="utf-8")
        # The service's KMS target writes its statistics when the service stops
        # (paths from pico4-monado.service); the socket starts it again for the
        # next client.
        kms_report = self.ssh(f"systemctl stop pico4-monado.service 2>/dev/null; cat {userspace.MONADO_RUNTIME_DIR}/kms.json", 60)
        (self.folder / "userspace-openxr-display-kms.json").write_text(kms_report.strip() + "\n", encoding="utf-8")
        for name, command in (("openxr-display.log", "cat /run/pico4-poc/openxr-display.log"),
                              ("monado-journal.txt", "journalctl -b --no-pager -o short-monotonic "
                                                     "-u pico4-monado.service -u pico4-session.service")):
            try:
                (self.folder / f"userspace-{name}").write_text(self.ssh(command, 30), encoding="utf-8")
            except (ValueError, OSError) as exc:
                (self.folder / f"userspace-{name}").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value, kms = json.loads(report), json.loads(kms_report)
        state["openxr_display"] = {
            **{k: value.get(k) for k in ("status", "system", "eye_size", "frames", "frame_hz", "late_frames",
                                         "max_frame_interval_ms")},
            "kms": {k: kms.get(k) for k in ("target", "mode", "format", "commits", "commit_hz", "vblank_hz",
                                            "repeated_vblanks", "retire_timeouts", "gpu_wait_ms", "eye_mean_rgb")}}
        self.save()
        require(code.strip() == "0", "OpenXR display check failed")
        userspace.validate_openxr_display(value, kms)
        if state.get("vr_display"):
            self.vr_display(state)

    def vr_display(self, state):
        """SteamVR's display path: the pico4-vrdisplay layer shows a VK_KHR_display swapchain on the panels.

        The test client drives the layer the way SteamVR's compositor does
        (docs/session.md); the Monado service above has released the display.
        """
        print(">> SteamVR display layer: VK_KHR_display on the panels", flush=True)
        output = self.ssh("systemctl stop pico4-monado.service 2>/dev/null; PICO4_VRDISPLAY=1 "
                          "PICO4_VRDISPLAY_STATS=/run/pico4-poc/vrdisplay.json "
                          "PICO4_VRDISPLAY_DUMP=/run/pico4-poc/vrdisplay.ppm "
                          f"pico4-vrdisplay-test {userspace.VR_DISPLAY_SECONDS} "
                          "2>/run/pico4-poc/vrdisplay.log; echo EXIT=$?", 120)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-vrdisplay.json").write_text(report.strip() + "\n", encoding="utf-8")
        stats_report = self.ssh("cat /run/pico4-poc/vrdisplay.json", 30)
        (self.folder / "userspace-vrdisplay-stats.json").write_text(stats_report.strip() + "\n", encoding="utf-8")
        try:
            (self.folder / "userspace-vrdisplay.log").write_text(
                self.ssh("cat /run/pico4-poc/vrdisplay.log", 30), encoding="utf-8")
        except (ValueError, OSError) as exc:
            (self.folder / "userspace-vrdisplay.log").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value, stats = json.loads(report), json.loads(stats_report)
        state["vrdisplay"] = {
            **{k: value.get(k) for k in ("status", "display", "mode", "acquire", "frames", "frame_hz",
                                         "vblank_hz", "counter")},
            "layer": {k: stats.get(k) for k in ("target", "fake", "mode", "format", "commits", "commit_hz",
                                                "vblank_hz", "retire_timeouts", "gpu_timeouts", "eye_mean_rgb")}}
        self.save()
        require(code.strip() == "0", "SteamVR display layer check failed")
        userspace.validate_vr_display(value, stats)
        self.vr_sync(state)
        if state.get("steamvr"):
            self.valve_session(state)

    def vr_sync(self, state):
        """Semaphores shared between processes, as SteamVR's compositor shares them.

        Two processes of the session user share a timeline semaphore as an
        OPAQUE_FD handle, which the display layer emulates over sync files
        (pico4-vrsync.c): the kernel has no object that could carry it.
        """
        print(">> SteamVR semaphores: a timeline shared between processes", flush=True)
        output = self.ssh(f"runuser -u {userspace.SESSION_USER} -- env PICO4_VRDISPLAY=1 "
                          f"VK_DRIVER_FILES={userspace.TURNIP_ICD} pico4-vrsync-test "
                          "2>/run/pico4-poc/vrsync.log; echo EXIT=$?", 120)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-vrsync.json").write_text(report.strip() + "\n", encoding="utf-8")
        try:
            (self.folder / "userspace-vrsync.log").write_text(self.ssh("cat /run/pico4-poc/vrsync.log", 30),
                                                             encoding="utf-8")
        except (ValueError, OSError) as exc:
            (self.folder / "userspace-vrsync.log").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value = json.loads(report)
        state["vrsync"] = {k: value.get(k) for k in ("status", "gpu_wait", "host_wait", "host_signal", "early_wait",
                                                     "work_ms", "early_held_ms")}
        self.save()
        require(code.strip() == "0", "Semaphore sharing check failed")
        userspace.validate_vr_sync(value)

    def valve_session(self, state):
        """Roadmap step 6: Valve's VR session with gamescope and SteamVR on the panels (docs/session.md).

        The check runs gamescope-session.target briefly (the GPU stays busy and
        nothing cools the headset actively), then returns to the configured
        session. driver_monado gives SteamVR the headset and controllers.
        """
        print(">> Valve's session: gamescope and SteamVR with driver_monado", flush=True)
        # The tests before it warmed the headset; let it cool first (bounded).
        started, hottest = time.monotonic(), None
        while True:
            try:
                hottest = float(self.ssh("cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | "
                                         "awk '$1 < 200000 && $1 > m {m = $1} END {print m / 1000}'", 30))
            except ValueError:
                break
            if hottest < userspace.VALVE_SESSION_START_C or \
                    time.monotonic() - started > userspace.VALVE_SESSION_COOLDOWN_SECONDS:
                break
            time.sleep(5)
        state["valve_session_cooldown"] = {"seconds": round(time.monotonic() - started), "hottest_c": hottest}
        self.save()
        # Its stderr (systemctl's notes among it) stays out of the JSON.
        output = self.ssh(f"pico4-valve-session-check --seconds {userspace.VALVE_SESSION_SECONDS} "
                          "2>/run/pico4-poc/valve-session.log; echo EXIT=$?", 400)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-valve-session.json").write_text(report.strip() + "\n", encoding="utf-8")
        logs = f"/home/{userspace.SESSION_USER}/.local/share/Steam/logs"
        for name, command in (("valve-session.log", "cat /run/pico4-poc/valve-session.log"),
                              ("valve-vrcompositor.txt", f"cat {logs}/vrcompositor.txt"),
                              ("valve-vrserver.txt", f"tail -n 400 {logs}/vrserver.txt"),
                              ("valve-vrdisplay.txt", f"cat {logs}/pico4-vrdisplay.txt"),
                              ("valve-journal.txt", "journalctl -b --no-pager -o short-monotonic _UID=1000 "
                                                    "| tail -n 2000")):
            try:
                (self.folder / f"userspace-{name}").write_text(self.ssh(command, 60), encoding="utf-8")
            except (ValueError, OSError) as exc:
                (self.folder / f"userspace-{name}").write_text(f"unavailable: {exc}\n", encoding="utf-8")
        value = json.loads(report)
        layer = value.get("layer") if isinstance(value.get("layer"), dict) else {}
        state["valve_session"] = {
            **{k: value.get(k) for k in ("status", "errors", "startup_seconds", "session", "compositor", "devices",
                                         "max_temp_c", "fan")},
            "layer": {k: layer.get(k) for k in ("target", "commits", "commit_hz", "vblank_hz", "late",
                                                "retire_timeouts", "gpu_timeouts", "gpu_wait_ms",
                                                "present_wait_timeouts", "eye_mean_rgb")},
            "performance": value.get("performance")}
        self.save()
        print(f"   integration: {value.get('status')}; {userspace.format_valve_performance(value.get('performance'))}",
              flush=True)
        require(code.strip() == "0", "Valve session check failed")
        userspace.validate_valve_session(value)

    def development_session(self):
        """Keep one authenticated boot available for bounded userspace iteration."""
        duration = self.args.debug_session_seconds
        stop_file = self.folder / "end-session"
        state = {"status": "active", "started": utc(), "duration_seconds": duration,
                 "deadline": (datetime.datetime.now(datetime.timezone.utc)
                              + datetime.timedelta(seconds=duration)).isoformat(),
                 "end_session_file": str(stop_file)}
        self.result.update(run_kind="development", graphics_acceptance="not_evaluated", development_session=state)
        self.save()
        print(f">> development session ready for {duration} seconds; create {stop_file} to finish", flush=True)
        deadline, next_check = time.monotonic() + duration, 0.0
        try:
            while True:
                now = time.monotonic()
                if stop_file.exists() or now >= deadline:
                    state["end_reason"] = "requested" if stop_file.exists() else "deadline"
                    break
                if now >= next_check:
                    self.authenticate(min(10, deadline - now))
                    next_check = now + 15
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            self.assertions()
            state["status"] = "completed"
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status="failed", error=str(exc) or "Interrupted")
            raise
        finally:
            state["finished"] = utc()
            self.save()
            try:
                (self.folder / "development-dmesg.txt").write_text(self.ssh("dmesg", timeout=10), encoding="utf-8")
            except (ValueError, OSError):
                pass

    def recover(self):
        self.result["recovery"] = "attempting"
        self.save()
        if self.boot_attempted and not self.authenticated:
            try:
                self.authenticate(10)
            except (ValueError, OSError):
                pass
        if self.authenticated:
            try:
                # Logs are best effort and must not prevent the recovery attempt.
                (self.folder / "recovery-boot.log").write_text(
                    self.ssh("cat /run/pico4-poc/boot.log", timeout=10), encoding="utf-8")
            except (ValueError, OSError):
                pass
            try:
                target = " bootloader" if self.boot_dirty else ""
                self.ssh(f"(sleep 2; pico4-reboot{target}) </dev/null >/dev/null 2>&1 &", timeout=10)
            except (ValueError, OSError):
                pass  # A dropped SSH connection can mean the reboot already began.
        if self.boot_dirty:
            self.restore_boot()
        rebooted_fastboot = False
        def stock(timeout):
            nonlocal rebooted_fastboot
            try:
                identity = self.identity(timeout)
                require(identity == self.baseline, "Returned Android identity does not match original headset/build")
                return identity
            except (ValueError, OSError):
                if not rebooted_fastboot:
                    serial = self.args.fastboot_serial or self.args.serial
                    output = self.command([self.args.fastboot, "devices"], timeout)
                    if any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()):
                        self.fastboot("reboot", timeout=timeout)
                        rebooted_fastboot = True
                raise ValueError("Waiting for original PICO OS to complete boot")
        self.wait("stock PICO OS recovery", self.args.recovery_timeout, stock)
        self.result["recovery"] = "verified"
        if self.result["boot_restore"] == "flash_acknowledged":
            self.result["boot_restore"] = "android_boot_verified"

    def restore_boot(self):
        self.result["boot_restore"] = "attempting"
        self.save()
        serial = self.args.fastboot_serial or self.args.serial
        def ready(timeout):
            output = self.command([self.args.fastboot, "devices"], timeout)
            if any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()):
                return
            # A partial/failed write could still have left a bootable stock image.
            identity = self.identity(timeout)
            require(identity == self.baseline, "Unexpected Android identity during restoration")
            self.adb("reboot", "bootloader", timeout=timeout)
            raise ValueError("Waiting for bootloader to restore original boot image")
        self.wait("fastboot for stock boot restoration", self.args.recovery_timeout, ready)
        self.authorize_flash()
        self.fastboot("flash", "boot", self.result["stock_boot"]["path"], timeout=self.args.boot_timeout)
        self.boot_dirty = False
        self.result["boot_restore"] = "flash_acknowledged"
        self.result["boot_restore_required"] = False
        self.save()
        self.fastboot("reboot")

    def execute(self):
        failed = False
        try:
            if self.args.command != "preflight":
                self.step("prepare test boot image", self.prepare)
            self.baseline = self.step("identify stock headset", self.identity)
            self.result["identity"] = self.baseline
            if self.flash_mode:
                self.step("verify and pin stock boot restoration image", self.prepare_restore)
            rootfs = self.args.rootfs if self.args.command == "preflight" else self.folder / "rootfs.tar"
            self.step("userspace preflight", lambda: self.preflight(rootfs))
            if self.args.command != "preflight":
                self.step("enter unlocked fastboot", self.enter_fastboot)
                self.step("flash test boot image" if self.flash_mode else "RAM boot", self.boot)
                self.step("SSH identity", lambda: self.wait("SSH", self.args.boot_timeout, self.connect))
                self.step("Linux shell and read-only storage", self.assertions)
                self.step("HTTP and diagnostic report", self.diagnostics)
                if getattr(self.args, "graphics", None):
                    self.step("native graphics", self.graphics)
                if self.userspace:
                    self.step("SteamOS-compatible userspace", self.userspace_handoff)
                if getattr(self.args, "debug_session_seconds", 0):
                    self.step("bounded development session", self.development_session)
        except (Exception, KeyboardInterrupt) as exc:
            failed = True
            self.result["error"] = str(exc) or "Interrupted"
        finally:
            if self.moved:
                try:
                    self.step("return to original PICO OS", self.recover)
                except (Exception, KeyboardInterrupt) as exc:
                    failed = True
                    self.result["recovery"] = "failed"
                    self.result["recovery_error"] = str(exc) or "Interrupted"
            self.result["boot_restore_required"] = self.boot_dirty
            self.result.update(status="failed" if failed else "passed", finished=utc())
            self.save()
            # A per-run credential must not remain usable after the test.
            (self.folder / "id_ed25519").unlink(missing_ok=True)
        print(f"{self.result['status'].upper()}: {self.folder / 'result.json'}", flush=True)
        return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pico4 hwtest", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run", "flash-run"):
        p = sub.add_parser(name)
        p.add_argument("--serial", required=name != "preflight", help="authorized ADB serial (preflight can select a sole device)")
        p.add_argument("--adb", default="adb")
        p.add_argument("--out", type=Path, default=ROOT / "out" / ("hardware-" + uuid.uuid4().hex[:12]),
                       help="new result directory (never overwritten)")
        if name == "preflight":
            p.add_argument("--rootfs", type=Path, required=True)
        else:
            p.add_argument("--image", type=Path, required=True)
            p.add_argument("--fastboot", default="fastboot")
            p.add_argument("--fastboot-serial", help="explicit fastboot serial if different from ADB")
            p.add_argument("--ssh", default="ssh")
            p.add_argument("--ssh-keygen", default="ssh-keygen")
            p.add_argument("--windows-ncm-python", help="Python with the windows-ncm extra (pyproject.toml); enable UsbDk NCM initialization")
            activity = p.add_mutually_exclusive_group()
            activity.add_argument("--graphics", choices=("kms", "vulkan", "all"),
                                  help="run optional native graphics tests; requires a graphics-enabled image")
            activity.add_argument("--debug-session-seconds", type=int, default=0, metavar="SECONDS",
                                  help="keep Linux available for userspace iteration (60-1800 seconds), then restore")
            p.add_argument("--graphics-seconds", type=int, default=10, help="KMS measurement duration per mode (3-120 seconds)")
            p.add_argument("--userspace", type=Path, metavar="ARCHIVE",
                           help="pico4-userspace.tar.zst from scripts/build-userspace.sh: hand over to systemd and check it")
            p.add_argument("--userspace-timeout", type=int, default=600,
                           help="seconds for receiving the archive and for the systemd boot (default: %(default)s)")
            p.add_argument("--host", default="172.16.42.1", help="headset USB IPv4 address")
            for flag, default in (("boot", 180), ("report", 180), ("recovery", 420)):
                p.add_argument(f"--{flag}-timeout", type=int, default=default)
            if name == "flash-run":
                p.add_argument("--backup", type=Path, required=True, help="verified original device backup")
                p.add_argument("--confirm-device", required=True, help="repeat --serial to authorize boot writes")
    args = ap.parse_args(argv)
    try:
        for tool in ("adb", "fastboot", "ssh", "ssh_keygen"):
            if hasattr(args, tool):
                require(shutil.which(getattr(args, tool)), f"Required executable missing: {getattr(args, tool)}")
        if args.command != "preflight":
            require(3 <= args.graphics_seconds <= 120, "Graphics duration must be between 3 and 120 seconds")
            if args.userspace:
                require(args.userspace.is_file() and args.userspace.with_name("pico4-userspace.manifest.json").is_file(),
                        "--userspace needs pico4-userspace.tar.zst with its manifest beside it")
                require(60 <= args.userspace_timeout <= 1800, "Userspace timeout must be between 60 and 1800 seconds")
            require(args.debug_session_seconds == 0 or 60 <= args.debug_session_seconds <= 1800,
                    "Development sessions must be between 60 and 1800 seconds")
            ipaddress.IPv4Address(args.host)
            if args.windows_ncm_python:
                require(sys.platform == "win32", "--windows-ncm-python requires Windows")
                require(shutil.which(args.windows_ncm_python), "Windows NCM Python executable is missing")
                # CreateProcess rejects relative paths written with forward slashes.
                args.windows_ncm_python = str(Path(shutil.which(args.windows_ncm_python)).resolve())
            require(all(getattr(args, f"{n}_timeout") > 0 for n in ("boot", "report", "recovery")),
                    "Timeouts must be positive")
            if args.command == "flash-run":
                require(args.confirm_device == args.serial, "--confirm-device must match --serial")
        elif args.serial is None:
            args.serial = device.select_device(args.adb, None)
        return Runner(args).execute()
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
