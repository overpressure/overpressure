# Shared helpers for the PICO 4 Linux POC scripts (sourced, busybox ash).

POC_RUN=/run/pico4-poc
POC_LOG=$POC_RUN/boot.log
POC_LIB=/usr/libexec/pico4-poc

# Defaults; every value can be overridden on the kernel command line as
# pico4poc.<name>=<value> (see docs/stage1.md).
POC_IP=172.16.42.1
POC_HOST_IP=172.16.42.2
POC_PREFIX=24

uptime_s() {
	read -r up _ < /proc/uptime 2>/dev/null
	echo "${up:-?}"
}

# log MESSAGE: kernel log (-> pstore console-ramoops), boot.log and pmsg.
# pmsg-ramoops survives a warm reboot, so after rebooting into PICO OS the
# last messages can be read from /sys/fs/pstore/pmsg-ramoops-0 (root).
log() {
	echo "pico4-poc: $*" > /dev/kmsg 2>/dev/null
	[ -d "$POC_RUN" ] && echo "[$(uptime_s)] $*" >> "$POC_LOG"
	[ -c /dev/pmsg0 ] && echo "pico4-poc[$(uptime_s)]: $*" > /dev/pmsg0 2>/dev/null
	return 0
}

# poc_arg NAME DEFAULT: value of pico4poc.NAME=... from /proc/cmdline.
poc_arg() {
	local word key="pico4poc.$1="
	for word in $(cat /proc/cmdline 2>/dev/null); do
		case "$word" in
		"$key"*) echo "${word#"$key"}"; return 0 ;;
		"pico4poc.$1") echo 1; return 0 ;;
		esac
	done
	echo "$2"
}

# cmdline_arg NAME: value of NAME=... (e.g. androidboot.serialno) or empty.
cmdline_arg() {
	local word
	for word in $(cat /proc/cmdline 2>/dev/null); do
		case "$word" in "$1="*) echo "${word#"$1="}"; return 0 ;; esac
	done
}

device_serial() {
	local s
	s=$(cmdline_arg androidboot.serialno)
	[ -n "$s" ] || s=$(cat /sys/devices/soc0/serial_number 2>/dev/null)
	[ -n "$s" ] || s=pico4-poc
	echo "$s"
}

# write VALUE FILE: echo into sysfs/configfs, log on failure.
write() {
	if ! echo "$1" > "$2" 2>/dev/null; then
		log "write '$1' > $2 failed"
		return 1
	fi
}

# led STATE: drive the headset's RGB status LED if the kernel exposes it.
#   boot = blue, ready = green, waiting = amber, error = blinking red, off.
# Only LEDs named after a plain colour are touched; flash, torch, IR and
# backlight LEDs are never changed.
led() {
	[ "$(poc_arg led 1)" = 1 ] || return 0
	local r=0 g=0 b=0 blink=0 d name v max
	case "$1" in
	boot) b=1 ;;
	ready) g=1 ;;
	waiting) r=1; g=1 ;;
	error) r=1; blink=1 ;;
	esac
	for d in /sys/class/leds/*; do
		[ -e "$d/brightness" ] || continue
		name=${d##*/}
		case "$name" in
		*flash* | *torch* | *ir* | *wled* | *backlight* | *lcd* | *kpd* | *button* | *vib* | *switch*) continue ;;
		red | *:red | *_red | red_*) v=$r ;;
		green | *:green | *_green | green_*) v=$g ;;
		blue | *:blue | *_blue | blue_*) v=$b ;;
		*) continue ;;
		esac
		max=$(cat "$d/max_brightness" 2>/dev/null)
		[ -n "$max" ] && [ "$max" -gt 0 ] 2>/dev/null || max=255
		echo none > "$d/trigger" 2>/dev/null
		if [ "$v" = 1 ]; then
			echo $(( max > 16 ? max / 4 : max )) > "$d/brightness" 2>/dev/null
			[ "$blink" = 1 ] && echo timer > "$d/trigger" 2>/dev/null
		else
			echo 0 > "$d/brightness" 2>/dev/null
		fi
	done
	return 0
}

# Wait until FILE exists (poll), at most SECONDS (0 = forever).
wait_for() {
	local i=0
	while [ ! -e "$1" ]; do
		[ "${2:-0}" -gt 0 ] && [ "$i" -ge "$2" ] && return 1
		sleep 1
		i=$((i + 1))
	done
}

# ext_label DEVICE: the volume name of an ext2/3/4 file system, from its superblock (this
# image has no e2label); fails without one. Magic 0xEF53 at 1080, the name at 1144 (16 bytes).
ext_label() {
	[ "$(dd if="$1" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')" = 53ef ] || return 1
	dd if="$1" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000'
}
