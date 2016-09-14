# Mezzanine Webfaction

Fabric file and related resources for deploying a [Mezzanine] project to a
Webfaction shared hosting account.

## Installation

1. Copy `fabfile.py`, and `deploy/` to your Mezzanine project folder, replacing
   them if they already exist.
1. Right after all the imports in `fabfile.py` ([line 27]), substitute your
   project name in the call to `real_project_name()`. This corresponds with the
   "inner" project folder, where your `local_settings.py` resides.
1. Configure the contents of the `FABRIC` dictionary in your
   `local_settings.py` as shown in `fabsettings.py`. Lines that are commented
   out are optional. Don't forget to set `ALLOWED_HOSTS` to the value it should
   have in production.

## Pre-requisites

**In your dev machine**
- Mezzanine (4+)
- Django (1.7+)
- Fabric
- A git/mercurial repo with your project files (optional)
- A pip requirements file

**In your Webfaction account**
- pip
- virtualenv
- supervisor
- Git app (optional)
- memcached

*Note: this script can install the server pre-requisites for you.*

## Usage

1. To start, we assume you have a working Mezzanine project with Fabric
   installed in your virtualenv. Your `local_settings.py` should have all your
   Webfaction details filled in the `FABRIC` dictionary. From here on, all
   commands are run from the project root in your local machine.
1. Run `fab install` to install all pre-requisites and prepare your Webfaction
   account for hosting your projects. You only need to run this task once for
   each account. All subsequent projects deployed to the same server can skip
   this step.
1. Run `fab deploy` to create your project in your Webfaction server and upload
   the latest version. Boom! Your site is live. Visit it in your browser.
1. Subsequent deployments can be done with `fab deploy`.
1. If you want to wipe out all traces of the project in your server, you can
   run `fab remove`. The prerequistes will persist.
1. Get a list of all available tasks with `fab --list`.

## FAQ

#### How is this different from the default Mezzanine fabfile?
This fabfile is based on the one provided by Mezzanine, but includes several
tweaks to make it work in a shared hosting environment.

- `sudo` is never used since Webfaction accounts don't have this privilege.
- The Webfaction API is used heavily to fully automate the deployment. This
  includes creating domain and site records, apps, databases and cronjobs.
- The server-wide Nginx installation is used via a static app, instead of
  defining custom Nginx config files.

#### How is this different from the [Webfaction tutorial on installing Django]?
Deploying with Fabric has several advantages over the method provided by
Webfaction:

- Fully automated. No need to login to the control panel at all. This was the
  main reason I created this fabfile, to speed up the transition from
  development to production on my client's projects.
- Uses Gunicorn as the application server instead of Apache.
- Installs everything according to Django best practices, instead of creating a
  Django App in the control panel which is hard to mantain up to date.
- Automates the installation of requirements, running of migrations, and
  database and static files backups.
- Uses supervisor for managing processes, wich is tidier than a cronjob for
  each Apache instance.

#### Why am I being prompted for a bunch of passwords when doing `fab deploy`?
This is largely because Fabric cannot "fake" password input for you. This is a
list of common passwords you will be asked for, and some alternatives to get
rid of them:

- **SSH password**: You might see a prompt like `Password for
  username@12.34.56.78`. This is a prompt for the SSH password for your
  Webfaction server. It is stored in `local_settings.FABRIC["SSH_PASS"]`. You
  can get rid of the prompt by using [key-based authentication].
- **Remote database password**: As the name implies, this is the password for
  the database in your Webfaction server. You can find it in
  `local_settings.FABRIC["DB_PASS"]`. You can get rid of the prompt by
  [creating a .pgpass file] in the server.
- **Local database password**: The same as the previous one, but for your local
  machine. This is stored in `local_settings.DATABASES["default"]["PASSWORD"]`.
  You can also create a `.pgpass` file for your computer to get rid of the
  prompt.

#### How come I'm seeing several gunicorn processes running for each Mezzanine project?
Gunicorn uses a master process and a configurable number of worker processes to
serve a site. The [Gunicorn docs] recommend this number should depend on the
amount of processor cores, however, in my tests with my 16-core Webfaction
server this results in 33 processes, which quickly eats all my RAM. I recommend
you use anything from 2 to 6 workers for your projects. You can tweak this in
the `NUM_WORKERS` setting in the `FABRIC` dictionary of your `local_settings`
and doing a deploy to apply the changes.

#### I received an email from Webfaction saying that my resource usage is over limit. Why?
Your Webfaction hosting account has a limit on the amount of CPU and RAM you
can use. If by some reason your Mezzanine site is over that limit, you will
receive a warning for you to reduce the resource usage. Most of the time, this
means reducing the number of gunicorn worker processes. You can do this in the
`FABRIC` section of `local_settings.py`, by setting `NUM_WORKERS` to something
like 1 or 2 and doing a new deployment.

#### Webfaction killed my processes for resource overuse, now my site is down!
This means your usage was WAY over limit, and Webfaction killed your processes
to immediately reduce your resource consumption. In order to bring your sites
back up, you need to do the following:

- Consider reducing the amount of gunicorn workers, as explained in the
  previous point.
- SSH into your Webfaction account and restart supervisor: `supervisord -c ~/etc/supervisord.conf`.
- Also restart memcached: `memcached -d -m 50 -s ~/memcached.sock -P ~/memcached.pid`.

#### Webfaction experienced an outage / rebooted my server and my site is down!
You can partially mitigate this by periodically starting `supervisord` and
`memcache` via a cronjob. You can also set the cronjob to be executed on
server reboot only:

```bash
# cron jobs
@reboot ~/bin/supervisord -c ~/etc/supervisord.conf
@reboot memcached -d -m 50 -s ~/memcached.sock -P ~/memcached.pid
```

There's an edge case with this approach: Gunicorn's PID file could potentially
prevent `gunicorn` from starting again if it stores a PID it can't kill. This
might be the case after a server reboot, where the PID file would keep the old
PID and `gunicorn` would try to kill it before starting again. You can get
around this by deleting all `gunicorn.pid` files after a reboot.

```bash
# Add to your crontab BEFORE the line that starts supervisord
@reboot find ~/webapps -maxdepth 2 -type f -name gunicorn.pid -delete
```

#### Why are you using a symlink to a static/php app instead of one to a static-only app?
Because by doing so you can specify expiration dates for static assets in
`.htaccess` in your root static directory. This prevents browsers from
requesting all your assets every time. [Rationale], [Question in QA site]. You
can change the static app from `symlink54` to `symlink_static_only` if you
wish.

#### What exactly is the fabfile doing?
I recommend you take a look into the source to wrap your head around each task,
but here is a quick run through them:

1. If you use `fab install` it will install and configure all pre- requesites.
   This includes setting up an account-level pip, virtualenv and supervisor
   installation. A supervisord conf file is created and [memcached is started]
   with an allocation of 50 Mb. If you're using git, a [git application] named
   `git_app` is created in `~/webapps/git_app`. All repos will live in there.
1. A full project setup with `fab deploy` will create a new virtualenv in the
   Webfaction server, create a site, database, a custom app, and a static app
   with the Webfaction API, and install all your project dependencies in the
   venv. It will create a site record in the project DB and a superuser if you
   define `ADMIN_PASS`.
1. Afte the first time, `fab deploy` pushes all your changes to the server,
   collect's static files and restart's the gunicorn process via supervisor.

## Extras

The fabfile comes with a few extra goodies not found in Mezzanine by default:

#### Sync the local database with the remote database
Local database must also be postgres.

```bash
fab pulldb # Download the remote DB and restore it locally
fab pushdb # Upload the local DB and restore it remotely
```

#### Sync the local user-uploaded media with the server
```bash
fab pullmedia # Download the remote media files into the local project
fab pushmedia # Upload the local media files into the remote project
```

#### Setup a cronjob to poll Twitter
Make sure you define `TWITTER_PERIOD` in your deploy settings first.

```bash
fab setup_twitter
```

#### Setup a mailbox to send emails from your server
This allows you to receive tracebacks if something goes wrong (if you add
yourself to the [ADMINS] setting), and make the contact forms actually send
notification emails. Make sure you have defined the three email settings in
your `FABRIC` dictionary. You can fill these settings however you like, Fabric
will create the mailbox via the Webfaction API and hook your site to it. For example:

```python
# in your FABRIC settings...
"EMAIL_USER": "mezzanine",  # Whatever you like
"EMAIL_PASS": "mezzanine",  # Whatever you like
"DEFAULT_EMAIL": "no-reply@username.webfactional.com",  # Use your Webfaction username
```

```bash
fab setup_email
fab deploy
```

## Known issues (please contribute!)

- Tested only with Python 2.7, Django 1.7-1.8, and Mezzanine 4.
- No support for MySQL.
- You can only deploy to one Webfaction server.

[Mezzanine]: http://mezzanine.jupo.org/
[line 27]: https://github.com/jerivas/mezzanine-webf/blob/master/fabfile.py#L27
[Webfaction tutorial on installing Django]: http://docs.webfaction.com/software/django/getting-started.html
[key-based authentication]: https://docs.webfaction.com/user-guide/access.html#using-ssh-keys
[creating a .pgpass file]: http://www.postgresql.org/docs/9.3/static/libpq-pgpass.html
[Rationale]: https://developers.google.com/speed/docs/best-practices/caching?csw=1#LeverageBrowserCaching
[Question in QA site]: http://community.webfaction.com/questions/7668/symlink-to-static-only-and-expires-max
[Gunicorn docs]: http://docs.gunicorn.org/en/latest/design.html#how-many-workers
[memcached is started]: http://docs.webfaction.com/software/memcached.html
[git application]: http://docs.webfaction.com/software/git.html
[ADMINS]: https://docs.djangoproject.com/en/1.8/ref/settings/#std:setting-ADMINS
