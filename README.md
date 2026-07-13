# flask-nginx aka "footprint" 👣

I use this to generate config files for my flask apps. Currently systemd and nginx.
It only depends on jinja2 and click which a also dependencies of flask/quart.

Will also work with starlette apps too e.g. fastapi.

It is intended to be installed into the same virtual environment that the flask
app inhabits so it can introspect the app (for static folders mainly).

```bash
export FLASK_APP=your_package.wsgi
footprint config nginx www.example.com > example.conf
footprint config nginx-install example.conf
```

```bash
# install in ~/.config/systemd/user
export FLASK_APP=your_package.wsgi
footprint config systemd --user  > example.service
footprint config systemd-install --user example.service
```

will install nginx and systemd files that will statically serve you 'static' assets and
run the Flask app with gunicorn.

Mostly I've found that confectioning these files by hand are highly error prone. These
commands will at least get the absolute pathnames correct :)

`footprint` will install a Quart or a starlette/fastapi using the `--asgi` flag.

Install with:

```bash
uv add flask-nginx
python -m pip install flask-nginx
```


If `footprint` finds a `pyproject.toml` file in the current directory
if will try to load `[tool.footprint]` values into its global configuration object.

*Unless* you specify a configuration file yourself with `footprint -c confg.toml ....`


## `nginx`, `systemd` and all that

Note that these configuration generating functions are
not infallible. Please examine the generated configure files
*carefully*! They are mainly useful for getting the directory
names correct etc. So if you move your repo then you can
easily regenerate and reinstall the files.

- [Nginx Docs](https://docs.nginx.com/nginx/). [Also](https://nginx.org/en/docs/) and [Proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

Test an nginx config with e.g.:

```bash
cd ~/Sites/websites/ppr
export FLASK_APP=ppr.wsgi
footprint config nginx  example.org | footprint config nginx-run -
```

This will run nginx at the terminal listening on port 5000 and run the backend
website.

To install a website:

```bash
footprint config nginx example.org -o website.conf
footprint config systemd [--user] -o website.service
# nginx requires sudo (default) or su
footprint config nginx-install website.conf
# if you can install into ~/.config/systemd/user
footprint config systemd-install [--user] website.service
```

You can test _this_ locally by editing `/etc/hosts` and adding a line:

`127.0.0.1 example.org`

to the file.

**REMEMBER**: Unix file permissions mean that you should edit `/etc/nginx/nginx.conf`
and change `user www-data;` to `user {you};` Or (recursively) change the owner on
all the repo directories to `www-data`.

If you install as "user" (i.e. `footprint config systemd --user ...`) then
**to ensure that the user systemd starts at boot time use**: `sudo loginctl enable-linger <user>`

See [here](https://nts.strzibny.name/systemd-user-services/):

> But what’s the real reason for having user services?
> To answer that, we have to realize when the enabled service starts and stops.
> If we enable a user service, it starts on user login, and runs as long as there is a
> session open for that user. Once the last session dies, the service stops.

---

See [digitalocean.com here](https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-20-04) for a tutorial about serving flask from nginx.


### `.flaskenv`

If a `.flaskenv` is found in the repo directory then nginx and systemd will
read paramters from that file. The keywords should be _uppercase_ version of
the known parameters. Unknown parameters will be ignored.


# nginx and the `--exclusive` option

Much of the web traffic today is bots and scrapers. Most of the time your
Flask app will be processing 404s. To offload this to nginx you should
add a `404.html` (*not* a template -- a full html page) file to your `/static` directory *and* use the `--exclusive`
option: which checks the routes of the *current* app and gets nginx to generate a 404
if these route prefixes are not found.

The *downside* of this is that you now cannot add any new routes to your app
without regenerating and reinstalling the nginx.conf file.
