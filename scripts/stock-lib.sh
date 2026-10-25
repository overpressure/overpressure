# Shared by the extraction scripts (sourced): the stock firmware directory that
# `python -m pico4 stock` builds from PICO's public OTA (docs/development.md#stock-firmware).
#
#   stock_dir DIR FILE...   checks DIR's manifest.json and the named files' sha256 and sets
#                           $stock to DIR's absolute path
#   dump IMAGE PATH DEST    copies one file or directory out of an ext4 image (debugfs)

stock_dir() {
    stock=$(realpath "${1:?specify the stock firmware directory (python -m pico4 stock)}")
    shift
    [[ -s "$stock/manifest.json" ]] || { echo "$stock has no manifest.json (python -m pico4 stock)" >&2; exit 1; }
    for f in "$@"; do
        [[ -s "$stock/$f" ]] || { echo "missing $stock/$f" >&2; exit 1; }
    done
    # Files must match the manifest before anything is read from them.
    python3 - "$stock" "$@" <<'EOF'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = json.loads((root / "manifest.json").read_text())["files"]
for name in sys.argv[2:]:
    h = hashlib.sha256()
    with open(root / name, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    if h.hexdigest() != files.get(name):
        sys.exit(f"{name}: sha256 does not match the stock manifest")
EOF
}

dump() {
    local kind
    kind=$(debugfs -R "stat $2" "$1" 2>/dev/null | sed -n 's/.*Type: \([a-z]*\).*/\1/p' | head -n 1)
    case "$kind" in
    directory) mkdir -p "$3" && debugfs -R "rdump $2 $3" "$1" >/dev/null 2>&1 ;;
    regular) debugfs -R "dump $2 $3" "$1" >/dev/null 2>&1 ;;
    symlink) echo "$2 in $1 is a link; name its target" >&2; return 1 ;;
    *) echo "missing $2 in $1" >&2; return 1 ;;
    esac
}

# write_manifest OUTPUT SOURCES_JSON: every file's sha256, the firmware and where each part came from.
write_manifest() {
    python3 - "$stock" "$1" "$2" > "$1/manifest.json" <<'EOF'
import hashlib, json, pathlib, sys
stock, out, sources = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), json.loads(sys.argv[3])
firmware = json.loads((stock / "manifest.json").read_text()).get("firmware")
files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(out.rglob("*")) if p.is_file() and p.name != "manifest.json"}
print(json.dumps({"schema": 2, "firmware": firmware, "sources": sources, "files": files}, indent=1))
EOF
    echo ">> $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$1/manifest.json") files in $1"
}
