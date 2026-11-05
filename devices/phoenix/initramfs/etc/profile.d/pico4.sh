# SPDX-License-Identifier: Unlicense
# PICO 4 Linux shell environment (all login shells: ssh, telnet, serial)

# Someone reached the shell, so the USB link works: never auto-reboot.
touch /run/pico4/no-autoreboot 2>/dev/null

PS1='\[\033[1;32m\]pico4\[\033[0m\]:\w\$ '
alias fastboot-mode='pico4-reboot bootloader'
alias android='pico4-reboot'

if [ -z "$P4_BANNER_SHOWN" ] && [ -t 1 ]; then
	export P4_BANNER_SHOWN=1
	pico4-info
fi
