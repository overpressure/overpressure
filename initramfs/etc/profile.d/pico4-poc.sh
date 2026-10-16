# PICO 4 Linux POC shell environment (all login shells: ssh, telnet, serial)

# Someone reached the shell, so the USB link works: never auto-reboot.
touch /run/pico4-poc/no-autoreboot 2>/dev/null

PS1='\[\033[1;32m\]pico4-poc\[\033[0m\]:\w\$ '
alias fastboot-mode='pico4-reboot bootloader'
alias android='pico4-reboot'

if [ -z "$POC_BANNER_SHOWN" ] && [ -t 1 ]; then
	export POC_BANNER_SHOWN=1
	pico4-info
fi
