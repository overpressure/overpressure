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

from . import bootimg, compress, cpio, device

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


def validate_storage(output: str) -> None:
    rows = [line.split() for line in output.splitlines()]
    require(rows and all(len(r) == 3 and r[0] == "BLOCK" and r[2] in ("0", "1") for r in rows),
            "Storage protection failed: missing or invalid block-device inventory")
    physical = [r for r in rows if not r[1].startswith(("loop", "ram", "zram", "nbd"))]
    require(physical, "No physical storage detected")
    require(all(r[2] == "1" for r in physical), "Storage protection failed: writable disk or partition")


def validate_mounts(output: str) -> None:
    rows = [row.split() for row in output.splitlines()]
    require(rows and all(len(r) == 6 for r in rows), "Invalid mount inventory")
    roots = [r for r in rows if r[1] == "/"]
    require(roots and all(r[2] in ("rootfs", "tmpfs", "ramfs") for r in roots),
            "Root filesystem is not RAM-backed")
    for row in rows:
        if row[0].startswith("/dev/"):
            require("rw" not in row[3].split(","), "A device-backed filesystem is mounted writable")


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
                  connect_timeout: int = 210, test_timeout: int = 900) -> dict:
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
        self.baseline = None
        self.boot_dirty = False
        self.result.update(boot_method="flash" if self.flash_mode else "ram" if args.command == "run" else "none",
                           boot_restore="not_needed", boot_restore_required=False)
        self.save()

    def save(self):
        temp = self.folder / "result.json.tmp"
        temp.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.folder / "result.json")

    def command(self, argv, timeout=30):
        self.count += 1
        prefix = self.folder / f"command-{self.count:04d}"
        Path(str(prefix) + ".json").write_text(json.dumps({"argv": list(map(str, argv)),
                    "timeout": timeout, "started": utc()}, indent=2), encoding="utf-8")
        try:
            process = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     timeout=timeout)
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

    def ssh(self, command, timeout=30):
        return self.command([self.args.ssh, "-F", self.folder / "ssh_config", "-i", self.folder / "id_ed25519",
                             "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
                             "-o", "StrictHostKeyChecking=accept-new", "-o",
                             f'UserKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"', "-o",
                             f'GlobalKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"',
                             f"root@{self.args.host}", command], timeout)

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
        self.command([self.args.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", self.folder / "id_ed25519"])
        (self.folder / "ssh_config").write_text("", encoding="utf-8")
        self.result.update(prepare_image(self.args.image, self.folder, self.run_id,
                                        (self.folder / "id_ed25519.pub").read_bytes(),
                                        self.args.boot_timeout + 30,
                                        self.args.boot_timeout + self.args.report_timeout + 300))

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

    def authenticate(self, timeout):
        output = self.ssh("cat /etc/pico4-poc/test-run; cat /sys/devices/soc0/serial_number", timeout)
        lines = output.splitlines()
        require(len(lines) == 2 and lines[0] == self.run_id and device.chip_id(lines[1]) == self.baseline["chip_id"],
                "SSH endpoint is not the expected test run and headset")
        self.authenticated = True
        # Only a verified endpoint may cancel the connection deadline. The
        # independent maximum test duration still recovers a crashed runner.
        self.ssh("touch /run/pico4-poc/test-authenticated", timeout)

    def assertions(self):
        info = json.loads(self.ssh("cat /etc/pico4-poc/build-info"))
        require(info == self.result["build_info"], "Running build metadata differs from supplied image")
        require(self.ssh("uname -m") == "aarch64", "Linux is not running ARM64")
        release = self.ssh("uname -r")
        require(info.get("kernel", "").startswith(f"Linux version {release} "), "Unexpected running kernel")
        require(self.ssh("id -u") == "0", "SSH did not provide the POC root shell")
        require(self.ssh("printf 'SHELL-%s' $((6*7))") == "SHELL-42", "SSH shell execution failed")
        storage = self.ssh("for d in /sys/class/block/*; do [ -e \"$d\" ] || continue; "
                           "printf 'BLOCK %s %s\\n' \"${d##*/}\" \"$(cat \"$d/ro\")\"; done")
        (self.folder / "storage.txt").write_text(storage + "\n", encoding="utf-8")
        validate_storage(storage)
        mounts = self.ssh("cat /proc/mounts")
        (self.folder / "mounts.txt").write_text(mounts + "\n", encoding="utf-8")
        validate_mounts(mounts)
        if self.flash_mode:
            size = (self.folder / "test-boot.img").stat().st_size
            output = self.ssh(f"head -c {size} /dev/block/by-name/boot | sha256sum")
            (self.folder / "boot-readback.txt").write_text(output + "\n", encoding="utf-8")
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
                self.step("SSH identity", lambda: self.wait("SSH", self.args.boot_timeout, self.authenticate))
                self.step("Linux shell and read-only storage", self.assertions)
                self.step("HTTP and diagnostic report", self.diagnostics)
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
    ap = argparse.ArgumentParser(description=__doc__)
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
            ipaddress.IPv4Address(args.host)
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
