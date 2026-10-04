#!/bin/sh
# flood.sh USER SECONDS PREFIX FIRST LAST
#
# From each source address PREFIX.FIRST .. PREFIX.LAST at once, log in as
# USER again and again for SECONDS, with whatever password the askpass
# helper gives (the caller sets a wrong one). One line per attempt goes to
# /root/flood.log; the summary is printed at the end.
user=$1
secs=$2
prefix=$3
first=$4
last=$5
rm -f /root/flood.log
end=$(($(date +%s) + secs))
i=$first
while [ "$i" -le "$last" ]; do
    (
        while [ "$(date +%s)" -lt "$end" ]; do
            ssh -b "$prefix.$i" -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
                -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
                "$user@welland.lab" true 2>&1 | tail -n 1 >> /root/flood.log
        done
    ) &
    i=$((i + 1))
done
wait
sed -E 's/port [0-9]+/port N/g; s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/ADDR/g' /root/flood.log | sort | uniq -c | sort -rn
