# footprint

console script for database transfers

copy ssh keys `rsync -a ~/.ssh/ {remote}:.ssh/` 
sync directories `ssh {machine1} rsync -a {directory1} {machine2}:{directory2}`
