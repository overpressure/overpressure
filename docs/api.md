# Headset interface reference

**TL;DR.** Every name a headset may use in headset interface 1.9, `overpressure.api.v1`: its
modules, and in each its functions and classes with their signatures, dataclass fields and constants, each
with the first paragraph of its documentation. A headset imports only these
(`from overpressure.api.v1 import REPO, qualcomm`, `from overpressure.api.v1.headset import Headset, Step`).
The rules (a minor only adds, a break makes a new major) are in
[adding-a-headset.md](adding-a-headset.md#the-headset-interface), what each minor added in
[what a headset may use](adding-a-headset.md#what-a-headset-may-use): a headset made for an older minor
uses only what that minor had. The source links lead to the code, which says more than its first paragraph.

`python scripts/api-reference.py` writes this file from the code; a test fails when it is out of date.

## Modules

| Module | What it is |
| --- | --- |
| [`overpressure.api.v1`](#overpressureapiv1) | Headset interface 1 (overpressure.headset.SUPPORTED names its newest minor): what a headset may use. |
| [`android`](#android) | adb and fastboot, fail-closed, each command naming the serial |
| [`assets`](#assets) | a headset's assets out of its stock folder |
| [`avb`](#avb) | Android Verified Boot footers and vbmeta |
| [`backup`](#backup) | verified backups, their archives, and the window's words for them |
| [`bootimg`](#bootimg) | Android boot images |
| [`compress`](#compress) | the compressions images use |
| [`contract`](#contract) | the headset runtime's contract on the PC's side, since 1.8 |
| [`cpio`](#cpio) | cpio archives (ramdisks) |
| [`doctor`](#doctor) | dependency checks |
| [`evidence`](#evidence) | runs on the headset that record every step |
| [`ext4`](#ext4) | ext4 images, read without mounting |
| [`fastboot`](#fastboot) | flashing through fastboot, the RAM boot |
| [`fat`](#fat) | FAT images, read without mounting |
| [`headset`](#headset) | the headset, its steps, workflows and finished page |
| [`install`](#install) | the installer: stages, tasks, asks and the context a task runs in |
| [`kernelimage`](#kernelimage) | a boot image with another kernel, since 1.7 |
| [`kernelinfo`](#kernelinfo) | a kernel image's version and configuration |
| [`kernelpin`](#kernelpin) | a headset's pin of overpressure/linux and its contracts, since 1.7 |
| [`localconfig`](#localconfig) | a headset's settings in .env |
| [`ota`](#ota) | a vendor's OTA: boot images and partitions, pinned members |
| [`pacman`](#pacman) | pacman's repositories on any host, since 1.7 |
| [`payload`](#payload) | Android A/B OTA payloads |
| [`power`](#power) | this computer's power, and the headset's battery before writes, 1.6 |
| [`prefetch`](#prefetch) | a headset's downloads before an install, since 1.7 |
| [`process`](#process) | bounded waits and running tools |
| [`progress`](#progress) | progress lines |
| [`qualcomm`](#qualcomm) | Qualcomm: SoC ID, EDL, partition maps, verified writes |
| [`runner`](#runner) | a step's command line |
| [`ssh`](#ssh) | SSH with the project's key |
| [`stage1`](#stage1) | an Alpine stage 1 in a stock boot image |
| [`storage`](#storage) | the read-only storage policy |
| [`tools`](#tools) | the pinned host tools |
| [`usbaccess`](#usbaccess) | USB access on Linux, a headset's USB modes |
| [`usblink`](#usblink) | a headset's USB network link on a Linux computer, since 1.6 |
| [`web`](#web) | HTTPS downloads, since 1.5 |
| [`wifi`](#wifi) | the owner's Wi-Fi for a headset's installed system, since 1.6 |
| [`windows_ncm`](#windowsncm) | Windows' USB network link |

## overpressure.api.v1

The interface's package itself ([source](../overpressure/api/v1/__init__.py)).

- `REPO`: The repository root: .env and the private, ignored folders (out/, cache/, assets/, backup/).

## android

`overpressure.api.v1.android`: adb and fastboot, fail-closed, each command naming the serial ([source](../overpressure/android.py)).

- `DEVICE_ROW_FIELDS = 2`: The fields of a `devices` line that matter: the serial and the state.
- `run(command: list[str], timeout: int = 60) -> str`: Adb or fastboot, logged to stderr; a nonzero exit, FAILED or ERROR raises ValueError.
- `release_usb(adb: str, runner: Callable[[list[str]], object], platform: str | None = None) -> None`: Stop the adb server (not on Windows), so that fastboot reaches the device.
- `device_rows(output: str, states: Sequence[str] = ("device", "unauthorized", "offline")) -> dict[str, str]`: Return the serial and state of each device that adb's or fastboot's devices lists in @p states, in its order.
- `select_device(tool: str, serial: str | None, runner: Callable[[list[str]], str] | None = None) -> str`: Return the serial to use: @p serial when it is attached and usable, else the only attached device.
- `unlocked(output: str, *, critical: bool = False) -> bool`: Tell whether fastboot's device-info or getvar output reports the bootloader unlocked.
- `check_unlock(fastboot: str, serial: str, *, critical: bool = False, runner: Callable[[list[str]], str] | None = None) -> None`: Raise unless the bootloader reports itself unlocked (and critical-unlocked when asked).
- `fastboot_value(output: str, name: str) -> str`: One getvar variable's value; missing or repeated raises ValueError.
- `executable(name: str) -> str`: Return the absolute path for a relative tool path; bare names stay PATH lookups.
- `partition_names(inventory: str, name: str) -> list[str]`: Return the partitions named @p name, with or without a slot suffix, in fastboot's getvar all.
- `single_partition_size(inventory: str, name: str) -> int | None`: Return the size of partition @p name from fastboot's getvar all, read as hexadecimal.
- `wait_booted(adb: str, serial: str, runner: Callable[..., str], clock: Timing, *, seconds: float = 300, system: str = "Android") -> str`: Wait until Android (@p system names it in messages) has finished booting; return its build fingerprint.

## assets

`overpressure.api.v1.assets`: a headset's assets out of its stock folder ([source](../overpressure/assets.py)).

- `SCHEMA = 2`
- `CHUNK = 1 << 22`
- `POSIX_MODES = os.name == "posix"`: Whether files here carry POSIX permissions (Windows keeps only a read-only flag).
- class `AssetError(RuntimeError)`: A stock file is missing or not the one its manifest names, or an asset is not where it should be.
- class `StockFolder`: A stock firmware folder: its manifest, its images checked against it, and readers for them.
  - `check(self, *names: str) -> None`: Check that each named file is there, not empty, and has the SHA-256 the manifest records.
  - `ext4(self, name: str) -> Ext4`: Return the checked ext4 image @p name, open for reading.
  - `fat(self, name: str) -> Fat`: Return the checked FAT image @p name, open for reading.
  - `close(self) -> None`: Close the open images.
- class `BackupFolder(StockFolder)`: A headset's backup in a stock folder's place: the same images, read out of its partitions.
  - `check(self, *names: str) -> None`: Check each image's partition file against the backup's manifest (once per folder object).
  - `ext4(self, name: str) -> Ext4`: Return the ext4 file system the image @p name names, open for reading.
  - `fat(self, name: str) -> Fat`: Return the FAT file system the image @p name names, open for reading.
  - `close(self) -> None`: Close the partition files.
- `dump(fs: Ext4, path: str, dest: Path) -> None`: Copy @p path out of an ext4 image, as the extraction scripts' debugfs did.
- `copy_matching(fs: Fat, directory: str, patterns: Iterable[str], dest: Path) -> list[str]`: Copy the files of @p directory that match each pattern into @p dest, as mcopy did; return their names.
- `widen_modes(folder: Path) -> None`: Let everyone read @p folder's files and enter its directories, as `chmod -R u+rwX,go+rX` does.
- `manifest_files(output: Path) -> dict[str, str]`: Return every file below @p output but the manifests, with its SHA-256, in path order.
- `write_manifest(output: Path, firmware: str | None, sources: Mapping[str, str]) -> int`: Write @p output/manifest.json: the firmware, where each part came from, every file's SHA-256.

## avb

`overpressure.api.v1.avb`: Android Verified Boot footers and vbmeta ([source](../overpressure/avb.py)).

- `FOOTER = struct.Struct(">4sIIQQQ28x")`
- `FOOTER_MAGIC = b"AVBf"`
- `VBMETA_MAGIC = b"AVB0"`
- `footer(partition: bytes) -> tuple[int, int, int]`: Return (original image size, vbmeta offset, vbmeta size) of a partition image with a footer.
- `with_stock_vbmeta(stock_partition: bytes, image: bytes, block: int = 4096) -> bytes`: Return a whole partition image: @p image and the stock partition's vbmeta after it.

## backup

`overpressure.api.v1.backup`: verified backups, their archives, and the window's words for them ([source](../overpressure/backup.py)).

- `ARCHIVE_META = "overpressure-backup.json"`: The archive's first member: what it holds and for which headset.
- `ARCHIVE_SCHEMA = 1`
- `ARCHIVE_SUFFIX = ".overpressure-backup.tar"`: The suffix the window suggests for an export.
- `CHUNK = 1024 * 1024`
- `HOW_TO_IMPORT`
- `HOW_TO_EXPORT`
- `missing(folder: Path, needed_for: str = "") -> str`: Return the words for a backup that is not in @p folder on this computer, with how to import it.
- `digest(path: Path, count: Callable[[int], None] | None = None) -> dict`: Return a file's size and SHA-256, as a manifest records them; an empty file raises.
- `save_new(path: Path, data: dict) -> None`: Write DATA as JSON to PATH, which must not exist: a record is never overwritten.
- `verified_backup(folder: Path, count: Callable[[int], None] | None = None) -> dict`: Return a backup's manifest once every file in it matches; a mismatch raises (@p count: as digest's).
- `verified_backup_shown(folder: Path, out: Callable[[str], None] | None = None) -> dict`: Return verified_backup's manifest, printing progress lines (overpressure.progress) as it reads.
- `IMAGE_SUFFIXES = (".bin", ".img")`: The suffixes of a partition's image in a backup, besides its GPT and rawprogram metadata.
- `partition_names(manifest: dict, name: str) -> list[str]`: Return the files in a backup manifest that are images of partition @p name (exact, no slot).
- `partition_image(backup: Path, name: str) -> tuple[Path, dict]`: Return the one image of partition @p name in a backup and its manifest entry.
- `verified_partition(backup: Path, name: str) -> tuple[Path, bytes]`: Return the path and bytes of partition @p name's image in a backup, checked against its manifest.
- `export_archive(bundle: BackupSet, codename: str, target: Path, say: Callable[[str], None] = print) -> dict`: Write BUNDLE as one archive at TARGET (never over a file there); return its description.
- `read_meta(tar: tarfile.TarFile) -> dict`: Return an archive's description; a file that is no backup archive raises.
- `check_archive(path: Path, manifest: dict | None = None, count: Callable[[int], None] | None = None) -> dict`: Return the manifest an archive holds once every backup file in it matches it; a mismatch raises.
- `import_archive(source: Path, codename: str, here: BackupSet, say: Callable[[str], None] = print) -> BackupSet`: Put the backup archive SOURCE onto this computer where HERE (the headset's settings now) says; return it.
- `compact(folder: Path, say: Callable[[str], None] | None = None) -> tuple[int, int]`: Give the backup's zero runs back to the file system; return its bytes on disk before and after.
- `main(headset: Headset, argv: list[str]) -> int`: Run python -m overpressure backup export|import FILE, or backup compact.

## bootimg

`overpressure.api.v1.bootimg`: Android boot images ([source](../overpressure/bootimg.py)).

- `BOOT_MAGIC = b"ANDROID!"`
- `BOOT_NAME_SIZE = 16`
- `BOOT_ARGS_SIZE = 512`
- `BOOT_EXTRA_ARGS_SIZE = 1024`
- `BOOT_ID_SIZE = 32`
- `V3_ARGS_SIZE = BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE`
- `V3_PAGE_SIZE = 4096`
- `HDR_SIZE = {0: 1632, 1: 1648, 2: 1660, 3: 1580, 4: 1584}`
- `HEADER_RECOVERY_DTBO = 1`
- `HEADER_DTB = 2`
- `HEADER_V3 = 3`
- `HEADER_SIGNATURE = 4`
- `PAGE_SIZES = (2048, 4096, 8192, 16384)`
- class `BootImage` (@dataclass): A boot image's parts and header fields.
  - `header_version: int`
  - `page_size: int`
  - `kernel: bytes`
  - `ramdisk: bytes`
  - `second: bytes = b""`
  - `recovery_dtbo: bytes = b""`
  - `dtb: bytes = b""`
  - `boot_signature: bytes = b""`
  - `cmdline: bytes = b""`
  - `extra_cmdline: bytes = b""`
  - `name: bytes = b""`
  - `os_version: int = 0`
  - `kernel_addr: int = 0`
  - `ramdisk_addr: int = 0`
  - `second_addr: int = 0`
  - `tags_addr: int = 0`
  - `dtb_addr: int = 0`
  - `id: bytes = b""`
  - `avb_footer: bool = False`
  - property `full_cmdline(self) -> str`: Return the whole kernel command line.
  - property `os_version_str(self) -> str`: Return the OS version and patch level, readable.
  - `with_cmdline(self, cmdline: str) -> BootImage`: Return a copy with CMDLINE as the kernel command line, split across the fields as mkbootimg does.
  - `compute_id(self) -> bytes`: Return the SHA-1 image id exactly as AOSP mkbootimg computes it (v0-v2).
  - `pack(self) -> bytes`: Return the image as mkbootimg would write it.
- `parse(data: bytes) -> BootImage`: Take a boot image apart.
- `ramdisk_tree(image: BootImage) -> cpio.Tree`: Return the files of a boot image's ramdisk (any compression compress.decompress reads).
- `with_ramdisk(image: BootImage, tree: cpio.Tree, *, drop: tuple[str, ...] = (), add: Sequence[str] = (), failed: str = "the boot image failed validation") -> bytes`: Return @p image with @p tree as its ramdisk (gzip) and a changed kernel command line.
- `decode_os_version(value: int) -> str`: Return a header's packed OS version and patch level, readable.

## compress

`overpressure.api.v1.compress`: the compressions images use ([source](../overpressure/compress.py)).

- `LZ4_LEGACY_MAGIC = 0x184C2102`
- `LZ4_LENGTH_MORE = 15`
- `LZ4_BYTE_MORE = 255`
- `LZ4_MIN_MATCH = 4`
- `SIGNATURES`
- `detect(data: bytes) -> str`: Return the format of DATA by its magic: a name of SIGNATURES, "arm64-image" or "unknown".
- `decompress(data: bytes) -> tuple[bytes, bytes, str]`: Decompress the first stream in ``data``.
- `lz4_block_decompress(src: bytes | memoryview, out: bytearray) -> None`: Append the decompressed contents of one raw LZ4 block to ``out``.
- `lz4_legacy_decompress(data: bytes) -> tuple[bytes, bytes]`: Return the data of a legacy LZ4 stream (the kernel's lz4 -l) and the bytes after it.
- `lz4_frame_decompress(data: bytes) -> tuple[bytes, bytes]`: Return the data of an LZ4 frame and the bytes after it.
- `gzip_compress(data: bytes, level: int = 9) -> bytes`: Deterministic gzip (mtime 0, no file name).

## contract

`overpressure.api.v1.contract`: the headset runtime's contract on the PC's side, since 1.8 ([source](../overpressure/contract.py)).

- `RUN = "/run/overpressure"`: The runtime's state (tmpfs), its programs and its settings, on the headset.
- `LIB = "/usr/libexec/overpressure"`
- `ETC = "/etc/overpressure"`
- `CONTACT = f"{RUN}/host-contact"`: The uptime of the host's last command: the maintenance system's deadline counts from it (recovery-watch).
- `TEST_RUN = f"{ETC}/test-run"`: A test run's identity in its image, and the mark that its endpoint was proven (the deadline then stops).
- `TEST_AUTHENTICATED = f"{RUN}/test-authenticated"`
- `TEST_CONNECT_TIMEOUT = f"{ETC}/test-connect-timeout"`: How long a test run waits for its first contact and runs at most (seconds, its image's files).
- `TEST_TIMEOUT = f"{ETC}/test-timeout"`
- `BUILD_INFO = f"{ETC}/build-info"`: The maintenance system's build (JSON): its Alpine, its packages, its kernel.
- `HEADSET = f"{ETC}/headset"`: The headset's values the runtime sources (its name, its USB link, its programs).
- `WORD = "overpressure."`: The word prefix the runtime reads on the kernel command line.
- `word(name: str, value: object = None) -> str`: Return the kernel command line word overpressure.NAME, with =VALUE when one is given.

## cpio

`overpressure.api.v1.cpio`: cpio archives (ramdisks) ([source](../overpressure/cpio.py)).

- `NEWC_MAGIC = b"070701"`
- `NEWC_CRC_MAGIC = b"070702"`
- `TRAILER = "TRAILER!!!"`
- `MAX_SYMLINKS = 40`
- class `Node` (@dataclass): One filesystem object. ``mode`` holds the full st_mode (type + permissions).
  - `mode: int`
  - `data: bytes = b""`
  - `uid: int = 0`
  - `gid: int = 0`
  - `rdev: tuple[int, int] = (0, 0)`
  - `mtime: int = 0`
  - property `is_dir(self) -> bool`: Tell whether this is a directory.
  - property `is_file(self) -> bool`: Tell whether this is a regular file.
  - property `is_symlink(self) -> bool`: Tell whether this is a symbolic link.
- class `Tree` (@dataclass): A root filesystem held in memory, keyed by relative POSIX path.
  - `nodes: dict[str, Node] = field(default_factory=dict)`
  - `mtime: int = 0`
  - `mkdir(self, path: str, mode: int = 0o755, uid: int = 0, gid: int = 0) -> str`: Create ``path`` (and parents). Returns the symlink-resolved path.
  - `add_file(self, path: str, data: bytes, mode: int = 0o644, uid: int = 0, gid: int = 0) -> None`: Add a regular file, its parent directories made.
  - `add_symlink(self, path: str, target: str, uid: int = 0, gid: int = 0) -> None`: Add a symbolic link to TARGET.
  - `add_device(self, path: str, kind: str, major: int, minor: int, mode: int = 0o600) -> None`: Add a device node: KIND "c" for a character, "b" for a block device.
  - `get(self, path: str) -> Node | None`: Return the node at PATH (symlinks not followed), None if there is none.
  - `exists(self, path: str) -> bool`: Tell whether there is a node at PATH (symlinks not followed).
  - `resolve(self, path: str, _depth: int = 0) -> str`: Resolve symlinks in ``path`` (inside the tree, never on the host).
  - `to_tar(self, fileobj: IO[bytes]) -> None`: Write the tree as an uncompressed tar without device nodes (handy for inspecting the rootfs).
  - `to_cpio(self) -> bytes`: Serialise as a newc cpio archive (parents always precede children).
- `read_cpio(data: bytes) -> list[tuple[str, Node]]`: Parse a (possibly concatenated) newc cpio archive into (path, Node) entries.

## doctor

`overpressure.api.v1.doctor`: dependency checks ([source](../overpressure/doctor.py)).

- `MARK`
- `HEADSET = "working on the headset"`: What the checks of a headset's settings and tools are needed for, unless the headset names it.
- `EDL = "EDL"`
- `FETCH = "python -m overpressure tools"`: The fixes' common beginning: the pinned host tools.
- `BUILDS = "builds on this computer (build container)"`: What the build container's checks are needed for: builds on this computer, the owner's choice (a headset may build its own system, as the PICO 4 does), so they are never missing.
- class `Check` (@dataclass(frozen=True)): One thing the workflow needs, and whether this host has it.
  - `name: str`
  - `needed_for: str`: the part of the workflow that needs it
  - `status: str`: ok, missing (needed), optional (missing, not needed), warning
  - `detail: str = ""`: what was found, or why it is missing
  - `fix: str = ""`: how to get it, when it is not ok
- `run_tool(argv: Sequence[str], timeout: float = 30) -> tuple[int, str]`: Return a program's exit status and combined output; (127, reason) when it cannot start.
- `has_module(name: str) -> bool`: Tell whether this Python can import the top-level module @p name, without importing it.
- class `Host` (@dataclass): What the checks may ask of this host; tests pass fakes.
  - `platform: str = sys.platform`
  - `run: Callable[[Sequence[str], float], tuple[int, str]] = run_tool`
  - `which: Callable[[str], str | None] = shutil.which`
  - `linux_cache: dict[str, dict[str, str]] = field(default_factory=dict)`
  - `has_module: Callable[[str], bool] = has_module`
  - property `engine(self) -> str`: Return the container engine's program (overpressure/linux.py).
  - `container_ready(self) -> bool`: Tell whether the build container's image is built.
  - `linux(self, script: str, timeout: float = 90) -> dict[str, str]`: Return the KEY=VALUE lines that @p script prints in the build container (a probe: unnamed).
- `version_check(host: Host, name: str, argv: Sequence[str], needed_for: str, fix: str) -> Check`: Check a program that answers a version query: its first output line, or how to get it.
- `env_check(root: Path = REPO, needed_for: str = HEADSET) -> Check`: Check that .env, which names the headset's serials and private files, is there.
- `serial_checks(settings: DeviceSettings, needed_for: str = HEADSET, edl_needed_for: str = EDL) -> list[Check]`: Check that the headset's adb, fastboot and EDL serials are set.
- `backup_check(folder: Path, needed_for: str = HEADSET, fix: str = "python -m overpressure device backup") -> Check`: Check the EDL backup: its manifest, and every file in it present at its recorded size.
- `loader_check(settings: DeviceSettings, needed_for: str = EDL, fix: str = "the headset's signed Firehose loader") -> Check`: Check that the Firehose loader the loader setting names is there.
- `edl_tool_check(host: Host, settings: DeviceSettings, needed_for: str = EDL) -> Check`: Check that edl-ng is there: the edl setting's, the fetched one or one on PATH.
- `tool_checks(host: Host, settings: DeviceSettings, needed_for: str = HEADSET) -> list[Check]`: Check adb and fastboot by their version queries.
- `ncm_checks(host: Host, settings: DeviceSettings) -> list[Check]`: Windows only: the Python with pyusb and libusb-package, and UsbDk behind it.
- `link_check(host: Host, gadget: usblink.Gadget, needed_for: str = HEADSET, found: Sequence[usblink.Link] | None = None) -> list[Check]`: Linux only, while the headset's own system is plugged in: its USB network link has the headset's lease.
- `usb_check(host: Host, modes: Sequence[usbaccess.UsbMode], needed_for: str = HEADSET, *, dirs: Sequence[Path] = usbaccess.RULE_DIRS, release: Path = usbaccess.OS_RELEASE, groups: Iterable[str] | None = None) -> list[Check]`: Linux only: check that the installed udev rules let this user open each of the headset's USB @p modes.
- `window_check() -> Check`: Check CustomTkinter, which the window needs beside Tk (the gui extra).
- `PACKAGE_FIX`: The pinned packages (pyproject.toml) and what works without them.
- `package_checks(host: Host) -> list[Check]`: Check the pinned Python packages: brotli for the stock OTA, pyusb with libusb for EDL devices, truststore.
- `host_checks(host: Host) -> list[Check]`: Return the checks every headset's workflow shares.
- `ENGINE = "Docker or Podman"`: The one fix for the builds' engine, whichever the owner picks and however it runs its containers.
- `ENGINE_FIX`
- `BINFMT_FIX`
- `ENGINE_MEMORY = 8 << 30`: The memory the userspace build wants in the container: its C++ units take about 1.5 GiB a job.
- `MEMORY_FIX`
- `engine_memory(info: str) -> int | None`: Return the engine's memory in bytes from its info output, or None when it names none.
- `BINFMT_PROBE`: The aarch64 handler by its ELF magic, whatever it is named, with its flags; the engine's architecture.
- `container_checks(host: Host) -> list[Check]`: Check the build container, for builds on this computer: Docker or Podman answers, its image, the handler.
- `run(headset: Headset | None = None, host: Host | None = None) -> list[Check]`: Run every check: the host's, then the headset's.
- `failed(checks: Iterable[Check]) -> list[Check]`: Return the checks of what is needed and missing.
- `report(checks: Sequence[Check]) -> str`: Return the checks grouped by what they are needed for, the fixes below each missing one.

## evidence

`overpressure.api.v1.evidence`: runs on the headset that record every step ([source](../overpressure/evidence.py)).

- `HTTP_LIMIT = 32 * 1024 * 1024`: What one HTTP fetch may return.
- class `NoRedirect(HTTPRedirectHandler)`: Follow no redirect: the headset's address is the only one a run talks to.
  - `redirect_request(self, req: Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str) -> None`: Refuse every redirect.
- `utc() -> str`: Return the current time in UTC, as the result records it.
- `require(condition: object, message: str) -> None`: Raise ValueError with @p message unless @p condition holds.
- `http_get(url: str, timeout: float = 15, limit: int = HTTP_LIMIT) -> bytes`: Fetch @p url, bounded in time and size, without a proxy and without following a redirect.
- class `Runner`: A run whose steps, commands and outputs are recorded in a new results folder.
  - `save(self) -> None`: Write the result file, replacing the last one whole.
  - `command(self, argv: Sequence[str | os.PathLike[str]], timeout: float = 30) -> str`: Run a host program, its command line and output kept as evidence; return its output.
  - `step(self, name: str, operation: Callable[[], T]) -> T`: Run one step of the run, recorded in the result whether it passes or fails.
  - `wait(self, name: str, seconds: float, operation: Callable[[float], T]) -> T`: Retry @p operation, given the time left for each attempt, until it succeeds or @p seconds pass.

## ext4

`overpressure.api.v1.ext4`: ext4 images, read without mounting ([source](../overpressure/ext4.py)).

- `MAGIC = 0xEF53`
- `SUPERBLOCK = 1024`
- `ROOT_INODE = 2`
- `EXTENT_MAGIC = 0xF30A`
- `EXTENTS_FL = 0x80000`
- `INLINE_DATA_FL = 0x10000000`
- `INIT_MAX_LEN = 32768`: An extent longer than this is uninitialized: it reads as zeros.
- `I_BLOCK = 60`: The inode's i_block: 15 32-bit words, a fast symlink's target or the extent tree's root.
- `DIRECT_BLOCKS = 12`
- `GOOD_OLD_INODE_SIZE = 128`
- `MTIME_EXTRA_END = 12`: i_mtime_extra's place after the 128-byte inode: it is there when i_extra_isize reaches it.
- `INCOMPAT_FILETYPE = 0x2`
- `INCOMPAT_64BIT = 0x80`
- `REFUSED`: Incompatible features this reader refuses: they change where or how file data is stored.
- `CHUNK = 1 << 20`
- `FILE_TYPES`: Directory entry file types (with the filetype feature).
- class `Ext4Error(ValueError)`: The image is not ext4, uses what this reader refuses, or lacks what was asked for.
- class `Inode` (@dataclass(frozen=True)): One inode: its type and permissions, owner, size, times and where its data is.
  - `number: int`
  - `mode: int`
  - `uid: int`
  - `gid: int`
  - `size: int`
  - `mtime: int`: seconds since the epoch
  - `mtime_ns: int`: nanoseconds within that second (0 without the extra fields)
  - `links: int`
  - `flags: int`
  - `blocks: int`: 512-byte sectors in use (i_blocks_lo)
  - `file_acl: int`
  - `block: bytes`: i_block: the extent tree's root, the block map or a fast symlink's target
  - property `is_dir(self) -> bool`: Tell whether this is a directory.
  - property `is_file(self) -> bool`: Tell whether this is a regular file.
  - property `is_symlink(self) -> bool`: Tell whether this is a symbolic link.
  - property `permissions(self) -> int`: Return the permission bits (with setuid, setgid and sticky).
- class `Run` (@dataclass(frozen=True)): Contiguous file blocks: logical block, physical block (0: a hole), length, initialized.
  - `logical: int`
  - `physical: int`
  - `length: int`
  - `initialized: bool = True`
- class `Ext4`: An ext4 (or ext2/ext3) image, read through a seekable binary file.
  - classmethod `open(cls, path: str | os.PathLike[str]) -> Ext4`: Open the image at @p path (closed with the reader).
  - `close(self) -> None`: Close the image's file when this reader opened it.
  - `inode(self, number: int) -> Inode`: Return inode @p number.
  - `runs(self, inode: Inode) -> list[Run]`: Return where @p inode's data blocks are, in logical order (holes left out).
  - `chunks(self, inode: Inode, size: int = CHUNK) -> Iterator[bytes]`: Yield @p inode's data in pieces of at most @p size bytes; holes give zeros.
  - `data(self, inode: Inode) -> bytes`: Return @p inode's whole data.
  - `entries(self, inode: Inode) -> Iterator[tuple[str, int, str]]`: Yield a directory's entries as (name, inode number, type), without "." and "..".
  - `lookup(self, path: str) -> Inode`: Return the inode at absolute @p path; symbolic links on the way are not followed.
  - `exists(self, path: str) -> bool`: Tell whether @p path is in the image.
  - `listdir(self, path: str) -> list[str]`: Return the names in the directory at @p path, in the directory's order.
  - `read(self, path: str) -> bytes`: Return the regular file at @p path.
  - `link_target(self, inode: Inode) -> str`: Return a symbolic link's target: in i_block when short (a fast link), else in its data.
  - `readlink(self, path: str) -> str`: Return the target of the symbolic link at @p path.
  - `walk(self, path: str = "/") -> Iterator[tuple[str, Inode]]`: Yield (path, inode) for @p path and everything below it, a directory before its contents.
- `main(argv: list[str] | None = None) -> int`: Run the command line: cat IMAGE PATH writes the image's regular file PATH to stdout.

## fastboot

`overpressure.api.v1.fastboot`: flashing through fastboot, the RAM boot ([source](../overpressure/fastboot.py)).

- `NOT_IN_FASTBOOT`: A wait for fastboot that ran out. A headset showing its fastboot screen is sometimes not seen by the computer after the reboot into it (the USB link does not come up); unplugging and plugging the cable in brings it.
- `REFUSED`: How bootloaders say that they do not boot from RAM.
- class `Refused(RuntimeError)`: The bootloader has no `fastboot boot` (or refuses it): nothing was written.
- `run(argv: list[str], timeout: float) -> str`: Return a program's combined output; a nonzero exit or a FAILED line raises RuntimeError with it.
- `DEVICE_ROW_FIELDS = 2`: The fields of a `fastboot devices` line that matter: the serial and the state.
- class `Fastboot` (@dataclass): fastboot for one headset: every command names its serial.
  - `tool: str`
  - `serial: str`
  - `runner: Callable[[list[str], float], str] = run`
  - `present(self) -> bool`: Tell whether the headset is in fastboot: listed with its serial, and nothing else is.
  - `restart_bootloader(self, clock: Timing, leave: float = 30, back: float = 120) -> bool`: Restart the headset into its bootloader; return once it has left fastboot and is listed again.
- `SPARSE_ABOVE = 64 << 20`: Images above this size go to fastboot as sparse images: ABLs crash on large raw downloads (an engineering ABL on a 100 MiB recovery image).
- `flash(fastboot: Callable[..., str], partition: str, image: Path, *, above: int = SPARSE_ABOVE, sparse_path: Path | None = None) -> None`: Write @p image to @p partition: as a sparse image above @p above bytes, else raw.
- class `RamBootTarget`: What a headset supplies for a RAM boot (Headset.ram_boot).
  - `boot_timeout: float = 180`: Seconds for the transfer and the boot command, and for the image to come up.
  - `up_timeout: float = 180`
  - `refused_hint: str = ""`: Said when the bootloader refuses: where this headset boots from instead.
  - property `fastboot(self) -> Fastboot`: Return fastboot for this headset.
  - `image(self) -> Path`: Return the image to boot when none is given.
  - `enter(self) -> None`: Bring the headset into fastboot (from Android, say); called when it is not there.
  - `check_unlocked(self) -> None`: Raise unless the bootloader reports a state that may boot an unsigned image.
  - `reached(self) -> None`: Raise until the booted image answers; return once it does.
- `ram_boot(target: RamBootTarget, image: Path | None = None, log: Callable[[str], None] = print, clock: Timing | None = None) -> None`: Boot @p image (else the target's) from RAM and wait until it answers.

## fat

`overpressure.api.v1.fat`: FAT images, read without mounting ([source](../overpressure/fat.py)).

- `ENTRY = 32`
- `ATTR_VOLUME = 0x08`
- `ATTR_DIRECTORY = 0x10`
- `ATTR_LFN = 0x0F`
- `DELETED = 0xE5`
- `FAT12_CLUSTERS = 4085`: The FAT type follows from the number of data clusters (the specification's limits).
- `FAT16_CLUSTERS = 65525`
- `LFN_LAST = 0x40`: A long name entry's sequence number: the last (first stored) one has this bit.
- `LFN_CHARS = (1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30)`: The 13 UCS-2 characters of a long name entry, by their byte offsets.
- class `FatError(ValueError)`: The image is not FAT, is damaged, or lacks what was asked for.
- class `Entry` (@dataclass(frozen=True)): A directory entry: its long (or 8.3) name, the 8.3 name, kind, first cluster, size and time.
  - `name: str`
  - `short_name: str`
  - `is_dir: bool`
  - `cluster: int`
  - `size: int`
  - `mtime: dt.datetime | None`: the write time, local time as FAT keeps it; None when not valid
- class `Fat`: A FAT12, FAT16 or FAT32 image, read through a seekable binary file.
  - classmethod `open(cls, path: str | os.PathLike[str]) -> Fat`: Open the image at @p path (closed with the reader).
  - `close(self) -> None`: Close the image's file when this reader opened it.
  - `next_cluster(self, cluster: int) -> int | None`: Return the cluster after @p cluster in its chain, None at the chain's end.
  - `chain(self, first: int) -> Iterator[int]`: Yield the clusters of the chain that starts at @p first; a chain that comes back is refused.
  - `chunks(self, entry: Entry) -> Iterator[bytes]`: Yield a file's data, a cluster at a time.
  - `read_entry(self, entry: Entry) -> bytes`: Return a file's data.
  - `entries(self, directory: Entry | None = None) -> Iterator[Entry]`: Yield the entries of @p directory (None: the root), without ".", ".." and the volume label.
  - `lookup(self, path: str) -> Entry | None`: Return the entry at absolute @p path (None for the root directory).
  - `listdir(self, path: str = "/") -> list[str]`: Return the names in the directory at @p path, in the directory's order.
  - `read(self, path: str) -> bytes`: Return the file at @p path.
  - `glob(self, path: str, pattern: str) -> list[Entry]`: Return the files in the directory at @p path whose long or 8.3 name matches @p pattern.

## headset

`overpressure.api.v1.headset`: the headset, its steps, workflows and finished page ([source](../overpressure/headset.py)).

- `SUPPORTED: dict[int, int] = {1: 9}`: The headset interface, MAJOR.MINOR (docs/adding-a-headset.md#the-headset-interface): each major this overpressure runs, with its newest minor. A minor only adds to its major; a change that breaks one makes a new major, and the old one stays, as its module overpressure.api.vMAJOR, until it is retired (RETIRED).
- `API = f"{max(SUPPORTED)}.{SUPPORTED[max(SUPPORTED)]}"`: The newest interface: what a new headset is made for.
- `RETIRED: dict[int, str] = {}`: The majors this overpressure no longer runs, each with the last overpressure version that ran it.
- `MANIFEST = "headset.json"`: A headset folder's description of itself: devices/<codename>/headset.json.
- `RUNTIME_SINCE = (1, 8)`: The interface from which a headset runs on overpressure's runtime (docs/runtime.md) unless it says otherwise: one made for an older interface brings its own /init, PID 1 and preinit.
- class `Info` (@dataclass(frozen=True)): What names and describes a headset.
  - `codename: str`: devices/<codename>, also the --device name
  - `name: str`: the product's name
  - `soc: str = ""`
  - `summary: str = ""`
- class `Command` (@dataclass(frozen=True)): A command of the command line: a module whose main(argv) takes the rest of the words.
  - `module: str`
  - `help: str`
  - `keep: bool = False`
  - `headset: bool | tuple[str, ...] = False`
- class `Param` (@dataclass(frozen=True)): A parameter of a step: its value goes into the command line in place of {name}.
  - `name: str`
  - `label: str`
  - `default: str = ""`
  - `kind: str = "text"`: text, path (a file or folder), choice or flag
  - `choices: tuple[str, ...] = ()`
  - `help: str = ""`
  - `option: str = ""`: a flag's word, given when the flag is on ("1")
- class `Step` (@dataclass(frozen=True)): One action of the workflow, as the window offers it.
  - `key: str`
  - `title: str`
  - `group: str`: "Setup", "Image" or "Headset"
  - `argv: tuple[str, ...]`
  - `help: str = ""`
  - `params: tuple[Param, ...] = ()`
  - `script: bool = False`: argv[0] is a program in the headset's folder
  - `linux_root: bool = False`: runs as root in the build container (overpressure/linux.py)
  - `writes: bool = False`: writes to the headset: the window asks first
  - `command(self, values: dict[str, str]) -> list[str]`: Return the command line with @p values in place of the parameters.
- `INSTALL = "install"`: The installer's workflow every headset with an installer has; its state stays where it always was.
- class `Workflow` (@dataclass(frozen=True)): Something the installer does with a headset, chosen right after the headset: install, upgrade, restore.
  - `key: str`: the plan's name: install --workflow KEY
  - `title: str`: the window's card and the stepper's caption
  - `summary: str`: what it does and what it keeps or erases, in one or two sentences
  - `erases: bool = False`: it erases the headset's data
  - `changes: bool = True`: It changes the headset (interface 1.3). One that only looks, such as a check, starts afresh every time and leaves the other workflows' progress, and the record of what was installed, as they are.
- class `Action` (@dataclass(frozen=True)): A choice on the installer's finished page: another workflow, or Advanced mode at one of the headset's steps.
  - `label: str`
  - `step: str = ""`: the key of the step Advanced mode opens at
  - `workflow: str = ""`: the workflow it starts instead (its start page)
- class `BackupSet` (@dataclass(frozen=True)): A headset's backup on this computer: what an export carries and where an import puts it.
  - `folder: Path`
  - `identity: Path`
  - `files: dict[str, Path] = field(default_factory=dict)`
  - `settings: dict[str, str] = field(default_factory=dict)`
- class `AfterInstall` (@dataclass(frozen=True)): What the installer's finished page says and offers: the generic window knows no headset's OS or store.
  - `title: str = "Installed"`
  - `text: str = "Your headset is ready."`
  - `actions: tuple[Action, ...] = ()`
- `tools_step() -> Step`: Return the window's step for overpressure's tool fetcher.
- `ram_boot_step(image: str, help_text: str = "") -> Step`: Return the window's step for overpressure's RAM boot, with the headset's default image.
- class `Headset(ABC)`: A supported headset.
  - `info: Info`
  - property `root(self) -> Path`: Its folder, devices/<codename>.
  - `commands(self) -> dict[str, Command]`: Its command line: command word -> module of its tooling package.
  - `steps(self) -> Sequence[Step]`: Its workflow for the window, in order: building its images, then working on it.
  - `dependencies(self) -> Sequence[Callable]`: Return its checks for python -m overpressure doctor.
  - `usb_modes(self) -> Sequence[UsbMode]`: Return the ways it appears on USB that the host's tools open (adb, fastboot, EDL).
  - `ram_boot(self) -> RamBootTarget | None`: How it boots an image from RAM (python -m overpressure ram-boot), or None.
  - `install_plan(self) -> Sequence[Stage]`: Return its installer's stages (python -m overpressure install); empty: it has no installer.
  - `workflows(self) -> Sequence[Workflow]`: Return what its installer does, in the window's order: the install alone unless it has more.
  - `plan(self, workflow: str) -> Sequence[Stage]`: Return the stages of WORKFLOW (python -m overpressure install --workflow WORKFLOW).
  - `workflow_problem(self, workflow: str) -> str`: Return why WORKFLOW cannot run now, in plain words, or "" when it can.
  - `after_install(self) -> AfterInstall`: Return what the window's finished page says and offers once its installer has run.
  - `finished_page(self, workflow: str) -> AfterInstall`: Return what the window's finished page says and offers once WORKFLOW has run (the install: after_install).
  - `backup_set(self) -> BackupSet | None`: Return its backup on this computer as .env names it (python -m overpressure backup), or None.
  - `adopt_backup(self, imported: BackupSet) -> None`: Make an imported backup this computer's: the .env settings that name it and its serials.
  - `support_files(self, folder: Path) -> None`: Add the headset's own files for a bug report into FOLDER (since 1.2): its logs, its device's bundle.
  - `support_masks(self) -> dict[str, str]`: Return what else a bug report must not show, each value with what it becomes (since 1.4).
  - `touches(self, argv: list[str]) -> bool`: Tell whether the command line ARGV (argv[0] its command word) touches the headset; help never does.
  - `main(self, argv: list[str]) -> int`: Run a command of its command line (argv[0] is the command word).
- class `Registry` (@dataclass): The headsets in devices/, by codename; refused: those this overpressure cannot run, and why.
  - `headsets: dict[str, Headset] = field(default_factory=dict)`
  - `refused: dict[str, str] = field(default_factory=dict)`
  - `get(self, codename: str) -> Headset`: Return the headset CODENAME; KeyError names the known ones, or why it was refused.
- `manifest(folder: Path) -> dict`: Return a headset folder's headset.json (codename, name, version, api, commit, date), {} if missing or broken.
- `api_version(value: object) -> tuple[int, int] | None`: Return a headset.json api ("1.2"; a bare 1 is 1.0) as (major, minor), None when it is none.
- `runs_on_runtime(folder: Path) -> bool`: Return whether FOLDER's headset is made for an interface on overpressure's runtime (RUNTIME_SINCE).
- `runs() -> str`: Return the interfaces this overpressure runs, in words.
- `api_problem(value: object) -> str`: Return why this overpressure cannot run a headset made for interface VALUE, "" when it can.
- `refusal(codename: str, data: dict) -> str`: Return why a headset folder with headset.json DATA cannot run here, or "" when it can.
- `discover() -> Registry`: Every devices/<codename>/tooling package that exports HEADSET, whose headset.json this interface runs.

## install

`overpressure.api.v1.install`: the installer: stages, tasks, asks and the context a task runs in ([source](../overpressure/install.py)).

- `OUT = REPO / "out" / "install"`: Where the lock lives, and each headset's folder with its state and its tasks' logs.
- `STATE = "state.json"`
- `SCHEMA = 1`
- `UPGRADE = "upgrade"`: The upgrade workflow (python -m overpressure upgrade runs it).
- `SYSTEM_WORKFLOWS = (INSTALL, UPGRADE)`: The workflows whose finished run leaves overpressure's system on the headset (installed_release).
- `COMPLETE = (DONE, SKIPPED)`: The statuses of a task that needs no run.
- `KINDS = ("confirm", "action", "form")`
- `EXIT = {DONE: 0, FAILED: 1, STOPPED: 3}`: The exit statuses: finished, failed, a usage error (or no installer), stopped.
- `EXIT_USAGE = 2`
- `MASK = "***"`: What a secret value becomes wherever text leaves the engine.
- `TERMINATE_GRACE = 10`: Seconds a stopped child that does not write gets to end before it is killed.
- `STOP_SLICE = 0.2`: Seconds a wait through ctx.clock sleeps at most before it looks for a stop request.
- `STOPPED_BETWEEN`: What a stopped run's finished event says (its where), in plain words.
- `STOPPED_NOTHING`
- `STOPPED_AGAIN = "Start the installer again to continue where it stopped."`
- `STOPPED_WROTE`
- `STOPPED_UNWRITTEN = "This step had not written to the headset yet."`
- class `Field` (@dataclass(frozen=True)): A field of a form ask.
  - `name: str`
  - `label: str`
  - `secret: bool = False`: masked when typed, never written anywhere
  - `optional: bool = False`
- class `Ask` (@dataclass(frozen=True)): A question to the owner: a consent (confirm), something to do (action) or a form.
  - `id: str`
  - `kind: str`: confirm, action or form
  - `title: str`
  - `text: str`
  - `fields: tuple[Field, ...] = ()`
  - `accept: str = ""`: the accepting button; empty: "Done" for an action, "Continue" otherwise
  - `decline: str = "Cancel"`
  - `event(self) -> dict`: Return the ask as its event.
- `IMPORT_BACKUP = "import-backup"`: What a failure offers beside Retry, by name (the finished event's "offer"): the backup's import.
- class `Failed(RuntimeError)`: A task failed: the message says what, in plain words; where says the headset's state and what to do.
  - `offer = ""`: What the window offers beside Retry (IMPORT_BACKUP), "" for nothing more.
- class `NoBackup(Failed)`: The headset's backup is not on this computer: the message says how to import it, the window offers it.
  - `offer = IMPORT_BACKUP`
- class `Stopped(Exception)`: The run stops: a declined ask, Ctrl-C or a closed window.
- class `StopClock`: The clock tasks get (ctx.clock): a sleep looks for a stop request every STOP_SLICE s and ends in Stopped.
  - `monotonic(self) -> float`: Return the clock's time.
  - `sleep(self, seconds: float, /) -> None`: Wait @p seconds in slices; raise Stopped as soon as a stop is requested.
- class `Task` (@dataclass(frozen=True)): One thing the install does, with the function that does it.
  - `id: str`: unique in the plan: the state's key and the log's name
  - `title: str`
  - `run: Callable[[Context], None]`
  - `estimate: str = ""`: how long it takes, in words ("about 1 min")
  - `writes: bool = False`: writes to the headset: its children are never killed midway
  - `done: Callable[[Context], bool] | None = None`: True: its result is there, no run needed
  - `where: str = ""`: after a failure: the headset's state and what to do next
- class `Stage` (@dataclass(frozen=True)): A group of tasks the owner sees as one step, with one main button in the window.
  - `id: str`
  - `title: str`
  - `summary: str`
  - `tasks: tuple[Task, ...]`
- class `UI(Protocol)`: Where the events go and the answers come from: the window (JSON lines) or a terminal.
  - `start(self, stop: Callable[[], None]) -> None`: Begin a run; call @p stop when the other side wants the run to stop.
  - `emit(self, event: dict) -> None`: Show one event.
  - `answer(self, ask: Ask) -> dict | None`: Return the answer to @p ask ({"accepted": ..., "values": {...}}), None when the run must stop.
- class `JsonUI`: The window's side: one JSON object per line on stdout, answers one per line on stdin.
  - `start(self, stop: Callable[[], None]) -> None`: Read answers on a thread from now on.
  - `emit(self, event: dict) -> None`: Write @p event as one line; a closed stdout stops the run.
  - `answer(self, ask: Ask) -> dict | None`: Wait for the answer to @p ask; None once stdin is closed.
- class `TextUI`: A terminal: stage headings, a line per task status, the child programs' output indented.
  - `MARKS: ClassVar[dict[str, str]]`
  - `start(self, stop: Callable[[], None]) -> None`: Nothing to start: Ctrl-C stops the run.
  - `say(self, text: str) -> None`: Write one line.
  - `emit(self, event: dict) -> None`: Write @p event as text.
  - `finished(self, event: dict) -> None`: Write how the run ended.
  - `answer(self, ask: Ask) -> dict | None`: Ask in the terminal; None at the end of input.
- class `Lock`: One engine at a time on this computer: an OS lock on out/install/lock, released when the process ends.
  - `acquire(self) -> bool`: Take the lock; False when another engine holds it.
  - `release(self) -> None`: Give the lock up.
- `Spawn`: Runs a child program: (argv, stdin bytes or None, own process group, line handler) -> exit status.
- `Capture = Callable[[list[str], float], "tuple[int, str]"]`: Runs a short program: (argv, timeout) -> (exit status, output).
- `workflow_folder(root: Path, codename: str, workflow: str) -> Path`: Return where WORKFLOW keeps its state and logs: the install directly in the headset's folder.
- `installed_release(codename: str, root: Path = OUT) -> str`: Return the headset's tooling (catalogue.identity) its last finished install or upgrade ran with, "" for none.
- class `Context`: What a task gets: events, asks, child programs, the settings and the state.
  - property `stopping(self) -> bool`: Tell whether the run has been asked to stop.
  - `log(self, line: str) -> None`: Add a line to the task's log (the details view and out/install/<codename>/<task>.log).
  - `progress(self, fraction: float | None, text: str = "") -> None`: Show how far the task is: @p fraction from 0 to 1 (None: unknown) and a short text.
  - `output_line(self, text: str, progress: Callable[[str], tuple[float | None, str] | None] | None = None) -> None`: Log a line a program printed; a progress line, or one @p progress reads, shows how far.
  - `note(self, text: str, level: str = "info") -> None`: Tell the owner something in plain words: info, or a warning.
  - `ask(self, ask: Ask) -> dict[str, str]`: Ask the owner and wait; return a form's values. A declined ask stops the run.
  - `choose(self, ask: Ask) -> bool`: Ask a yes-or-no question whose no goes on (@p ask's decline); return True for its accept.
  - `run(self, argv: Sequence[str | os.PathLike[str]], *, stdin: bytes | None = None, progress: Callable[[str], tuple[float | None, str] | None] | None = None) -> str`: Run a child program from the repository root, its output streamed to the log; return its output.
  - `output(self, argv: Sequence[str | os.PathLike[str]], timeout: float = 60, *, check: bool = True) -> str`: Run a short program (a poll, a query) without streaming; its output goes to the log file only.
  - `settings(self, cls: type[S]) -> S`: Return the settings of @p cls from .env as they are now (earlier tasks may have written it).
  - `set_setting(self, key: str, value: str) -> None`: Set KEY=VALUE in .env: what the installer finds out (a serial), never a secret.
  - `remember(self, choice: str) -> None`: Record the owner's CHOICE for this task in the state (never a secret): a later run reads it back.
  - `recall(self, task: str) -> str`: Return the choice the task @p task recorded, in this run or an earlier one; "" without one.
  - `completed(self, task: str) -> bool`: Tell whether the task @p task is done or skipped, in this run or an earlier one.
- `replug(what: str, seconds: float) -> Ask`: Return the ask after a wait for @p what that ran out after @p seconds: plug the headset in again.
- `link_ask(problem: str) -> Ask`: Return the ask for a USB link that is up without the headset's address (usblink.problem's @p problem).
- `replug_until(ctx: Context, what: str, seconds: float, operation: Callable[[], T], *, modes: Sequence[usbaccess.UsbMode] = (), link: Callable[[], str] | None = None) -> T`: Wait for @p what as process.until() does; when @p seconds run out, ask for a replug, then wait anew.
- class `Engine`: Runs a plan: events to a UI, the state file, the lock, the tasks' logs.
  - `plan_event(self) -> dict`: Return the plan as its event.
  - property `state_path(self) -> Path`: Return the state file.
  - `load_state(self) -> None`: Read the state file; a missing or unreadable one is a fresh start.
  - `save_state(self) -> None`: Write the state file whole.
  - `status(self, task: str) -> str`: Return a task's recorded status (pending when it has none).
  - `set_status(self, stage: Stage, task: Task, status: str, *, record: bool = True) -> None`: Show a task's status and record it in the state file.
  - `forget_others(self) -> None`: Forget what the other workflows had done: the headset is where this one left it, not where they did.
  - `forget(self, stage_id: str) -> None`: Forget what was done from stage @p stage_id on (--restart).
  - `redact(self, text: str) -> str`: Return @p text with every secret value masked.
  - `check_argv(self, argv: Sequence[str]) -> None`: Refuse a command line that carries a secret value: secrets go on stdin only.
  - `emit(self, event: dict) -> None`: Hand @p event to the UI.
  - `write_log(self, task: Task, text: str) -> None`: Append @p text to out/install/<codename>/<task>.log.
  - `close_logs(self) -> None`: Close the tasks' log files.
  - `finish(self, status: str, task: Task | None, error: str = "", where: str = "", offer: str = "") -> str`: Emit the finished event (with what the window offers beside Retry, OFFER); return @p status.
  - `request_stop(self) -> None`: Stop at the next wait; a running child that does not write to the headset is ended, one that writes not.
  - `step(self, stage: Stage, task: Task) -> str | None`: Run one task unless it needs no run; return the run's end status when it ends the run.
  - `run(self, restart: str | None = None) -> str`: Run the plan from where it stopped (from stage @p restart on, with it); return how it ended.
  - `show_plan(self) -> None`: Emit the plan and every task's recorded status (--plan): nothing runs.
- `stopped_where(task: Task, *, wrote: bool) -> str`: Return what a run stopped in @p task says: the headset's state and what to do.
- `check_plan(stages: Sequence[Stage]) -> None`: Refuse a plan whose stage or task ids repeat: the state and the logs are keyed by them.
- `main(headset: Headset, argv: list[str], *, stdin: IO[str] | None = None, stdout: IO[str] | None = None, root: Path = OUT) -> int`: Run python -m overpressure install for @p headset.
- `upgrade(headset: Headset, argv: list[str]) -> int`: Run python -m overpressure upgrade: the installer's upgrade.
- `restore(headset: Headset, argv: list[str]) -> int`: Run python -m overpressure restore: the installer's way back to the headset's own system.

## kernelimage

`overpressure.api.v1.kernelimage`: a boot image with another kernel, since 1.7 ([source](../overpressure/kernelimage.py)).

- `DEBUG`: --debug: every driver probe logged (the RAM console then ends with the probe a stalled boot waits for), every task and CPU on a panic, msm's debug output, and a hard lockup a panic.
- `trace_words(function: str) -> tuple[str, ...]`: Return the words that record the function graph below one kernel function and print it on a panic.
- `keyed_words(*groups: Iterable[str]) -> tuple[str, ...]`: Merge command line words; a later word replaces an earlier one with its key.
- `command_line(stock: str, extra: Iterable[str], drop: tuple[str, ...], limit: int) -> str`: Return @p stock's words without those @p extra replaces by key or that start with @p drop, then @p extra.
- `files_archive(files: dict[str, Path]) -> cpio.Tree`: Archive each file at its path (the tree makes the parent directories).
- `appended(ramdisk: bytes, *trees: cpio.Tree) -> bytes`: Append each archive to the ramdisk; the kernel unpacks them in order.
- `with_kernel(base: bootimg.BootImage, kernel: bytes, dtb: bytes, cmdline: str, ramdisk: bytes, *, recovery_dtbo: bool = True) -> bytes`: Return @p base packed with @p kernel, @p dtb, @p cmdline and @p ramdisk, its signature cleared, checked.

## kernelinfo

`overpressure.api.v1.kernelinfo`: a kernel image's version and configuration ([source](../overpressure/kernelinfo.py)).

- `FDT_MAGIC = b"\xd0\x0d\xfe\xed"`
- `FDT_HEADER_SIZE = 40`
- class `KernelInfo` (@dataclass): What a kernel image tells about itself.
  - `compression: str`
  - `image_size: int`
  - `version: str = ""`
  - `config: dict[str, str] = field(default_factory=dict)`
  - `config_text: str = ""`
  - `appended_dtbs: list[bytes] = field(default_factory=list)`
  - `has_skip_initramfs: bool = False`
  - `arm64_magic: bool = False`
  - `opt(self, name: str) -> str | None`: Return the value of CONFIG_<name> ("y", "m", a string) or None if unset/unknown.
  - `enabled(self, name: str) -> bool | None`: Tell whether an option is on: True/False if the config is known, None if no config was embedded.
- `analyse(kernel_blob: bytes) -> KernelInfo`: Return what a (possibly compressed) kernel image tells about itself.
- `extract_ikconfig(image: bytes) -> str`: Return the kernel's embedded config (CONFIG_IKCONFIG), or "" without one.
- `split_dtbs(blob: bytes) -> list[bytes]`: Split concatenated flattened device trees.
- `dtb_summary(dtb: bytes) -> dict`: Return model / compatible / qcom,msm-id / qcom,board-id of a DTB root node.

## kernelpin

`overpressure.api.v1.kernelpin`: a headset's pin of overpressure/linux and its contracts, since 1.7 ([source](../overpressure/kernelpin.py)).

- `Log = Callable[[str], None]`
- `Opener = Callable[..., object]`
- `IN_PACKAGES = "packages:"`: A contract path in overpressure/packages at the pinned commit, not in the headset's repository.
- `BUILD_DIFFERS = ("Image.gz",)`: What a build of the pinned commit gives that the release does not: Valve's configuration signs modules with a key each build generates and builds into Image.gz, so only the other files match the pin.
- class `Release` (@dataclass(frozen=True)): A pinned release: overpressure/linux's (kernel/release.json) or overpressure/packages' (packages.json).
  - `repository: str`
  - `tag: str`
  - `commit: str`
  - `image: str`
  - `files: dict[str, str]`: name -> SHA-256
  - `url(self, name: str) -> str`: Return the download URL of the release's file @p name.
  - `source_url(self, path: str) -> str`: Return the URL of the pinned commit's file @p path (the tree as it is, no history).
- `release(path: Path) -> Release`: Return the pin in @p path: a kernel's release.json, or a packages.json (no image, no files).
- `sha256(path: Path) -> str`: Return @p path's SHA-256 (empty for an empty file, which no pin names).
- `mismatches(folder: Path, pin: Release, *, built: bool = False) -> list[str]`: Return the files in @p folder that are missing or differ from the pin, as messages.
- `current(folder: Path, pin: Release) -> bool`: Tell whether @p folder holds the pinned kernel's files.
- `fetch(folder: Path, pin: Release, *, log: Log = print, opener: Opener | None = None) -> list[str]`: Download the pinned release's files into @p folder, each checked by hash; return their names.
- `build_argv(step: str, work: str) -> list[str]`: Return the command line that runs the headset's kernel build @p step (a script) here, in the build container.
- `build(step: str, work: str, pin: Release, call: Callable[..., int] = subprocess.call) -> int`: Build the pinned commit with @p step in the image the release was built with; return its exit status.
- class `Contract` (@dataclass(frozen=True)): A name or path the headset uses and the kernel must provide.
  - `name: str`
  - `kernel: tuple[tuple[str, str], ...]`
  - `here: tuple[tuple[str, str], ...]`
  - `kernel_value: Callable[[str], str] = field(default=lambda value: value)`
  - `here_value: Callable[[str], str] = field(default=lambda value: value)`
- `numbered(name: str) -> str`: Return a workqueue's name as the kernel formats it: ring0 -> ring%d.
- `values(text: str, pattern: str) -> list[str]`: Return the first group of every match of @p pattern in @p text, split where a match lists several.
- `local_text(path: str, root: Path) -> str`: Return a file under @p root, or every file of a folder, as text.
- `source_file(path: str, pin: Release, *, cache: Path, opener: Opener | None = None) -> str`: Return the pinned commit's file @p path, kept in @p cache per commit.
- `check(contracts: tuple[Contract, ...], read: Callable[[str], str], root: Path, packages: Callable[[str], str]) -> list[str]`: Return the broken contracts, each as one line that names both sides.

## localconfig

`overpressure.api.v1.localconfig`: a headset's settings in .env ([source](../overpressure/localconfig.py)).

- `ROOT = REPO`
- `EXE = ".exe" if sys.platform == "win32" else ""`
- `QUOTE_PAIR = 2`
- class `Settings` (@dataclass): The settings every headset shares; a headset adds its own fields and PREFIX.
  - `PREFIX: ClassVar[str] = "OVERPRESSURE_"`
  - `platform_tools: str = ""`
  - `ssh: str = "ssh"`
  - `ssh_keygen: str = "ssh-keygen"`
  - classmethod `key(cls, name: str) -> str`: Return the .env key of setting NAME: the prefix and the name in capitals.
  - `path(self, name: str) -> Path`: Return a path setting, relative to the repository root unless absolute.
  - `tool(self, name: str) -> str`: Return adb or fastboot to run.
  - `require(self, *names: str) -> None`: Raise unless every setting of NAMES is set, naming the missing keys.
- class `DeviceSettings(Settings)` (@dataclass): The settings of a headset on a stock Android bootloader: its serials, its EDL backup, the EDL tools.
  - `adb_serial: str = ""`
  - `fastboot_serial: str = ""`
  - `edl_serial: str = ""`
  - `identity: str = "backup/device.json"`
  - `backup: str = "backup/stock"`
  - `loader: str = "assets/firehose.elf"`
  - `edl: str = ""`: empty: the fetched edl-ng (python -m overpressure tools)
  - `ncm_python: str = ""`: empty: this Python on Windows, which has pyusb and libusb-package
  - `ssh_public_key: str = ""`: the owner's own public key file, enrolled besides this PC's key
  - `edl_program(self) -> Path`: Return the edl setting when set, else the fetched edl-ng, else edl-ng on PATH.
  - `ncm(self) -> Path | None`: Return the Python for the Windows NCM initialization.
- `parse(text: str) -> dict`: Return the KEY=VALUE lines; blank lines and # comments are skipped, quotes around a value removed.
- `update(values: Mapping[str, str], path: Path | None = None, example: Path | None = None) -> None`: Set KEY=VALUE lines in .env, keeping every other line and comment.
- `load(cls: type[S] = Settings, path: Path | None = None, environ: Mapping[str, str] | None = None) -> S`: Return the settings of class @p cls from .env and the environment.

## ota

`overpressure.api.v1.ota`: a vendor's OTA: boot images and partitions, pinned members ([source](../overpressure/ota.py)).

- `CHUNK = 8 * 1024 * 1024`
- `CONNECTIONS = 8`: Range requests one member downloads with at once: PICO's server gives each connection about 3 MB/s.
- `DOWNLOADS = 0.85`: The share of an extraction's progress its downloads stand for; rebuilding the block images has the rest.
- `BLOCK = 4096`
- `TRANSFER_LIST_VERSIONS = range(1, 5)`: The transfer list versions AOSP's block OTAs write.
- `TRANSFER_LIST_STASH_VERSION = 2`: From this version on, two more header lines count the stash.
- `load_catalog(path: str | os.PathLike[str]) -> dict`: Return a headset's catalog of its vendor's builds: name -> {"url": ..., ...}.
- `stream_member(src: payload.Source, info: zipfile.ZipInfo, out: IO[bytes], log: Callable[[str], None] = print, meter: progress.Meter | None = None) -> None`: Write a zip member's data into @p out, checked against its CRC-32; @p meter counts what is downloaded.
- `zip_infos(src: payload.Source) -> dict[str, zipfile.ZipInfo]`: Return a zip's members by name, from its central directory.
- `read_member(src: payload.Source, name: str, log: Callable[[str], None] = print) -> bytes`: Return a zip member's data, checked against its CRC-32.
- class `PinnedMember` (@dataclass(frozen=True)): One file in a vendor's remote zip, pinned by its size and SHA-256.
  - `url: str`
  - `member: str`
  - `size: int`
  - `sha256: str`
- `read_pinned(item: PinnedMember, log: Callable[[str], None] = print) -> bytes`: Return a pinned member's data, read from its zip over HTTP ranges (CRC-32 checked, not yet the hash).
- `fetch_pinned(item: P, path: Path, log: Callable[[str], None] = print, read: Callable[[P], bytes] | None = None) -> Path`: Put a pinned member at @p path unless it is there already; return the path.
- `fetch_boot_image(location: str, cache_dir: str | None = None, log: Callable[[str], None] = print, partition: str = "boot") -> bytes`: Return the raw stock boot.img, or another partition's image such as recovery's.
- `parse_rangeset(text: str) -> list[tuple[int, int]]`: Return a transfer list's range set ("4,0,10,20,30": two ranges) as (first, end) block pairs.
- `apply_transfer_list(transfer_list: str, new_data: IO[bytes], out: IO[bytes]) -> int`: Rebuild a partition image from a full OTA's transfer list and its decompressed new data.
- `member_ok(path: Path, info: zipfile.ZipInfo) -> bool`: Tell whether PATH holds the member already: its size and CRC-32 match.
- `download_member(src: payload.Source, info: zipfile.ZipInfo, path: Path, log: Callable[[str], None] = print, meter: progress.Meter | None = None) -> None`: Download one zip member into @p path, checked against its CRC-32; keep it if it is there.
- class `BrotliReader(io.RawIOBase)`: A brotli stream, decompressed as it is read: a few MB in memory whatever the image's size.
  - `readable(self) -> bool`: Tell io that this stream can be read.
  - `read(self, size: int = -1) -> bytes`: Return up to @p size decompressed bytes (all that is left for -1); b"" at the end.
- `brotli_module() -> Any | None`: Return the brotli package, None when this Python lacks it (plain python without uv).
- `decompressed(path: Path, package: Callable[[], Any] = brotli_module, which: Callable[[str], str | None] = shutil.which) -> Generator[IO[bytes], None, None]`: Yield @p path's brotli stream decompressed: by the brotli package, else by the brotli command.
- `rebuild_image(download: Path, name: str, out_path: Path, log: Callable[[str], None] = print, *, package: Callable[[], Any] = brotli_module, which: Callable[[str], str | None] = shutil.which) -> None`: Rebuild an ext4 image from NAME.new.dat.br and NAME.transfer.list.
- `sha256(path: Path) -> str`: Return a file's SHA-256, in hex.
- `members_needed(copied: Mapping[str, str], block_images: Mapping[str, str]) -> Iterable[str]`: Yield the OTA members a set of copied files and block images needs.
- `extract(label: str, location: str, output: Path, copied: Mapping[str, str], block_images: Mapping[str, str], *, log: Callable[[str], None] = print) -> dict`: Extract a full block OTA's files into @p output.

## pacman

`overpressure.api.v1.pacman`: pacman's repositories on any host, since 1.7 ([source](../overpressure/pacman.py)).

- class `Download` (@dataclass(frozen=True)): One file: where it lies in the build folder, where it comes from, and how it is checked.
  - `path: str`
  - `url: str`
  - `sha256: str = ""`
  - `size: int = 0`
- class `Database` (@dataclass): A pacman repository's database: its packages by name, what each provides, and its server.
  - `name: str`
  - `server: str`
  - `packages: dict[str, dict[str, list[str]]]`
  - `arch: str = "aarch64"`
  - classmethod `parse(cls, name: str, server: str, data: bytes, arch: str = "aarch64") -> Database`: Read a database: a tar archive (xz, gzip, zstd aside) of NAME-VERSION/desc files.
  - `by_file(self) -> dict[str, dict[str, list[str]]]`: Return the packages by their file name.
  - `url(self, filename: str) -> str`: Return where the server has @p filename.
  - `download(self, fields: dict[str, list[str]], path: str) -> Download`: Return the download of a package of this database into @p path, checked by the database's SHA-256.
- `bare(dependency: str) -> str`: Return a dependency's name without its version: "glibc>=2.40" -> "glibc".
- class `Packages`: The packages of @p databases by name or by what they provide, the first database's where several have one.
  - `find(self, wanted: str) -> tuple[str, tuple[Database, dict[str, list[str]]] | None]`: Return the package that satisfies @p wanted (its name, or a provider's) and where it is.
- `closure(names: list[str], databases: list[Database]) -> list[tuple[Database, dict[str, list[str]]]]`: Return @p names and everything they depend on, from @p databases (the first that has one).
- `repositories(pacman_conf: str) -> list[tuple[str, str]]`: Return pacman.conf's repositories that a server outside this build serves: (name, server).
- `lock(text: str) -> list[tuple[str, str]]`: Return packages.lock's entries (and SHA256SUMS'): (SHA-256, file name).
- `latest(database: Database, name: str, version_prefix: str) -> dict[str, list[str]]`: Return @p database's @p name if its version starts with @p version_prefix (protobuf 25), else fail.

## payload

`overpressure.api.v1.payload`: Android A/B OTA payloads ([source](../overpressure/payload.py)).

- `PAYLOAD_MAGIC = b"CrAU"`
- `OP_NAMES`
- `USER_AGENT = "overpressure-build/1.0 (+payload range reader)"`
- `HTTP_PARTIAL_CONTENT = 206`
- `HTTP_SERVER_ERROR = 500`
- `ATTEMPTS = 5`
- class `Source`: Random access to the bytes of a file, local or remote.
  - `size: int`
  - `parallel = False`: Whether read_at may run in several threads at once (each HTTP request is its own connection).
  - `read_at(self, offset: int, length: int) -> bytes`: Return LENGTH bytes from OFFSET (fewer at the end).
- class `FileSource(Source)`: A local file.
  - `read_at(self, offset: int, length: int) -> bytes`: Return LENGTH bytes from OFFSET (fewer at the end).
- class `HttpSource(Source)`: Random access to a remote file through HTTP Range requests.
  - `BLOCK = 256 * 1024`
  - `parallel = True`
  - `read_at(self, offset: int, length: int) -> bytes`: Return LENGTH bytes from OFFSET (fewer at the end), small reads through a block cache.
- class `SourceFile(io.RawIOBase)`: Adapter so :mod:`zipfile` can read the central directory of a Source.
  - `readable(self) -> bool`: Return True: a Source is read.
  - `seekable(self) -> bool`: Return True: a Source has random access.
  - `tell(self) -> int`: Return the position.
  - `seek(self, offset: int, whence: int = io.SEEK_SET) -> int`: Move the position as file objects do.
  - `readinto(self, b: WriteableBuffer) -> int`: Read into B from the position; return the number of bytes read.
- class `SliceSource(Source)`: A range of another Source, such as a member stored in a zip.
  - `read_at(self, offset: int, length: int) -> bytes`: Return LENGTH bytes from OFFSET (fewer at the end).
- `open_source(location: str, log: Callable[[str], None] = print) -> Source`: Open a local file, or an http(s) URL through range requests.
- `payload_from(location: str, log: Callable[[str], None] = print) -> Source`: Return a Source for payload.bin inside an OTA zip (or a bare payload).
- `pb_decode(buf: bytes) -> dict[int, list]`: Return a protobuf message's fields by number, each a list of its values in order.
- class `Operation` (@dataclass): One install operation: what to do with a blob of the payload, and where it goes.
  - `type: int`
  - `data_offset: int`
  - `data_length: int`
  - `dst_extents: list[tuple[int, int]]`
  - `data_sha256: bytes | None`
- class `Partition` (@dataclass): A partition's new size and hash, and the operations that write it.
  - `name: str`
  - `size: int`
  - `sha256: bytes | None`
  - `operations: list[Operation] = field(default_factory=list)`
- class `Payload` (@dataclass): A payload's manifest: where its data begins, and its partitions.
  - `src: Source`
  - `block_size: int`
  - `data_start: int`
  - `partitions: dict[str, Partition]`
- `PAYLOAD_VERSION = 2`
- `read_payload(src: Source) -> Payload`: Read a payload's header and manifest.
- `extract_partition(payload: Payload, name: str, log: Callable[[str], None] = print) -> bytes`: Return partition NAME's image, written by its operations and checked against its hash.

## power

`overpressure.api.v1.power`: this computer's power, and the headset's battery before writes, 1.6 ([source](../overpressure/power.py)).

- `MIN_BATTERY = 30`: The least battery charge a computer off mains may have for a run that touches the headset.
- `SYSFS = Path("/sys/class/power_supply")`
- `UNKNOWN = 255`
- `NO_BATTERY = 128`
- class `Power` (@dataclass(frozen=True)): What a computer runs on: a battery or not, mains or not (None: unknown), the battery's charge.
  - `battery: bool = False`
  - `mains: bool | None = None`
  - `percent: int | None = None`
- `from_windows(ac_line: int, flag: int, percent: int) -> Power`: Return the power from GetSystemPowerStatus's ACLineStatus, BatteryFlag and BatteryLifePercent.
- `from_sysfs(root: Path = SYSFS) -> Power`: Return the power from Linux's power supplies: a system battery (not a device's) and mains or USB online.
- `from_pmset(output: str) -> Power`: Return the power from macOS's pmset -g batt.
- `read() -> Power`: Return this computer's power; unknown parts stay None.
- `problem(found: Power, minimum: int = MIN_BATTERY) -> str`: Return why FOUND is too little for a write to the headset, or "" when it is enough.
- `battery_problem() -> str`: Return why this computer's power is too little for a write to the headset, or "".
- `MIN_HEADSET_WRITE = 20`: The least charge a headset may have for partition writes when it does not charge (since interface 1.6).
- `headset_refusal(reading: str, minimum: int = MIN_HEADSET_WRITE) -> str`: Return why partition writes may not start on a headset's battery @p reading, or "" if they may.

## prefetch

`overpressure.api.v1.prefetch`: a headset's downloads before an install, since 1.7 ([source](../overpressure/prefetch.py)).

- `MANIFEST = "manifest.txt"`: The pin's files in a cache, one path per line: what an install sends.
- `PACKAGE_CACHE = "root/downloads/packages"`: The upstream packages, beside build-userspace.sh's environment, which a new environment's unpacking would empty: build-userspace.sh links them into its package cache (its --cachedir).
- `DATABASES = "root/downloads/databases"`: The image's package databases, for build-userspace.sh --databases.
- `HOST = "downloads/host"`: The build host's databases, packages and repository, for build-on-headset.sh.
- `CHUNK = 1 << 20`
- `PARALLEL = 4`: Downloads at once, as pacman.conf's ParallelDownloads.
- `RETRY_SECONDS = 120`: A body that breaks off is tried again for as long as overpressure's downloads try (web.RETRY).
- `Log = Callable[[str], None]`
- `Opener = Callable[..., IO[bytes]]`
- `local(path: str) -> str`: Return the name this computer keeps @p path under: a ":" (a package's epoch) is no file name's on Windows.
- `shell_value(script: str, name: str) -> str`: Return the value packages.sh gives NAME (NAME=value), with $HOLO_SNAPSHOT filled in.
- class `Prefetch`: A headset's downloads for its pins into @p cache, through @p opener (web's, the tests' fake).
  - `small(self, path: str, url: str) -> bytes`: Return the file @p path, downloaded from @p url unless it is there.
  - `archive_file(self, archive: bytes, path: str) -> str`: Return the file @p path of GitHub's archive of the pinned commit.
  - `plan(self) -> list[Download]`: Return every file the headset's build would download for the pin, the small ones fetched already.
  - `write(self, path: str, data: bytes) -> None`: Write @p data to @p path unless it holds that already.
  - `whole(self, item: Download) -> bool`: Tell whether @p item is there with its SHA-256.
  - `fetch(self, item: Download, meter: progress.Meter) -> None`: Download @p item through a .part file, checked; a body that breaks off is tried again.
  - `sized(self, items: list[Download]) -> list[Download]`: Return @p items, each of unknown size with its Content-Length (a HEAD request), for the progress.
  - `run(self) -> list[str]`: Download what is missing; return the pin's files (the manifest), the cache rid of others.
  - `prune(self, keep: set[str]) -> None`: Remove the files of earlier pins, and parts of downloads that stopped.
- `manifest(cache: Path) -> list[str]`: Return the files the last prefetch left for the pin, or none: what an install sends.

## process

`overpressure.api.v1.process`: bounded waits and running tools ([source](../overpressure/process.py)).

- class `Final(Exception)`: An error that waiting does not cure: until() raises it at once instead of retrying.
- class `CommandFailed(RuntimeError)`: A program that exited nonzero: the message ends with its output's last part, @p output holds all of it.
- `run(argv: Sequence[str | os.PathLike[str]], timeout: float = 60, stdin: int | IO[bytes] | None = None, *, check: bool = True, log: str | os.PathLike[str] | None = None) -> str`: Run a program from the repository root; its output goes to LOG too.
- `run_streamed(argv: Sequence[str | os.PathLike[str]], timeout: float, on_line: Callable[[str], None], *, check: bool = True, stdin: IO[bytes] | None = None) -> str`: Run a program as run() does, handing each line to @p on_line as it comes; return its output.
- class `Timing(Protocol)`: What a wait needs: the time module, a Clock or a test's fake.
  - `monotonic(self) -> float`: Return a clock that only moves forward, in seconds.
  - `sleep(self, seconds: float, /) -> None`: Wait SECONDS.
- class `Clock` (@dataclass): The time a wait uses; tests pass a fake one.
  - `monotonic: Callable[[], float] = time.monotonic`
  - `sleep: Callable[[float], None] = time.sleep`
- `until(what: str, seconds: float, operation: Callable[[], T], clock: Timing, interval: float = 3) -> T`: Return @p operation's result once it returns.

## progress

`overpressure.api.v1.progress`: progress lines ([source](../overpressure/progress.py)).

- `PREFIX = "progress"`
- `CHUNK = 1 << 20`
- `FEW = {1: 10, 60: 5, 3600: 3}`: What "a few" stands for, by unit: a few seconds, minutes or hours.
- `line(text: str, fraction: float | None = None) -> str`: Return the progress line for @p text at @p fraction (0 to 1; None: how far is unknown).
- `say(text: str, fraction: float | None = None) -> None`: Print the progress line for @p text at @p fraction, at once (the installer reads it as it comes).
- `parse(text: str) -> tuple[float | None, str] | None`: Return (fraction, text) of a progress line; None for any other line.
- `estimate_seconds(words: str) -> float | None`: Return the seconds an estimate in words stands for, its upper end ("1 to 3 h": 3 h); None without one.
- `clock(seconds: float) -> str`: Return @p seconds as a clock reading: 0:05, 12:34, 1:02:03.
- `duration(seconds: float) -> str`: Return @p seconds in rounded words: under a minute, 4 min, 1 h 20 min.
- class `Meter`: Count bytes and print a progress line for each percent they advance (and the last).
  - property `fraction(self) -> float`: Return how far the task is, from start to end.
  - `add(self, count: int) -> None`: Count @p count more bytes; print a line when a new percent of the task is reached.
- `mib(done: int, total: int) -> str`: Return a byte count of a total in MiB: "1200 of 2900 MiB".
- `feed(source: IO[bytes], meter: Meter) -> Generator[int, None, None]`: Yield the read end of a pipe that carries @p source, for a child's stdin, counting on @p meter.

## qualcomm

`overpressure.api.v1.qualcomm`: Qualcomm: SoC ID, EDL, partition maps, verified writes ([source](../overpressure/qualcomm.py)).

- `EDL_USB = ("05c6", "9008")`
- `EDL_MODE = UsbMode("EDL mode", *EDL_USB)`: EDL as a USB mode: the device edl-ng opens, which Linux grants through a udev rule.
- `U32_MAX = 0xFFFFFFFF`
- `chip_id(value: str) -> int`: Return the SoC ID (/sys/devices/soc0/serial_number), decimal or 0x-hexadecimal, as a 32-bit number.
- `usb_count(vendor: int, product: int) -> int | None`: Return how many USB devices have @p vendor and @p product, None when pyusb or libusb is missing.
- `ioreg_count(output: str, vendor: int, product: int) -> int`: Count the devices in macOS's `ioreg -p IOUSB -l` output with @p vendor and @p product.
- `sysfs_count(root: Path, vendor: int, product: int) -> int`: Count the devices under Linux's /sys/bus/usb/devices with @p vendor and @p product.
- `edl_devices(*, platform: str = sys.platform, usb: Callable[[int, int], int | None] = usb_count, command: Callable[[list[str], float], str] = run, sysfs: Path = Path("/sys/bus/usb/devices")) -> int`: Return the number of attached Qualcomm EDL (9008) devices.
- `edl_command(args: argparse.Namespace, *words: str, memory: str = "ufs") -> list[str]`: Return an EDL command line for edl-ng or bkerler/edl, with the device's storage type.
- `NOT_KEPT = ("cache", "rawdump", "logdump")`: Partitions a backup leaves out besides userdata: Android's cache and the crash-dump areas. No install or restore writes them back, and they hold nothing of the unit's own (2.7 GiB of a PICO 4's 12.5, 0.1 of it data).
- `skipped(*, include_userdata: bool) -> list[str]`: Return the partitions a backup leaves out: NOT_KEPT, and userdata unless it is asked for.
- `backup_commands(args: argparse.Namespace, done: dict[int, list[str]] | None = None) -> list[list[str]]`: Return the EDL command lines of a full backup (ARGS as edl_command's, with the backup's).
- `UNFINISHED = "unfinished.json"`: In a backup folder while its capture is unfinished: whose backup it is (the identity), so that a later try goes on with it. The manifest takes its place once the backup is complete.
- `unfinished(folder: Path) -> dict | None`: Return the identity whose unfinished backup @p folder holds, None for anything else (since 1.5).
- `dumped(folder: Path, lun: int) -> list[str]`: Return the partitions of @p lun a stopped edl-ng backup in @p folder read whole (since 1.5).
- `validate_native_dump(folder: Path, luns: list[int], *, include_userdata: bool) -> None`: Reject missing or truncated files against each native client's rawprogram map.
- `REQUIRED_PARTITIONS = ("abl", "boot", "devinfo")`: Partitions a backup must hold an image of (a slot suffix allowed), or no manifest is written.
- class `DumpProgress`: Turn edl-ng's "Dumping NAME: P% ..." display into progress lines: the partition and each tenth of it.
- `capture_backup(folder: Path, commands: list[list[str]], identity: dict, *, backend: str, luns: list[int] | None, include_userdata: bool, runner: Callable[..., str], required: Sequence[str] = REQUIRED_PARTITIONS, resume: bool = False) -> dict`: Take a full EDL backup into the new folder @p folder and write its manifest; return the manifest.
- `write_verified(write: list[str], read: Callable[[str], list[str]], image: Path, *, readback: Path, backup: Path, runner: Callable[..., str]) -> None`: Write @p image to a partition through EDL, read it back and compare the bytes.
- `enter_edl(adb: str, serial: str, runner: Callable[..., str], clock: Timing, *, edl_count: Callable[[], int] = edl_devices, seconds: float = 30, modes: Sequence[UsbMode] = (EDL_MODE,), denied: Callable[[], bool] | None = None) -> None`: Bring the headset @p serial from Android to EDL (adb reboot edl), unless it is the one EDL device already.
- `edl_ng(program: str | Path, loader: str | Path, *words: str | Path, memory: str = "Ufs") -> list[str | Path]`: Return an edl-ng command line with the device's Firehose @p loader and storage type.
- `NO_ANSWER`: What that means, in plain words: a device in EDL mode opens the conversation once, right after it appears.
- `LINK_LOST`: What a broken link in the middle of edl-ng's session means: its loader keeps running, but answers no new session.
- `link_lost(output: str) -> bool`: Tell whether edl-ng's @p output says the link broke mid-session (since 1.5): a timeout or a garbled reply.
- `failed_output(exc: BaseException) -> str`: Return the whole output of the command behind @p exc: an `output` on it or on what caused it, else its text.
- `unanswered(output: str) -> bool`: Tell whether edl-ng's @p output says the device in EDL mode did not answer (a stale EDL session).
- `serial_shown(output: str) -> bool`: Tell whether edl-ng's @p output names the device's serial.
- `fresh_edl_argv(program: str | Path, loader: str | Path) -> list[str | Path]`: Return the edl-ng command that restarts the device into a fresh EDL session (Firehose's reset_to_edl).
- `reboot_argv(program: str | Path, loader: str | Path) -> list[str | Path]`: Return the edl-ng command that leaves EDL with a normal restart (Firehose's reset): Android starts.
- `RESTART_SECONDS = 60`: Seconds a loader may take to carry out fresh_edl_argv's restart (the PICO 4's: about 12 s, whatever the delay asked).
- class `UsbReader`: Read what a device's loader still sends on its USB pipe: a computer's reading lets it carry out a reset.
- `drain_until_gone(clock: Timing, *, seconds: float = 60, reader: Callable[[], bool] | None = None, platform: str = sys.platform) -> bool`: After a power command, read the device's pipe until it leaves USB; return whether it left in @p seconds.
- `wait_fresh_edl(edl_count: Callable[[], int], clock: Timing, *, seconds: float = RESTART_SECONDS) -> None`: Wait until the device restarted by fresh_edl_argv has left USB, then is back as the one EDL device.
- `check_edl_serial(output: str, serial: str) -> None`: Raise unless edl-ng's @p output names @p serial: only the configured headset may be written.
- `partition_size(backup: Path, label: str) -> int`: Return a partition's size from the backup's rawprogram maps.

## runner

`overpressure.api.v1.runner`: a step's command line ([source](../overpressure/runner.py)).

- `process(headset: Headset, step: Step, values: dict[str, str], *, python: str = sys.executable) -> list[str]`: Return the argv that runs @p step with @p values; its working directory is the repository.
- `stop(proc: subprocess.Popen, argv: list[str]) -> None`: End a step's program @p proc; a build's container first, which outlives a client that is killed.
- `display(argv: list[str], platform: str = sys.platform) -> str`: Return the command line as one would type it in this host's terminal.
- `run(argv: list[str], output: Callable[[str], None], started: Callable[[subprocess.Popen], None] | None = None) -> int`: Run @p argv from the repository root, handing each line of its output to @p output; return its exit status.

## ssh

`overpressure.api.v1.ssh`: SSH with the project's key ([source](../overpressure/ssh.py)).

- `argv(ssh: str, key: Path, known_hosts: Path, target: str, command: str, *, connect_timeout: int = 5) -> list`: Return the ssh command line that runs @p command on @p target (user@host), batch mode, key only.
- `is_public_key(line: bytes) -> bool`: Tell whether LINE is one OpenSSH public key (not a private one): what authorized_keys takes.
- `authorized_keys(this_pc: bytes, extra: Path | None, setting: str) -> bytes`: Return the authorized_keys a headset's image enrolls: this PC's key, and those of @p extra.
- `PUBLIC_KEY`: The ask for the owner's own public key, which a headset's install enrolls beside this computer's.
- `NO_PUBLIC_KEY`
- `ask_public_key(ctx: Context) -> str`: Ask for the owner's own public key until the answer is one; return its line, empty for none.

## stage1

`overpressure.api.v1.stage1`: an Alpine stage 1 in a stock boot image ([source](../overpressure/stage1.py)).

- `RUNTIME = Path(__file__).resolve().parent / "runtime"`: overpressure's headset runtime: the files a stage 1 takes under its headset's overlay (Spec.runtime).
- `REQUIRED_CONFIG`: Kernel options every stage 1 relies on (checked against the stock kernel's IKCONFIG).
- `BOOT_HEADER_V3 = 3`: The first boot image header version without the SHA-1 id mkbootimg writes.
- `BUSYBOX_EXTRAS_APPLETS`: busybox-extras' applet links, normally created by its post-install script.
- class `Spec` (@dataclass(frozen=True)): What a headset's stage 1 is made of.
  - `overlay: Path`: the headset's files for the root (its initramfs/)
  - `packages: Sequence[str] = ()`: Alpine packages, resolved with their dependencies
  - `files: Mapping[str, Path] = field(default_factory=dict)`: root path -> prebuilt executable
  - `required_config: Mapping[str, str] = field(default_factory=lambda: dict(REQUIRED_CONFIG))`
  - `alpine_branch: str = "v3.24"`
  - `alpine_version: str = "3.24.2"`
  - `alpine_repos: tuple[str, ...] = ("main", "community")`
  - `arch: str = "aarch64"`
  - `runtime: bool | None = None`: overpressure's runtime (RUNTIME) under the overlay: the maintenance system's /init, PID 1, services, USB gadget, block policy, recovery deadline and shared library. The headset's overlay then gives its own files and its values in etc/overpressure/headset (docs/runtime.md). None: as the headset's interface has it (on from 1.8, headset.RUNTIME_SINCE, by the headset.json at or above the overlay); True or False decides.
- `takes_runtime(spec: Spec) -> bool`: Return whether SPEC's stage 1 takes overpressure's runtime: as it says, else by its headset's interface.
- `analyse(label: str, data: bytes, interesting_config: Iterable[str] = ()) -> tuple[dict, bootimg.BootImage, kernelinfo.KernelInfo, list[tuple[str, cpio.Node]]]`: Return (report dict, BootImage, KernelInfo, stock ramdisk cpio entries).
- `print_report(rep: dict, log: Callable[[str], None] = print) -> None`: Log the summary of a report of analyse().
- `check_requirements(kinfo: kernelinfo.KernelInfo, required: Mapping[str, str]) -> list[str]`: Return what the stock kernel lacks of REQUIRED ("A|B" accepts either option).
- `build_rootfs(spec: Spec, cache: str, stock_entries: Iterable[tuple[str, cpio.Node]], build_info: dict, *, minimal: bool = False, log: Callable[[str], None] = print) -> cpio.Tree`: Build the stage-1 root.
- `kernel_cmdline(stock: bootimg.BootImage, cmdline_append: Iterable[str]) -> list[str]`: Return the stock kernel arguments with @p cmdline_append added (each once).
- `assemble(stock: bootimg.BootImage, ramdisk: bytes, cmdline_append: Iterable[str]) -> bytes`: Return the stock boot image with the new ramdisk and the kernel arguments appended.
- `add_overlay(tree: cpio.Tree, root: str | os.PathLike[str]) -> None`: Add a directory's files to the tree.

## storage

`overpressure.api.v1.storage`: the read-only storage policy ([source](../overpressure/storage.py)).

- `BLOCK_FIELDS = 3`: A row of the storage inventory: BLOCK, the device's name and its read-only flag.
- `MOUNT_FIELDS = 6`: A row of /proc/mounts: device, mount point, type, options, dump and pass.
- `MOUNT_POINT = 1`
- `DATA = "/data"`: Where an installed system mounts its data partition.
- `validate_storage(output: str, data: str = "") -> None`: Require every physical block device read-only, except the installed system's data partition.
- `data_device(mounts: str, mount_point: str = DATA) -> str`: Return the block device (sda16) mounted at @p mount_point, or empty.
- `validate_mounts(output: str, *, installed: bool = False, data: str = DATA) -> None`: Refuse a writable device-backed mount: stage 1 runs from RAM, the installed system read-only.

## tools

`overpressure.api.v1.tools`: the pinned host tools ([source](../overpressure/tools.py)).

- `CACHE = REPO / "cache" / "tools"`
- `EXE = ".exe" if sys.platform == "win32" else ""`
- class `Archive` (@dataclass(frozen=True)): A tool's archive for one host, pinned by its SHA-256.
  - `url: str`
  - `sha256: str`
- class `Tool` (@dataclass(frozen=True)): A pinned host tool: its archive per host and the programs in it.
  - `name: str`
  - `version: str`
  - `archives: dict[str, Archive]`: host key (see host_key) -> archive
  - `programs: dict[str, str]`: program name -> path inside the unpacked archive
  - `archive(self, host: str | None = None) -> Archive | None`: Return the archive for HOST (this host by default), None if there is none.
  - property `folder(self) -> Path`: Return where the tool is unpacked.
- `host_key() -> str`: windows-x86_64, linux-aarch64, darwin-arm64, ...: the archive a tool needs here.
- `PLATFORM_TOOLS`
- `EDL_NG`
- `ARCHIVE_TOOLS = {tool.name: tool for tool in (PLATFORM_TOOLS, EDL_NG)}`
- `installed(tool: Tool) -> bool`: Tell whether TOOL is unpacked from this host's pinned archive.
- `program(name: str) -> Path | None`: Return a fetched program (adb, fastboot, edl-ng), or None when it is not fetched.
- `download(url: str, sha256: str, log: Callable[[str], None] = print, opener: Callable = web.open_once) -> bytes`: @p url's content, which must hash to @p sha256 (else ValueError; nothing is kept).
- `fetch(tool: Tool, log: Callable[[str], None] = print, opener: Callable = web.open_once) -> Path`: Download and unpack @p tool for this host unless it is there; return its folder.
- `main(argv: list[str] | None = None) -> int`: Fetch the named tools (this host's by default).

## usbaccess

`overpressure.api.v1.usbaccess`: USB access on Linux, a headset's USB modes ([source](../overpressure/usbaccess.py)).

- `RULE_DIRS`: Where udev reads rules; a file in an earlier folder hides one of the same name in a later one.
- `SEAT_LATE = "73-seat-late.rules"`: uaccess takes effect only when tagged before this file, which turns the tag into the user's access.
- `SYSFS = Path("/sys/bus/usb/devices")`
- `DEV = Path("/dev/bus/usb")`
- `OS_RELEASE = Path("/etc/os-release")`
- `NO_PERMISSIONS = "no permissions"`: What adb and fastboot print for a device they may not open.
- `TRIGGER = "sudo udevadm trigger"`: Applies the rules to devices that are already connected.
- `GRACE = 6.0`: Seconds a device may stay without access after it appears: udev grants it a moment later.
- `PACKAGES`: The Android udev rules by distribution (os-release ID or ID_LIKE): the package and the file it installs.
- `OWN_RULES = "/etc/udev/rules.d/70-overpressure.rules"`: Where the fix's own rule goes when no package covers a mode.
- class `UsbMode` (@dataclass(frozen=True)): One way a headset appears on USB: a name for the owner, its vendor and product IDs (hex).
  - `name: str`
  - `vendor: str`
  - `product: str`
  - `access: bool = True`
  - `storage: bool = False`
  - `label: str = ""`
  - `rule(self) -> str`: Return the udev rule that grants the logged-in user this mode's device.
  - `storage_rule(self) -> str`: Return the udev rule that turns off udev's watch on this mode's drives.
- class `NoAccessError(PermissionError, Final)`: The user may not use a connected device of the headset; the message says what to install.
- `rule_files(dirs: Sequence[Path] = RULE_DIRS) -> list[Path]`: Return the rule files udev reads, in its order: by name, an earlier folder's file hiding a later one's.
- `granted(mode: UsbMode, files: Iterable[Path], groups: Iterable[str] = ()) -> bool`: Tell whether the rules in @p files let this user open @p mode's device.
- `unwatched(mode: UsbMode, files: Iterable[Path]) -> bool`: Tell whether the rules in @p files turn off udev's watch on @p mode's drives.
- `user_groups() -> list[str]`: Return the names of this process's groups; none where there are no POSIX groups (Windows).
- `distribution(path: Path = OS_RELEASE) -> list[str]`: Return this system's distribution and the ones it is like (os-release ID, then ID_LIKE).
- `fix(missing: Sequence[UsbMode], *, release: Path = OS_RELEASE, files: Sequence[Path] = (), watched: Sequence[UsbMode] = ()) -> str`: Return what the owner runs so that @p missing modes are granted and @p watched modes' drives unwatched.
- `missing_modes(modes: Sequence[UsbMode], *, dirs: Sequence[Path] = RULE_DIRS, groups: Iterable[str] | None = None) -> tuple[list[UsbMode], list[Path]]`: Return the modes a tool opens that no installed rule grants this user, and the rule files read.
- `watched_modes(modes: Sequence[UsbMode], files: Iterable[Path]) -> list[UsbMode]`: Return the modes carrying a drive whose udev watch no installed rule turns off.
- `device_files(vendor: str, product: str, sysfs: Path = SYSFS, dev: Path = DEV) -> list[Path]`: Return the device files of the connected USB devices with @p vendor and @p product (Linux).
- `denied(mode: UsbMode, *, sysfs: Path = SYSFS, dev: Path = DEV, access: Callable[[Path], bool] | None = None) -> list[Path]`: Return the connected devices of @p mode this user may not write (always none off Linux).
- `listing_denied(output: str) -> bool`: Tell whether adb's or fastboot's device list shows a device it may not open.
- `unusable(output: str, mode: UsbMode) -> bool`: Tell whether adb's or fastboot's listing (@p output) leaves out a device of @p mode this user may not use.
- `seen(modes: Sequence[UsbMode], *, platform: str = sys.platform, sysfs: Path = SYSFS, dev: Path = DEV) -> str`: Return what this computer's USB bus holds of a headset's @p modes (Linux), for a log; else empty.
- `message(mode: UsbMode, modes: Sequence[UsbMode], *, dirs: Sequence[Path] = RULE_DIRS, release: Path = OS_RELEASE) -> str`: Return why a connected device of @p mode cannot be used, and the fix, in plain words.
- `guard(runner: Callable[..., str], modes: Sequence[UsbMode], tools: dict[str, UsbMode], clock: Timing, grace: float = GRACE) -> Callable[..., str]`: Return @p runner, with adb's and fastboot's "no permissions" turned into NoAccessError.
- class `Denial` (@dataclass): A device without access, said only once it has stayed so for GRACE s (udev grants a moment late).
  - `clock: Timing`
  - `grace: float = GRACE`
  - `since: float | None = None`
  - `see(self, is_denied: bool, text: Callable[[], str]) -> None`: Record whether the device is denied now; raise NoAccessError with @p text once it stayed so.
- `require(mode: UsbMode, modes: Sequence[UsbMode], clock: Timing, *, grace: float = GRACE, denied_now: Callable[[], bool] | None = None) -> None`: Return once a connected device of @p mode may be used; raise NoAccessError if it stays denied for @p grace s.

## usblink

`overpressure.api.v1.usblink`: a headset's USB network link on a Linux computer, since 1.6 ([source](../overpressure/usblink.py)).

- `SYS_NET = Path("/sys/class/net")`
- `Run = Callable[[Sequence[str]], "tuple[int, str]"]`: Runs a program and returns its exit status and output; tests pass a fake.
- class `Gadget` (@dataclass(frozen=True)): A headset's USB network gadget: its IDs and product string, its network, the lease, the fix's profile.
  - `vendor: str`: hex, as sysfs writes it ("1d6b")
  - `product: str`
  - `label: str`: the USB product string, which narrows generic IDs to the headset
  - `network: str`: the headset's network ("172.16.42.0/24")
  - `host_ip: str`: the one lease it hands this computer
  - `profile: str`: NetworkManager's profile the fix adds ("overpressure-CODENAME")
  - `in_network(self, address: str) -> bool`: Tell whether @p address (with its prefix) lies in the headset's network.
- `run_quietly(argv: Sequence[str]) -> tuple[int, str]`: Run a query (ip, nmcli) for at most 10 s; return its exit status and output, (127, "") when it cannot run.
- class `Link` (@dataclass(frozen=True)): The headset's network interface on this computer: its name, MAC, IPv4 addresses, NetworkManager's profile.
  - `name: str`
  - `mac: str`
  - `addresses: tuple[str, ...]`
  - `profile: str`: the profile NetworkManager put on it; empty when none, or without NetworkManager
- `usb_device(interface: Path) -> Path`: Return the USB device folder of the network interface folder @p interface (its device is a USB interface).
- `interfaces(gadget: Gadget, net: Path = SYS_NET, device: Callable[[Path], Path] = usb_device) -> list[str]`: Return the network interfaces of @p gadget, by name (Linux; none elsewhere).
- `addresses(name: str, run: Run) -> tuple[str, ...]`: Return @p name's IPv4 addresses with their prefix (ip -j), empty when there are none or ip cannot tell.
- `profile(name: str, run: Run) -> str`: Return the NetworkManager profile active on @p name, empty when none or without NetworkManager.
- `links(gadget: Gadget, net: Path = SYS_NET, run: Run = run_quietly, device: Callable[[Path], Path] = usb_device) -> list[Link]`: Return @p gadget's network interfaces on this computer, each with its addresses and profile.
- `fix(gadget: Gadget, link: Link, *, nmcli: bool) -> str`: Return the commands that give @p link the headset's lease, for a terminal.
- `problem(gadget: Gadget, found: Sequence[Link] | None = None, *, platform: str = sys.platform, nmcli: bool | None = None) -> str`: Return why the headset's USB link is up without its address, and the fix, in the owner's words; else empty.

## web

`overpressure.api.v1.web`: HTTPS downloads, since 1.5 ([source](../overpressure/web.py)).

- `USER_AGENT = "overpressure"`
- `RETRY_SECONDS = 120`: How long a short outage is tried again before a download fails, and the waits between the tries.
- `FIRST_WAIT = 2`
- `LONGEST_WAIT = 30`
- `TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})`: Answers that a later try may not get: a timeout, too many requests, the server's own trouble.
- `Opener = Callable[..., IO[bytes]]`
- `Request = urllib.request.Request`: The request type, so that a caller needs no urllib of its own.
- class `DownloadError(OSError)`: A request that failed for good: the message names its URL and why; status is the HTTP status, if any.
- `context() -> ssl.SSLContext`: Return the TLS context of every request: the system's verifier through truststore, else Python's.
- `system_verifier() -> bool`: Tell whether TLS is verified by the system's own verifier (truststore), for the dependency check.
- `open_once(request: str | urllib.request.Request, timeout: float = 60) -> http.client.HTTPResponse`: Open @p request (a URL or a Request, http or https) once, TLS verified by context(); urllib's errors.
- `describe(exc: BaseException) -> str`: Return in a few words why a request failed: the HTTP status, TLS, the name, a timeout, the connection.
- `transient(exc: BaseException) -> bool`: Tell whether a later try may succeed: any failure of the connection, or an answer of TRANSIENT_STATUS.
- class `Retry` (@dataclass(frozen=True)): How a request is tried again: for @p seconds, each try again logged; the clock is the tests'.
  - `seconds: float = RETRY_SECONDS`
  - `log: Callable[[str], None] = print`
  - `sleep: Callable[[float], None] = time.sleep`
  - `clock: Callable[[], float] = time.monotonic`
- `RETRY = Retry()`: The default: two minutes, logged to the output.
- `urlopen(request: str | urllib.request.Request, timeout: float = 60, retry: Retry = RETRY, opener: Opener = open_once) -> IO[bytes]`: Open @p request, a transient failure tried again (Retry); a failure for good raises DownloadError.
- `read(request: str | urllib.request.Request, timeout: float = 60, retry: Retry = RETRY, opener: Opener = open_once) -> bytes`: Return the body of @p request, a transient failure (also of the body) tried again; else DownloadError.
- `answers(url: str, timeout: float = 15) -> str`: Return "" when this computer gets an answer from @p url (any HTTP status is one), else why not.

## wifi

`overpressure.api.v1.wifi`: the owner's Wi-Fi for a headset's installed system, since 1.6 ([source](../overpressure/wifi.py)).

- `FILE`: Where the keyfile goes on the installed system, and the command that puts it there from SSH's stdin.
- `COMMAND`
- `SSID_BYTES = 32`
- `PSK_LENGTHS = (8, 63)`
- `PSK_HEX = 64`
- `ASK`
- `problem(ssid: str, password: str) -> str`: Return what is wrong with a Wi-Fi answer, in plain words; empty when it can be used.
- `keyfile(ssid: str, password: str, connection: str) -> bytes`: Return a NetworkManager keyfile for @p ssid: WPA-PSK with @p password, an open network without one.
- `ask(ctx: Context) -> tuple[str, str]`: Ask for the Wi-Fi network until the answer can be used; empty: no Wi-Fi.
- `remember(ctx: Context) -> None`: Ask for the Wi-Fi now, so the owner can leave during the install: the answer in memory, a skip in the state.
- `held(ctx: Context, task: str = "wifi") -> tuple[str, str]`: Return the answer remember() kept; a skip in an earlier run is not asked twice, a lost password is asked again.

## windows_ncm

`overpressure.api.v1.windows_ncm`: Windows' USB network link ([source](../overpressure/windows_ncm.py)).

- `SCRIPT = Path(__file__).resolve()`
- `USB_CLASS_CDC = 2`
- `CDC_SUBCLASS_NCM = 0x0D`
- `command(python: str | os.PathLike[str], serial: str | None = None) -> list`: Return this script's command line for @p python, a Python with pyusb and libusb-package.
- `initialize(devices: list, serial: str, dispose: Callable[..., object]) -> dict`: Initialize the one gadget in DEVICES (PyUSB devices) if its serial is SERIAL; release it after.
- `backend() -> tuple`: Return libusb's backend on UsbDk, the loaded UsbDk helper, and PyUSB's core and util.
- `main(argv: list[str] | None = None) -> int`: Initialize the gadget or check the dependencies; print the result as JSON.
