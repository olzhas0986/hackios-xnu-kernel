#!/system/bin/sh
# Read-only hardware/firmware inventory. No network profiles or passphrases.
set -eu
umask 077
[ "$(getprop ro.product.device)" = munch ] || { echo 'Expected POCO F4 (munch).' >&2; exit 1; }
name="munch-wifi-info-$(date +%Y%m%d-%H%M%S)"
out="/data/local/tmp/$name"
mkdir -p "$out/firmware" "$out/pci"
uname -a > "$out/kernel.txt"
getprop ro.build.version.incremental > "$out/build.txt"
id > "$out/collector-user.txt"
cat /proc/iomem > "$out/iomem.txt" 2> "$out/iomem.err" || true
if [ -r /sys/firmware/fdt ]; then cat /sys/firmware/fdt > "$out/live.dtb"; fi
dmesg 2> "$out/dmesg.err" | grep -iE 'cnss|qca6390|wlan|wifi|mhi|pcie|regulatory|board.id|bdf' > "$out/wifi-dmesg.txt" || true
for device in /sys/bus/pci/devices/*; do
    [ -d "$device" ] || continue
    slot=$(basename "$device")
    mkdir -p "$out/pci/$slot"
    for entry in vendor device subsystem_vendor subsystem_device class modalias resource; do
        [ ! -r "$device/$entry" ] || cat "$device/$entry" > "$out/pci/$slot/$entry" 2>/dev/null || true
    done
    readlink "$device/driver" > "$out/pci/$slot/driver.txt" 2>/dev/null || true
done
for entry in /sys/kernel/debug/clk/clk_summary /sys/kernel/debug/regulator/regulator_summary; do
    [ ! -r "$entry" ] || cat "$entry" > "$out/$(basename "$entry").txt" 2>/dev/null || true
done
: > "$out/firmware-map.txt"
# Keep source paths to distinguish device board data from similarly named files.
for directory in /vendor/firmware /vendor/firmware_mnt/image /odm/firmware /mnt/vendor/persist/wlan; do
    [ -d "$directory" ] || continue
    find -L "$directory" -maxdepth 6 -type f \( -name 'amss*.bin' -o -name 'm3.bin' \
        -o -name 'bd*.elf' -o -name 'bdwlan*' -o -name 'board*.bin' \
        -o -name 'regdb*.bin' -o -name 'WCNSS_qcom_cfg.ini' \) 2>/dev/null |
    while IFS= read -r source; do
        [ -r "$source" ] || continue
        bytes=$(wc -c < "$source")
        [ "$bytes" -le 33554432 ] || continue
        destination="$out/firmware$source"
        mkdir -p "$(dirname "$destination")"
        cp "$source" "$destination"
        printf '%s\t%s\n' "$source" "$bytes" >> "$out/firmware-map.txt"
    done
done
(cd "$out" && find firmware -type f -exec sha256sum '{}' \;) > "$out/firmware-sha256.txt"
archive="/sdcard/Download/$name.tar.gz"
tar -czf "$archive" -C /data/local/tmp "$name"
echo "Created: $archive"
echo 'Only hardware information and vendor firmware collected. No network credentials or device controls accessed.'
