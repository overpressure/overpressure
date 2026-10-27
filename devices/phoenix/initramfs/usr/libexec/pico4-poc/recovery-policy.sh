# Return a recovery reason, or nothing while the POC can keep running.
# Arguments: uptime, connect deadline, test deadline, test mode, reachable, acknowledged,
# and the uptime of the host's last contact (0 or empty: none). The test deadline (the
# maximum runtime) counts from Linux start, or from that contact when the host renews it
# (the dev loop does with every command), so it ends abandoned runs, not sessions in use.
recovery_reason() {
	if [ "$4" = 1 ] && [ "$(($1 - ${7:-0}))" -ge "$3" ]; then
		if [ "${7:-0}" -gt 0 ]; then
			echo "maximum runtime: no host contact for ${3}s"
		else
			echo "hardware test exceeded ${3}s"
		fi
	elif [ "$2" -gt 0 ] && [ "$1" -ge "$2" ]; then
		if [ "$4" = 1 ] && [ "$6" != 1 ]; then
			echo "hardware runner did not authenticate within ${2}s"
		elif [ "$4" != 1 ] && [ "$5" != 1 ]; then
			echo "no usable USB connection within ${2}s"
		fi
	fi
}

# Step-4 userspace: a hardware run must reach the new userspace over SSH soon
# after the switch, or the headset returns to fastboot without waiting for
# the whole test deadline.
# Arguments: uptime, uptime at the switch (empty: no switch), limit, test mode, reached.
userspace_reason() {
	if [ "$4" = 1 ] && [ -n "$2" ] && [ "$5" != 1 ] && [ "$(($1 - $2))" -ge "$3" ]; then
		echo "hardware runner did not reach the userspace within ${3}s of the switch"
	fi
}
