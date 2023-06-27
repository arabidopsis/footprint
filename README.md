# footprint 👣

I use this to generate config files for my flask apps. Currently systemd and nginx.
It only depends on Flask (which should alread be in the virtualenv) and invoke (which I might
refactor at some stage). e.g.

```bash
footprint config nginx . www.example.com > example.conf
footprint config nginx-install example.conf
```

```bash
# install in ~/.config/systemd/user
footprint config systemd --user . > example.service
footprint config systemd-install --user example.service
```

will install nginx and systemd files that will statically serve you 'static' assets and
run the Flask app with gunicorn.

Mostly I've found that confectioning these files by hand are highly error prone. These
commands will at least get the absolute pathnames correct :)



Install with:

```bash
python -m pip install -U git+https://github.com/arabidopsis/footprint.git
# or
# git clone https://github.com/arabidopsis/footprint.git
# cd footprint
# python -m pip install [--editable] .
```

or add to your `pyproject.toml` file

```toml
footprint = { git = "https://github.com/arabidopsis/footprint.git", branch="main" }
```

Once installed you can upgrade with:

```bash
footprint update
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

Note that these configuration generating functions are
not infallible. Please examine the generated configure files
*carefully*! They are mainly useful for getting the directory
names correct etc. So if you move your repo then you will
have to regenerate and reinstall the files.

* [Nginx Docs](https://docs.nginx.com/nginx/). [Also](https://nginx.org/en/docs/) and [Proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

Test an nginx config with e.g.:

```bash
website=~/Sites/websites/ppr
footprint config nginx $website example.org | footprint config run-nginx-conf - $website
```

This will run nginx at the terminal listening on port 2048 and run the backend
website.

To install a website:

```bash
footprint config nginx $website example.org -o website.conf
footprint config systemd [--user] $website -o website.service
# nginx requires sudo (default) or su
footprint config nginx-install [--su] website.conf
# if you can install into ~/.config/systemd/user
footprint config systemd-install [--user] website.service
```

You can test *this* locally by editing `/etc/hosts` and adding a line:

`127.0.0.1 example.org`

to the file.

**REMEMBER**: Unix file permissions mean that you should edit `/etc/nginx/nginx.conf`
and change `user www-data;` to `user {you};` Or (recursively) change the owner on
all the repo directories to `www-data`.

If you install as "user" (i.e. `footprint config systemd --user ...`) then
**to ensure that the user systemd starts at boot time use**: ``sudo loginctl enable-linger <user>``

See [here](https://nts.strzibny.name/systemd-user-services/):

> But what’s the real reason for having user services?
> To answer that, we have to realize when the enabled service starts and stops.
> If we enable a user service, it starts on user login, and runs as long as there is a
> session open for that user. Once the last session dies, the service stops.

----

See [digitalocean.com here](https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04
) for a tutorial about serving flask from nginx.

Uninstall with `footprint config nginx-uninstall [--su] website.conf` and `footprint config systemd-uninstall [--user] website.service`

### `.flaskenv`

If a `.flaskenv` is found in the repo directory then nginx and systemd will
read paramters from that file. The keywords should be *uppercase* version of
the known parameters. Unknown parameters will be ignored.

### TODO

* Generate extra systemd files for background processes. Maybe look in
  {application_dir}/etc/systemd for templates. Name them `{app_name}-{filename}.service`
