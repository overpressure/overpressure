# Return a recovery reason, or nothing while the POC can keep running.
# Arguments: uptime, connect deadline, test deadline, test mode, reachable, acknowledged.
recovery_reason() {
	if [ "$4" = 1 ] && [ "$1" -ge "$3" ]; then
		echo "hardware test exceeded ${3}s"
	elif [ "$2" -gt 0 ] && [ "$1" -ge "$2" ]; then
		if [ "$4" = 1 ] && [ "$6" != 1 ]; then
			echo "hardware runner did not authenticate within ${2}s"
		elif [ "$4" != 1 ] && [ "$5" != 1 ]; then
			echo "no usable USB connection within ${2}s"
		fi
	fi
}
