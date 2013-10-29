# Mezzanine Webfaction

Fabric file and related resources for deploying a [Mezzanine](http://mezzanine.jupo.org/) project to a Webfaction shared hosting account.

## Installation

Download all the files in this repo (except .gitignore) to your Mezzanine project folder, replacing the default files.

## Pre-requisites

### In your dev machine:
- Mezzanine
- Django
- Fabric
- Git
- A pip requirements file

### In your Webfaction server
- Account-level pip
- Account-level virtualenv
- Account-level supervisor. Copy to ~/.etc/supervisord.conf:

```
[unix_http_server]
file=/home/YOUR_USER/tmp/supervisor.sock

[supervisord]
logfile=/home/YOUR_USER/logs/user/supervisord.log
logfile_maxbytes=50MB
logfile_backups=10
loglevel=info
pidfile=/home/YOUR_USER/etc/supervisord.pid

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///home/YOUR_USER/tmp/supervisor.sock

[include]
files = /home/YOUR_USER/etc/supervisor/conf.d/*.conf
```

- A git app created through then Webfaction panel
- A running memcached process ([tutorial](http://docs.webfaction.com/software/memcached.html))

## Usage

1. Configure your live settings in the `DEPLOYMENT_SETTINGS` section of `local_settings.py`. This is the only file you have to edit, all others will be populated by Fabric. All available settings are explained below.
1. In your dev machine and in your project directory run `fab all` to setup everything for your project in the server. `fab all` simply calls `fab create` and the `fab deploy:first=True`. It basically sets up your project environment first and then deploys it for the first time.
1. Subsequent deployments can be done with `fab deploy`. If you use `fab deploy:backup=True`, Fabric will backup your project database and static files before deploying the current version of the project.
1. If you want to wipe out all traces of the project in your server: `fab remove`.
1. Get a list of all available tasks with `fab --list`.

## Known issues (please contribute!)

- No support for MySQL.
- No support for mercurial.
- Missing task to set up all pre-requisites in the server.
- You can only deploy to one Webfaction server and only to one domain.
