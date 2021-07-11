# footprint 👣

console script for database transfers and nginx/systemd configuration. Install with:

```bash
python -m pip install -U git+https://github.com/arabidopsis/footprint.git
# or
# git clone https://github.com/arabidopsis/footprint.git
# cd footprint
# python -m pip install [--editable] .
```

copy ssh keys `rsync -a ~/.ssh/ {remote}:.ssh/`
sync directories `ssh {machine1} rsync -a {directory1} {machine2}:{directory2}`

```bash
footprint mysql dump mysql://{user}:{pw}@{src}/{db} /var/www/websites/{repo}/instance/sql
# rsync the entire repo
footprint rsync {src}:/var/www/websites/{repo} {tgt}
footprint mysql load mysql://{user}:{pw}@{tgt}/{db} /var/www/websites/{repo}/instance/sql/{db}.sql.gz
```

## `nginx`, `systemd` and all that

* [Nginx Docs](https://docs.nginx.com/nginx/). [Also](https://nginx.org/en/docs/) and [Proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

Test an nginx config with e.g.:

```bash
website=~/Sites/websites/ppr
footprint config nginx $website example.org | footprint config nginx-app - $website
```

This will run nginx at the terminal listening on port 2048 and run the backend
website.

To install a website:

```bash
footprint config nginx $website example.org -o website.conf
footprint config systemd $website -o website.service
footprint config install --sudo website.conf website.service
```

You can test *this* locally by editing `/etc/hosts` and adding a line
`127.0.0.1 example.org` to the file.

**REMEMBER**: Unix file permissions mean that you should edit `/etc/nginx/nginx.conf`
and change `user www-data;` to `user {you};` Or (recursively) change the owner on
all the repo directories to `www-data`.

See [here](https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
) for a tutorial.

Uninstall with `footprint config uninstall --sudo website.conf website.service`

## `.flaskenv`

If a `.flaskenv` is found in the repo directory then nginx and systemd will
read paramters from that file. The keywords should be *uppercase* version of
the known parameters. Unknown parameters will be ignored.

## TODO

* Generate extra systemd files for background processes. Maybe look in
  {application_dir}/etc/systemd for templates. Name them `{app_name}-{filename}.service`
* Generate nginx location for `^/(robots.txt|crossdomain.xml|favicon.ico|browserconfig.xml|humans.txt`
