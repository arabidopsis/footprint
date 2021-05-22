# footprint

console script for database transfers. Install with:

```bash
python -m pip install -U git+https://github.com/arabidopsis/footprint.git
# or
# pip install [--editable] .
```

copy ssh keys `rsync -a ~/.ssh/ {remote}:.ssh/`
sync directories `ssh {machine1} rsync -a {directory1} {machine2}:{directory2}`

```bash
footprint mysqldump mysql://{user}:{pw}@{src}/{db} /var/www/websites/{repo}/instance/sql
footprint rsync {src}:/var/www/websites/{repo} {tgt}
footprint mysqlload mysql://{user}:{pw}@{tgt}/{db} /var/www/websites/{repo}/instance/sql/{db}.sql.gz
```
