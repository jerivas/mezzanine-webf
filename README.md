# Mezzanine Webfaction

Fabric file and related resources for deploying a [Mezzanine](http://mezzanine.jupo.org/) project to a Webfaction shared hosting account.

## Installation

Download `fabfile.py`, `fabsettings.py`, `wsgi.py` and `deploy/` to your Mezzanine project folder, replacing them if they already exist.

## Pre-requisites

### In your dev machine
- Mezzanine
- Django
- Fabric
- A git repo with your project files
- A pip requirements file

### In your Webfaction account
- pip
- virtualenv
- supervisor
- Git app
- memcached

*Note: this script can install the server pre-requisites for you.*

## Usage

#### YOU MUST INSTALL THE PRE-REQUISITES IN YOUR SERVER FIRST!

1. Copy the contents of `fabsettings.py` to `local_settings.py` and tweak to your liking. This is the only file you have to edit, all others will be populated by Fabric. All available settings are explained in `fabsettings.py`. **These settings are different from those provided in `settings.py` by Mezzanine, so make sure you only use the ones provided by `fabsettings.py`.**
1. Run `fab prepare_webfaction` to prepare your account for hosting your projects. You only need to run this task once for each account. All subsequent projects can skip this step.
1. In your dev machine and in your project directory run `fab all` to setup everything for your project in the server. `fab all` simply calls `fab create` and the `fab deploy:first=True`. It basically sets up your project environment and then deploys it for the first time.
1. Subsequent deployments can be done with `fab deploy`. If you use `fab deploy:backup=True`, Fabric will backup your project database and static files before deploying the current version of the project.
1. You can setup up a cronjob for polling Twitter with `fab setup_twitter`. Make sure you define `TWITTER_PERIOD` in your deploy settings first.
1. If you want to wipe out all traces of the project in your server: `fab remove`. Calling `fab remove:venv=True` will also delete the virtualenv associated to the project.
1. Get a list of all available tasks with `fab --list`.

## FAQ

- **How is this different from the default Mezzanine fabfile?**  
This fabfile is based on the one provided by Mezzanine, but includes several tweaks to make it work in a shared hosting environment.  

    - `sudo` is never used since Webfaction accounts don't have this privilege.
    - The Webfaction API is used heavily to fully automate the deployment. This includes creating domain and site records, apps, databases and cronjobs.
    - The server-wide Nginx installation is used via a static app, instead of defining custom Nginx config files. 

- **How is this different from the [Webfaction tutorial on installing Django](http://docs.webfaction.com/software/django/getting-started.html)?**  
Deploying with Fabric has several advantages over the method provided by Webfaction:
    - Fully automated. No need to login to the control panel at all. This was the main reason I created this fabfile, to speed up the transition from development to production of my client's projects.
    - Uses Gunicorn as the application server instead of Apache.
    - Installs everything according to Django best practices, instead of creating a Django App in the control panel which is hard to mantain up to date.
    - Uses South and can also automate database and static files back ups.
    - Uses supervisor for managing processes, wich is tidier than a cronjob for each Apache instance.

- **Why are you using a symlink to a static/php app instead of one to a static-only app?**  
Because by doing so you can specify expiration dates for static assets in `.htaccess` in your root static directory. This prevents browsers from requesting all your assets every time. [Rationale](https://developers.google.com/speed/docs/best-practices/caching?csw=1#LeverageBrowserCaching), [Question in QA site](http://community.webfaction.com/questions/7668/symlink-to-static-only-and-expires-max). You can change the static app from `symlink54` to `symlink_static_only` if you wish.

- **How come I'm seeing three processes running for each Mezzanine project?**  
Gunicorn uses a master process and a configurable number of worker processes to serve a site. The [docs recommend this number should depend on the amount of processor cores](http://docs.gunicorn.org/en/latest/design.html#how-many-workers), however, in my tests with my 16-core Webfaction server this results in 33 processes, which quickly eats all my RAM. I've hardcoded 2 processes in `gunicorn.conf.py` and all seems well. Feel free to modify this number to your needs.

- **What exactly is the fabfile doing?**  
I recommend you take a look into the source to wrap your head around each task, but here is a quick run through them:
    1. If you use `fab prepare_webfaction` it will install and configure all pre-requesites. This includes setting up an account-level pip, virtualenv and supervisor installation. A supervisord conf file is created and [memcached is started](http://docs.webfaction.com/software/memcached.html) with an allocation of 50 Mb. A [git application](http://docs.webfaction.com/software/git.html) named "git" is created in ~/webapps/git. All repos will live in there.
    1. A full project setup with `fab create` will create a new virtualenv, set up a remote git repo and add it to your local git repo as "webfaction", create a site, database, a custom app, and a static app with the Webfaction API, and install all your project dependencies in the venv. It will create a site record in the project db and a superuser if you define `ADMIN_PASS`.
    1. `fab deploy` pushes all your changes to the remote repo, collect's static files and restart's the gunicorn process via supervisorctl.

## Known issues (please contribute!)

- Tested only with Python 2.7, Django 1.5, and Mezzanine 1.4.
- No support for MySQL.
- No support for Mercurial, SVN.
- You can only deploy to one Webfaction server and only to one domain.
