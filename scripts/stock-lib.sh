# SPDX-License-Identifier: Unlicense
# Shared by the headsets' Linux build scripts (sourced): files out of a stock ext4 image, such as
# the vendor.img of a stock folder (`python -m overpressure stock`), read by overpressure's own
# ext4 reader (overpressure/ext4.py), which needs only python3; the assets themselves are
# extracted in Python too (overpressure/assets.py).
#
#   dump IMAGE PATH DEST    copies the regular file PATH out of an ext4 image to DEST

stock_lib_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

dump() {
    PYTHONPATH="$stock_lib_repo${PYTHONPATH:+:$PYTHONPATH}" python3 -m overpressure.ext4 cat "$1" "$2" > "$3.part" ||
        { rm -f "$3.part"; return 1; }
    mv "$3.part" "$3"
}
