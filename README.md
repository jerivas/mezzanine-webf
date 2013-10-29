# Mezzanine Webfaction

Fabric file and related resources for deploying a [Mezzanine](http://mezzanine.jupo.org/) project to a Webfaction shared hosting account.

## Installation

Download all the files in this repo (except .gitignore) to your Mezzanine project folder, replacing the default files.

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

## Usage

#### YOU MUST INSTALL THE PRE-REQUISITES IN YOUR SERVER FIRST!
Run `fab prepare_webfaction` to prepare your account for hosting your projects. You only need to run this task once for each account. All subsequent projects can skip this step.

1. Configure your live settings in the `DEPLOYMENT_SETTINGS` section of `local_settings.py`. This is the only file you have to edit, all others will be populated by Fabric. All available settings are explained below.
1. In your dev machine and in your project directory run `fab all` to setup everything for your project in the server. `fab all` simply calls `fab create` and the `fab deploy:first=True`. It basically sets up your project environment first and then deploys it for the first time.
1. Subsequent deployments can be done with `fab deploy`. If you use `fab deploy:backup=True`, Fabric will backup your project database and static files before deploying the current version of the project.
1. If you want to wipe out all traces of the project in your server: `fab remove`.
1. Get a list of all available tasks with `fab --list`.

## Known issues (please contribute!)

- No support for MySQL.
- No support for mercurial.
- You can only deploy to one Webfaction server and only to one domain.
