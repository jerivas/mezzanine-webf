import os
import re
import sys
from functools import wraps
from getpass import getpass, getuser
from contextlib import contextmanager
from posixpath import join

from fabric.api import abort, env, cd, hide, local, prefix, run, task
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, upload_template
from fabric.colors import green

################
# Config setup #
################

conf = {}
if sys.argv[0].split(os.sep)[-1] in ("fab",             # POSIX
                                     "fab-script.py"):  # Windows
    # Ensure we import settings from the current dir
    try:
        conf = __import__("settings", globals(), locals(), [], 0).FABRIC
        try:
            conf["HOSTS"][0]
        except (KeyError, ValueError):
            raise ImportError
    except (ImportError, AttributeError):
        print("Aborting, no hosts defined.")
        exit()

env.user = conf.get("SSH_USER", getuser())
env.password = conf.get("SSH_PASS", None)
env.key_filename = conf.get("SSH_KEY_PATH", None)
env.hosts = conf.get("HOSTS", [])
env.live_subdomain = conf.get("LIVE_SUBDOMAIN", None)
env.live_domain = conf.get("LIVE_DOMAIN", None)
env.live_host = "%s.%s" % (env.live_subdomain, env.live_domain) if (
    env.live_subdomain) else env.live_domain

env.proj_name = conf.get("PROJECT_NAME", os.getcwd().split(os.sep)[-1])
env.proj_path = "/home/%s/webapps/%s" % (env.user, env.proj_name)
env.venv_home = conf.get("VIRTUALENV_HOME", "/home/%s/.virtualenvs" % env.user)
env.venv_name = conf.get("VIRTUALENV_NAME", env.proj_name)
env.venv_path = "%s/%s" % (env.venv_home, env.venv_name)
env.reqs_path = conf.get("REQUIREMENTS_PATH", "requirements/project.txt")
env.manage = "%s/bin/python %s/manage.py" % (env.venv_path, env.proj_path)
env.repo_path = conf.get("REPO_PATH", "/home/%s/webapps/git/repos/%s.git" % (
    env.user, env.proj_name))
env.locale = conf.get("LOCALE", "en_US.UTF-8")
env.supervisor_conf = "/home/%s/etc/supervisor/conf.d/%s.conf" % (
    env.user, env.proj_name)
env.twitter_period = conf.get("TWITTER_PERIOD", None)

env.admin_pass = conf.get("ADMIN_PASS", None)
env.db_pass = conf.get("DB_PASS", None)
env.secret_key = conf.get("SECRET_KEY", "")
env.nevercache_key = conf.get("NEVERCACHE_KEY", "")


##################
# Template setup #
##################

# Each template gets uploaded at deploy time, only if their
# contents has changed, in which case, the reload command is
# also run.

templates = {
    "supervisorctl": {
        "local_path": "deploy/supervisorctl.conf",
        "remote_path": "%(supervisor_conf)s",
        "reload_command": "supervisorctl restart gunicorn_%(proj_name)s",
    },
    "gunicorn": {
        "local_path": "deploy/gunicorn.conf.py",
        "remote_path": "%(proj_path)s/gunicorn.conf.py",
    },
    "settings": {
        "local_path": "deploy/live_settings.py",
        "remote_path": "%(proj_path)s/local_settings.py",
    },
    "post receive hook": {
        "local_path": "deploy/post-receive",
        "remote_path": "%(repo_path)s/hooks/post-receive"
    },
}


###################################
# Wrappers for the Webfaction API #
###################################

def get_webf_session():
    """
    Return an instance of a Webfaction server and a session for authentication
    to make further API calls.
    """
    import xmlrpclib
    server = xmlrpclib.ServerProxy("https://api.webfaction.com/")
    print("Logging in to Webfaction as %s." % env.user)
    if env.password is None:
        env.password = getpass(
            "Enter Webfaction password for user %s: " % env.user)
    session, account = server.login(env.user, env.password)
    print("Succesfully logged in as %s." % env.user)
    return server, session, account


def get_webf_obj(server, session, obj_type, obj_name, subdomain=None):
    """
    Check the existence of an object in the server. Return the object
    if found, False if not. A simple wrapper for the "list_XXX" API methods.
    """
    # Get a list of objects from the API
    obj_list = getattr(server, "list_%ss" % obj_type)(session)
    # Choose a key according to the object type
    key_map = {"domain": "domain", "db_user": "username"}
    key = key_map.get(obj_type, "name")
    # Filter the list by key and get a single object
    try:
        obj = [item for item in obj_list if item[key] == obj_name][0]
    # If the list is empty, there's no match, return False
    except IndexError:
        return False
    else:
        # If we're querying for a subdomain, let's check it's there
        if key == "domain" and subdomain is not None:
            return obj if subdomain in obj["subdomains"] else False
        # Else just return the object we already found
        return obj


def del_webf_obj(server, session, obj_type, obj_name, *args):
    """
    Remove and object from the server. A simple wrapper for the "delete_XXX"
    API methods.
    """
    obj = getattr(server, "delete_%s" % obj_type)(session, obj_name, *args)
    return obj


######################################
# Context for virtualenv and project #
######################################

@contextmanager
def virtualenv():
    """Run commands within the project's virtualenv."""
    with cd(env.venv_path):
        with prefix("source %s/bin/activate" % env.venv_path):
            yield


@contextmanager
def project():
    """Run commands within the project's directory."""
    with virtualenv():
        with cd(env.proj_path):
            yield


@contextmanager
def update_changed_requirements():
    """
    Check for changes in the requirements file across an update,
    and get new requirements if changes have occurred.
    """
    reqs_path = join(env.proj_path, env.reqs_path)
    get_reqs = lambda: run("cat %s" % reqs_path)
    old_reqs = get_reqs() if env.reqs_path else ""
    yield
    if old_reqs:
        new_reqs = get_reqs()
        if old_reqs == new_reqs:
            # Unpinned requirements should always be checked.
            for req in new_reqs.split("\n"):
                if req.startswith("-e"):
                    if "@" not in req:
                        # Editable requirement without pinned commit.
                        break
                elif req.strip() and not req.startswith("#"):
                    if not set(">=<") & set(req):
                        # PyPI requirement without version.
                        break
            else:
                # All requirements are pinned.
                return
        pip("-r %s/%s" % (env.proj_path, env.reqs_path))


###########################################
# Utils and wrappers for various commands #
###########################################

def log_call(func):
    """Log the name of the function it wraps to stdout."""
    @wraps(func)
    def logged(*args, **kawrgs):
        header = "-" * len(func.__name__)
        print(green("\n".join([header, func.__name__, header]), bold=True))
        return func(*args, **kawrgs)
    return logged


def get_templates():
    """Return each of the templates with env vars injected."""
    injected = {}
    for name, data in templates.items():
        injected[name] = dict([(k, v % env) for k, v in data.items()])
    return injected


def upload_template_and_reload(name):
    """
    Uploas a template only if it has changed, and if so, reload a
    related service.
    """
    template = get_templates()[name]
    local_path = template["local_path"]
    if not os.path.exists(local_path):
        project_root = os.path.dirname(os.path.abspath(__file__))
        local_path = os.path.join(project_root, local_path)
    remote_path = template["remote_path"]
    reload_command = template.get("reload_command")
    remote_data = ""
    if exists(remote_path):
        with hide("stdout"):
            remote_data = "cat %s" % remote_path
    with open(local_path, "r") as f:
        local_data = f.read()
        # Escape all non-string-formatting-placeholder occurrences of '%':
        local_data = re.sub(r"%(?!\(\w+\)s)", "%%", local_data)
        if "%(db_pass)s" in local_data:
            env.db_pass = db_pass()
        local_data %= env
    clean = lambda s: s.replace("\n", "").replace("\r", "").strip()
    if clean(remote_data) == clean(local_data):
        return
    upload_template(local_path, remote_path, env, use_sudo=False, backup=False)
    if reload_command:
        reload_command


def db_pass():
    """Prompt for the database password if unknown."""
    if not env.db_pass:
        env.db_pass = getpass("Enter the database password: ")
    return env.db_pass


@task
def pip(packages):
    """Install Python packages within the virtual environment."""
    with virtualenv():
        return run("pip install %s" % packages)


@task
def backup(filename):
    """Back up (dump) the project database to a file."""
    return run("pg_dump -U %s -Fc %s > %s" % (
        env.proj_name, env.proj_name, filename))


@task
def restore(filename):
    """Restore the project database from a backup."""
    return run("pg_restore -U %s -c -d %s %s" % (
        env.proj_name, env.proj_name, filename))


@task
def python(code):
    """
    Run Python code in the project's virtual environment, with Django loaded.
    """
    setup = "import os; os.environ[\'DJANGO_SETTINGS_MODULE\']=\'settings\';"
    full_code = 'python -c "%s%s"' % (setup, code.replace("`", "\\\`"))
    with project():
        return run(full_code)


def static():
    """Return the live STATIC_ROOT directory."""
    return python("from django.conf import settings;"
                  "print(settings.STATIC_ROOT)").split("\n")[-1]


@task
def manage(command):
    """Run a Django management command."""
    return run("%s %s" % (env.manage, command))


######################
# Prepare Webfaction #
######################

@task
@log_call
def prepare_webfaction():
    """
    Install all prerequistes in a Webfaction server. This task should only be
    run once per server. All new projects deployed with this fabfile don't
    need to run it again.
    """
    print("Installing all prerequistes to Webfaction server.")
    srv, ssn, acn = get_webf_session()
    srv.create_app(ssn, "git", "git", False, env.password)
    run("easy_install-2.7 pip")
    run("pip-2.7 install virtualenv supervisor")
    remote_path = "/home/%s/etc" % env.user
    run("mkdir -p %s/supervisor/conf.d" % remote_path)
    remote_path += "/supervisord.conf"
    upload_template("deploy/supervisord.conf", remote_path, env, backup=False)
    run("mkdir -p /home/%s/tmp" % env.user)
    run("supervisord")
    # Memcached will use up to 50 Mb of memory.
    run("memcached -d -m 50 -s $HOME/memcached.sock -P $HOME/memcached.pid")
    print("Successfully set up git, pip, virtualenv, supervisor, and "
          "memcached.")


#########################
# Install and configure #
#########################

@task
@log_call
def setup_venv():
    """Set up a new virtualenv or reuse an existing one."""
    run("mkdir -p %s" % env.venv_home)
    with cd(env.venv_home):
        if exists(env.venv_name):
            if confirm("Virtualenv already exists: %s. Reinstall?"
                       % env.venv_name):
                print("Reinstalling virtualenv from scratch.")
                run("rm -r %s" % env.venv_name)
                run("virtualenv %s --distribute" % env.venv_name)
            else:
                print("Using existing virtualenv: %s." % env.venv_name)
        else:
            if confirm("Virtualenv does not exist: %s. Create?"
                       % env.venv_name):
                print("Creating virtualenv.")
                run("virtualenv %s --distribute" % env.venv_name)
                print("New virtualenv: %s." % env.venv_path)
            else:
                abort("Aborting at user request.")
        # Make sure we don't inherit anything from the system's Python
        run("touch %s/lib/python2.7/sitecustomize.py" % env.venv_name)


@task
@log_call
def setup_webfaction():
    """
    Creates a db, db user, custom app, static app, domains, and site record
    using the Webfaction API.
    """
    srv, ssn, acn = get_webf_session()
    # Database user
    db_user = get_webf_obj(srv, ssn, "db_user", env.proj_name)
    if db_user:
        abort("Database user %s already exists." % db_user["username"])
    db = get_webf_obj(srv, ssn, "db", env.proj_name)
    if db:
        abort("Databse %s already exists." % db["name"])
    print("Creating new Postgres database.")
    if env.db_pass is None:
        env.db_pass = db_pass()
    srv.create_db(ssn, env.proj_name, "postgresql", env.db_pass)
    print("New database and database user: %s." % env.proj_name)
    # Custom app
    app = get_webf_obj(srv, ssn, "app", env.proj_name)
    if app:
        abort("App %s already exists." % app["name"])
    print("Creating new custom app.")
    app = srv.create_app(ssn, env.proj_name, "custom_app_with_port", True, "")
    print("New custom app: %s. Listening to port: %s." % (
        app["name"], app["port"]))
    # Static app
    static_app = get_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
    if static_app:
        abort("Static app %s already exists." % static_app["name"])
    print("Creating new static app.")
    static_app_name = "%s_static" % env.proj_name
    static_dir = "%s/static" % env.proj_path
    srv.create_app(ssn, static_app_name, "symlink54", False, static_dir)
    print("New static app: %s. Serving /static from %s." % (
        static_app_name, static_dir))
    # Domain and subdomain
    dom = get_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
    if dom:
        abort("Domain %s already exists." % env.live_host)
    print("Configuring domains.")
    srv.create_domain(ssn, env.live_domain, env.live_subdomain)
    print("New domain: %s." % env.live_host)
    # Site record
    site = get_webf_obj(srv, ssn, "website", env.proj_name)
    if site:
        abort("Website: %s already exists." % site["name"])
    print("Creating new site record")
    main_app, static_app = [env.proj_name, "/"], [static_app_name, "/static"]
    site = srv.create_website(ssn, env.proj_name, env.host_string, False,
                              [env.live_host], main_app, static_app)
    print("New site record: %s. IP Address: %s. Live hosts: %s. Apps: %s." % (
          site["name"], site["ip"], site["subdomains"], site["site_apps"]))


@task
@log_call
def setup_git():
    """Create a new git repo or reuse and existing one. """
    if not exists(env.repo_path):
        print("Setting up git repo")
        run("mkdir %s" % env.repo_path)
        with cd(env.repo_path):
            run("git init --bare")
    upload_template_and_reload("post receive hook")
    run("chmod +x %s/hooks/post-receive" % env.repo_path)
    print("Git repo ready at %s" % env.repo_path)
    local("git remote add webfaction ssh://%s@%s%s" % (env.user,
        env.host_string, env.repo_path))
    print("Added new remote 'webfaction'. You can now push to it with "
          "git push webfaction.")
    print("Pushing master branch.")
    local("git push webfaction +master:refs/heads/master")
    print("All files pushed to remote server.")


@task
@log_call
def setup_project():
    """Prepare the venv and database for deployment."""
    upload_template_and_reload("settings")
    with project():
        if env.reqs_path:
            pip("-r %s/%s" % (env.proj_path, env.reqs_path))
        pip("gunicorn setproctitle south psycopg2 "
            "django-compressor python-memcached")
        manage("createdb --noinput --nodata")
        python("from django.conf import settings;"
               "from django.contrib.sites.models import Site;"
               "site, _ = Site.objects.get_or_create(id=settings.SITE_ID);"
               "site.domain = '" + env.live_host + "';"
               "site.save();")
        if env.admin_pass:
            pw = env.admin_pass
            user_py = ("from mezzanine.utils.models import get_user_model;"
                       "User = get_user_model();"
                       "u = User(username='admin');"
                       "u.is_staff = u.is_superuser = True;"
                       "u.set_password('%s');"
                       "u.save();" % pw)
            python(user_py)
            shadowed = "*" * len(pw)
            print(user_py.replace("'%s'" % pw, "'%s'" % shadowed))


@task
@log_call
def create():
    """
    Create a new virtual environment for a project.
    Push git repo to remote sever.
    Crete database, db user, and website.
    Set up the project.
    """
    setup_venv()
    setup_webfaction()
    setup_git()
    setup_project()
    return True


@task
@log_call
def remove(venv=False):
    """
    Blow away the current project.
    """
    srv, ssn, acn = get_webf_session()
    website = get_webf_obj(srv, ssn, "website", env.proj_name)
    if website:
        del_webf_obj(srv, ssn, "website", env.proj_name, env.host_string)
        print("Removed website: %s." % website["name"])
    dom = get_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
    if dom:
        del_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
        print("Removed domain: %s." % env.live_host)
    main_app = get_webf_obj(srv, ssn, "app", env.proj_name)
    if main_app:
        del_webf_obj(srv, ssn, "app", main_app["name"])
        print("Removed app: %s." % env.proj_name)
    static_app = get_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
    if static_app:
        del_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
        print("Removed app: %s." % static_app["name"])
    db = get_webf_obj(srv, ssn, "db", env.proj_name)
    if db:
        del_webf_obj(srv, ssn, "db", env.proj_name, "postgresql")
        print("Removed database: %s." % db["name"])
    db_user = get_webf_obj(srv, ssn, "db_user", env.proj_name)
    if db_user:
        del_webf_obj(srv, ssn, "db_user", env.proj_name, "postgresql")
        print("Removed database user: %s." % env.proj_name)
    if isinstance(env.twitter_period, int):
        srv.delete_cronjob(ssn, "*/%s * * * * %s poll_twitter" % (
            env.twitter_period, env.manage))
        print("Removed Twitter cron job for %s." % env.proj_name)
    if venv and exists(env.venv_path):
        run("rm -rf %s" % env.venv_path, quiet=True)
        print("Removed remote virtualenv: %s." % env.venv_name)
    if exists(env.repo_path):
        run("rm -rf %s" % env.repo_path, quiet=True)
        local("git remote rm webfaction", capture=True)
        print("Removed remote git repo: %s." % env.repo_path)
    for template in get_templates().values():
        remote_path = template["remote_path"]
        if exists(remote_path):
            run("rm %s" % remote_path, quiet=True)
            print("Removed remote file: %s." % template["remote_path"])
    run("supervisorctl update")


##############
# Deployment #
##############

@task
@log_call
def restart():
    """
    Restart gunicorn worker processes for the project.
    """
    pid_path = "%s/gunicorn.pid" % env.proj_path
    if exists(pid_path):
        run("kill -HUP `cat %s`" % pid_path)
    else:
        run("supervisorctl restart gunicorn_%s" % env.proj_name)


@task
@log_call
def deploy(first=False, backup=False):
    """
    Deploy latest version of the project.
    Check out the latest version of the project from version
    control, install new requirements, sync and migrate the database,
    collect any new static assets, and restart gunicorn's work
    processes for the project.
    """
    if not exists(env.proj_path):
        abort("Project %s does not exist in host server. "
              "Run fab create before trying to deploy." % env.proj_name)
    srv, ssn, acn = get_webf_session()
    app = get_webf_obj(srv, ssn, "app", env.proj_name)
    env.gunicorn_port = app["port"]
    for name in get_templates():
        upload_template_and_reload(name)
    local("git push webfaction master")
    if backup:
        with project():
            backup("last.db")
            static_dir = static()
            if exists(static_dir):
                run("tar -cf last.tar %s" % static_dir)
    manage("collectstatic -v 0 --noinput")
    static_dir = static() + "/.htaccess"
    upload_template("deploy/htaccess", static_dir, backup=False)
    manage("syncdb --noinput")
    manage("migrate --noinput")
    if first:
        run("supervisorctl update")
    else:
        restart()
    return True


@task
@log_call
def setup_twitter():
    """
    Setup a cron job to poll Twitter periodically.
    """
    if isinstance(env.twitter_period, int):
        srv, ssn, acn = get_webf_session()
        srv.create_cronjob(ssn, "*/%s * * * * %s poll_twitter" % (
            env.twitter_period, env.manage))
        manage("poll_twitter")
        print("New cronjob. Twitter will be polled every %s minutes. "
              "Please make sure you have configured your Twitter credentials "
              "in your site settings." % env.twitter_period)
    else:
        abort("TWITTER_PERIOD not set correctly in deployment settings.")


@task
@log_call
def rollback():
    """
    Reverts project state to the last deploy.
    When a deploy is performed, the current state of the project is
    backed up. This includes the last commit checked out, the database,
    and all static files. Calling rollback will revert all of these to
    their state prior to the last deploy.
    """
    with project():
        with update_changed_requirements():
            update = "git checkout" if env.git else "hg up -C"
            run("%s `cat last.commit`" % update)
        with cd(join(static(), "..")):
            run("tar -xf %s" % join(env.proj_path, "last.tar"))
        restore("last.db")
    restart()


@task
@log_call
def all():
    """
    Installs everything required on a new system and deploy.
    From the base software, up to the deployed project.
    """
    if create():
        deploy(first=True)
