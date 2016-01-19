from __future__ import print_function, unicode_literals
from future.builtins import open

import os
import re
import sys
import tempfile
from contextlib import contextmanager
from functools import wraps
from getpass import getpass, getuser
from importlib import import_module
from posixpath import join

from mezzanine.utils.conf import real_project_name

from fabric.api import abort, env, cd, get, prefix, run as _run, hide, task, local
from fabric.context_managers import settings as fab_settings
from fabric.contrib.console import confirm
from fabric.contrib.files import exists, upload_template
from fabric.contrib.project import rsync_project
from fabric.colors import yellow, green, blue, red


################
# Config setup #
################

env.proj_app = real_project_name("project_name")

conf = {}
if sys.argv[0].split(os.sep)[-1] in ("fab", "fab-script.py"):
    # Ensure we import settings from the current dir
    try:
        conf = import_module("%s.settings" % env.proj_app).FABRIC
        try:
            conf["HOSTS"][0]
        except (KeyError, ValueError):
            raise ImportError
    except (ImportError, AttributeError):
        print("Aborting, no hosts defined.")
        exit()

env.db_pass = conf.get("DB_PASS", None)
env.admin_pass = conf.get("ADMIN_PASS", None)
env.admin_user = conf.get("ADMIN_USER", "admin")
env.user = conf.get("SSH_USER", getuser())
env.password = conf.get("SSH_PASS", None)
env.key_filename = conf.get("SSH_KEY_PATH", None)
env.hosts = conf.get("HOSTS", [""])
env.live_subdomain = conf.get("LIVE_SUBDOMAIN", None)
env.live_domain = conf.get("LIVE_DOMAIN", None)
env.live_host = "%s.%s" % (env.live_subdomain, env.live_domain) if (
    env.live_subdomain) else env.live_domain

env.proj_name = conf.get("PROJECT_NAME", env.proj_app)
env.venv_home = "/home/%s/.virtualenvs" % env.user
env.venv_path = join(env.venv_home, env.proj_name)
env.proj_path = "/home/%s/webapps/%s" % (env.user, env.proj_name)
env.manage = "%s/bin/python %s/manage.py" % (env.venv_path, env.proj_path)
env.domains = conf.get("DOMAINS", env.live_host)
env.domains_python = ", ".join(["'%s'" % s for s in env.domains])
env.vcs_tools = ["git", "hg"]
env.deploy_tool = conf.get("DEPLOY_TOOL", "rsync")
env.reqs_path = conf.get("REQUIREMENTS_PATH", None)
env.locale = conf.get("LOCALE", "en_US.UTF-8")
env.twitter_period = conf.get("TWITTER_PERIOD", None)
env.num_workers = conf.get("NUM_WORKERS",
                           "multiprocessing.cpu_count() * 2 + 1")

env.secret_key = conf.get("SECRET_KEY", "")
env.nevercache_key = conf.get("NEVERCACHE_KEY", "")

env.email_user = conf.get("EMAIL_USER", None)
env.email_pass = conf.get("EMAIL_PASS", None)
env.default_email = conf.get("DEFAULT_EMAIL", None)
if not (env.email_user and env.email_pass and env.default_email):
    env.use_email = "#"
else:
    env.use_email = ""

# Remote git repos need to be "bare" and reside separated from the project
if env.deploy_tool == "git":
    env.repo_path = "/home/%s/webapps/git_app/repos/%s.git" % (env.user, env.proj_name)
else:
    env.repo_path = env.proj_path


##################
# Template setup #
##################

# Each template gets uploaded at deploy time, only if their
# contents has changed, in which case, the reload command is
# also run.

templates = {
    "supervisor": {
        "local_path": "deploy/supervisor.conf.template",
        "remote_path": "/home/%(user)s/etc/supervisor/conf.d/%(proj_name)s.conf",
        "reload_command": "supervisorctl update gunicorn_%(proj_name)s",
    },
    "gunicorn": {
        "local_path": "deploy/gunicorn.conf.py.template",
        "remote_path": "%(proj_path)s/gunicorn.conf.py",
    },
    "settings": {
        "local_path": "deploy/local_settings.py.template",
        "remote_path": "%(proj_path)s/%(proj_app)s/local_settings.py",
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
    """
    Runs commands within the project's virtualenv.
    """
    with cd(env.venv_path):
        with prefix("source %s/bin/activate" % env.venv_path):
            yield


@contextmanager
def project():
    """
    Runs commands within the project's directory.
    """
    with virtualenv():
        with cd(env.proj_path):
            yield


@contextmanager
def update_changed_requirements():
    """
    Checks for changes in the requirements file across an update,
    and gets new requirements if changes have occurred.
    """
    reqs_path = join(env.proj_path, env.reqs_path)
    get_reqs = lambda: run("cat %s" % reqs_path, show=False)
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

def _print(output):
    print()
    print(output)
    print()


def print_command(command):
    _print(blue("$ ", bold=True) +
           yellow(command, bold=True) +
           red(" ->", bold=True))


@task
def run(command, show=True, *args, **kwargs):
    """
    Runs a shell comand on the remote server.
    """
    if show:
        print_command(command)
    with hide("running"):
        return _run(command, *args, **kwargs)


def log_call(func):
    @wraps(func)
    def logged(*args, **kawrgs):
        header = "-" * len(func.__name__)
        _print(green("\n".join([header, func.__name__, header]), bold=True))
        return func(*args, **kawrgs)
    return logged


def get_templates():
    """
    Returns each of the templates with env vars injected.
    """
    injected = {}
    for name, data in templates.items():
        injected[name] = dict([(k, v % env) for k, v in data.items()])
    return injected


def upload_template_and_reload(name):
    """
    Uploads a template only if it has changed, and if so, reload the
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
            remote_data = run("cat %s" % remote_path, show=False)
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
        run(reload_command)


def cpmedia(upload=True):
    """
    Copy media files between the remote and local environments.
    The upload param determines the direction of the transfer.
    """
    # The empty last part ends the join() with a separator
    local_dir = join(os.getcwd(), "static", "media", "")
    remote_dir = join(static(), "media", "")
    excludes = [".thumbnails"]
    rsync_project(remote_dir=remote_dir, local_dir=local_dir, exclude=excludes,
                  upload=upload)


def rsync_upload():
    """
    Uploads the project with rsync excluding some files and folders.
    """
    excludes = ["*.pyc", "*.pyo", "*.db", ".DS_Store", ".coverage",
                "local_settings.py", "/static", "/.git", "/.hg"]
    local_dir = os.getcwd() + os.sep
    return rsync_project(remote_dir=env.proj_path, local_dir=local_dir,
                         exclude=excludes)


def vcs_upload():
    """
    Uploads the project with the selected VCS tool.
    """
    if env.deploy_tool == "git":
        remote_path = "ssh://%s@%s%s" % (env.user, env.host_string,
                                         env.repo_path)
        if not exists(env.repo_path):
            run("mkdir -p %s" % env.repo_path)
            with cd(env.repo_path):
                run("git init --bare")
        local("git push -f %s master" % remote_path)
        with cd(env.repo_path):
            run("GIT_WORK_TREE=%s git checkout -f master" % env.proj_path)
            run("GIT_WORK_TREE=%s git reset --hard" % env.proj_path)
    elif env.deploy_tool == "hg":
        remote_path = "ssh://%s@%s/%s" % (env.user, env.host_string,
                                          env.repo_path)
        with cd(env.repo_path):
            if not exists("%s/.hg" % env.repo_path):
                run("hg init")
            with fab_settings(warn_only=True):
                push = local(
                    "hg push --config ui.remotecmd=/home/%s/bin/hg -f %s" %
                    (env.user, remote_path))
                if push.return_code == 255:
                    abort("'hg push' failed.")
            run("hg update -C")


def db_pass():
    """
    Prompts for the database password if unknown.
    """
    if not env.db_pass:
        env.db_pass = getpass("Enter the database password: ")
    return env.db_pass


@task
def pip(packages, show=True):
    """
    Install Python packages within the virtual environment.
    """
    # We use our own tmp folder to avoid problems with the system /tmp.
    pip_tmp = "/home/%s/tmp/pip" % env.user
    if not exists(pip_tmp):
        run("mkdir -p %s" % pip_tmp)
    with virtualenv():
        run("pip install -b %s %s" % (pip_tmp, packages), show=show)
        run("rm -rf %s/*" % pip_tmp, show=show)  # Cleanup


@task
def backup(filename):
    """
    Backs up the remote (production) database.
    """
    print(blue("Input the remote database password when prompted", bold=True))
    return run("pg_dump -U %s -Fc %s > %s" % (
        env.proj_name, env.proj_name, filename))


@task
def local_backup(filename):
    """
    Backs up the local (development) database.
    """
    print(blue("Input the local database password when prompted", bold=True))
    return local("pg_dump -U %s -Fc %s -h localhost > %s" % (
        env.proj_name, env.proj_name, filename))


@task
def restore(filename):
    """
    Restores the remote (production) database from a previous backup.
    """
    print(blue("Input the remote database password when prompted", bold=True))
    return run("pg_restore -U %s -c -d %s %s" % (
        env.proj_name, env.proj_name, filename))


@task
def local_restore(filename):
    """
    Restores the local (development) database from a previous backup.
    """
    print(blue("Input the local database password when prompted", bold=True))
    return local("pg_restore -U %s -c -d %s -h localhost %s" % (
        env.proj_name, env.proj_name, filename))


@task
def python(code, show=True):
    """
    Runs Python code in the project's virtual environment, with Django loaded.
    """
    setup = "import os;" \
            "os.environ[\'DJANGO_SETTINGS_MODULE\']=\'%s.settings\';" \
            "import django;" \
            "django.setup();" % env.proj_app
    full_code = 'python -c "%s%s"' % (setup, code.replace("`", "\\\`"))
    with project():
        if show:
            print_command(code)
        result = run(full_code, show=False)
    return result


def static():
    """
    Returns the live STATIC_ROOT directory.
    """
    return python("from django.conf import settings;"
                  "print(settings.STATIC_ROOT)", show=False).split("\n")[-1]


@task
def manage(command):
    """
    Runs a Django management command.
    """
    return run("%s %s" % (env.manage, command))


#########################
# Install and configure #
#########################

@task
@log_call
def install():
    """
    Installs all prerequistes in a Webfaction server.
    This task should only be run once per server. All new projects deployed with this
    fabfile don't need to run it again.
    """
    # Install git
    srv, ssn, acn = get_webf_session()
    srv.create_app(ssn, "git_app", "git_230", False, env.password)

    # Install Python requirements
    run("easy_install-2.7 pip")
    run("pip install -U pip virtualenv virtualenvwrapper supervisor mercurial")

    # Set up supervisor
    conf_path = "/home/%s/etc" % env.user
    run("mkdir -p %s/supervisor/conf.d" % conf_path)
    conf_path += "/supervisord.conf"
    upload_template("deploy/supervisord.conf.template", conf_path, env, backup=False)
    run("mkdir -p /home/%s/tmp" % env.user)
    run("supervisord -c %s" % conf_path)

    # Set up virtualenv and virtualenvwrapper
    run("mkdir -p %s" % env.venv_home)
    bashrc = "/home/%s/.bashrc" % env.user
    run("echo 'export WORKON_HOME=%s' >> %s" % (env.venv_home, bashrc))
    run("echo 'export VIRTUALENVWRAPPER_PYTHON=/usr/local/bin/python2.7' >> %s" % bashrc)
    run("echo 'source $HOME/bin/virtualenvwrapper.sh' >> %s" % bashrc)

    # Set up memcached (with 50 MB of RAM)
    run("memcached -d -m 50 -s $HOME/memcached.sock -P $HOME/memcached.pid")

    print(green("Successfully set up git, mercurial, pip, virtualenv, "
                "supervisor, memcached.", bold=True))


@task
@log_call
def create():
    """
    Creates the environment needed to host the project.
    The environment consists of: virtualenv, database, project
    files, project-specific Python requirements, and Webfaction API objects.
    """
    # Set up virtualenv
    run("mkdir -p %s" % env.venv_home)
    with cd(env.venv_home):
        if exists(env.proj_name):
            if confirm("Virtualenv already exists in host server: %s"
                       "\nWould you like to replace it?" % env.proj_name):
                run("rm -rf %s" % env.proj_name)
            else:
                abort("Aborted at user request")
        run("virtualenv %s" % env.proj_name)
        # Make sure we don't inherit anything from the system's Python
        run("touch %s/lib/python2.7/sitecustomize.py" % env.proj_name)

    # Create elements with the Webfaction API
    _print(blue("Creating database and website records in the Webfaction "
                "control panel...", bold=True))
    srv, ssn, acn = get_webf_session()

    # Database
    db_user = get_webf_obj(srv, ssn, "db_user", env.proj_name)
    if db_user:
        abort("Database user %s already exists." % db_user["username"])
    db = get_webf_obj(srv, ssn, "db", env.proj_name)
    if db:
        abort("Databse %s already exists." % db["name"])
    if env.db_pass is None:
        env.db_pass = db_pass()
    srv.create_db(ssn, env.proj_name, "postgresql", env.db_pass)

    # Custom app
    app = get_webf_obj(srv, ssn, "app", env.proj_name)
    if app:
        abort("App %s already exists." % app["name"])
    app = srv.create_app(ssn, env.proj_name, "custom_app_with_port", True, "")
    # Save the application port to a file for later deployments
    run("echo '%s' > %s/app.port" % (app["port"], env.proj_path))

    # Static app
    static_app = get_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
    if static_app:
        abort("Static app %s already exists." % static_app["name"])
    static_app_name = "%s_static" % env.proj_name
    static_dir = "%s/static" % env.proj_path
    srv.create_app(ssn, static_app_name, "symlink54", False, static_dir)

    # Domain and subdomain
    dom = get_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
    if dom:
        abort("Domain %s already exists." % env.live_host)
    srv.create_domain(ssn, env.live_domain, env.live_subdomain)

    # Site record
    site = get_webf_obj(srv, ssn, "website", env.proj_name)
    if site:
        abort("Website: %s already exists." % site["name"])
    main_app, static_app = [env.proj_name, "/"], [static_app_name, "/static"]
    site = srv.create_website(ssn, env.proj_name, env.host_string, False,
                              [env.live_host], main_app, static_app)

    # Upload project files
    _print(blue("Uploading project files...", bold=True))
    if env.deploy_tool in env.vcs_tools:
        vcs_upload()
    else:
        rsync_upload()

    # Install project-specific requirements
    _print(blue("Installing project requirements...", bold=True))
    upload_template_and_reload("settings")
    with project():
        if env.reqs_path:
            pip("-r %s/%s" % (env.proj_path, env.reqs_path), show=False)
        pip("gunicorn setproctitle psycopg2 "
            "django-compressor python-memcached", show=False)
    # Bootstrap the DB
        _print(blue("Initializing the database...", bold=True))
        manage("createdb --noinput --nodata")
        python("from django.conf import settings;"
               "from django.contrib.sites.models import Site;"
               "site, _ = Site.objects.get_or_create(id=settings.SITE_ID);"
               "site.domain = '" + env.live_host + "';"
               "site.save();")
        if env.admin_pass:
            pw = env.admin_pass
            user_py = ("from django.contrib.auth import get_user_model;"
                       "User = get_user_model();"
                       "u, _ = User.objects.get_or_create(username='%s');"
                       "u.is_staff = u.is_superuser = True;"
                       "u.set_password('%s');"
                       "u.save();" % (env.admin_user, pw))
            python(user_py, show=False)
            shadowed = "*" * len(pw)
            print_command(user_py.replace("'%s'" % pw, "'%s'" % shadowed))

    return True


@task
@log_call
def remove():
    """
    Blow away the current project.
    """
    # Delete Webfaction API objects
    _print(blue("Removing database and website records from the Webfaction "
                "control panel...", bold=True))
    srv, ssn, acn = get_webf_session()
    website = get_webf_obj(srv, ssn, "website", env.proj_name)
    if website:
        del_webf_obj(srv, ssn, "website", env.proj_name, env.host_string)
    domain = get_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
    if domain:
        del_webf_obj(srv, ssn, "domain", env.live_domain, env.live_subdomain)
    main_app = get_webf_obj(srv, ssn, "app", env.proj_name)
    if main_app:
        del_webf_obj(srv, ssn, "app", main_app["name"])
    static_app = get_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
    if static_app:
        del_webf_obj(srv, ssn, "app", "%s_static" % env.proj_name)
    db = get_webf_obj(srv, ssn, "db", env.proj_name)
    if db:
        del_webf_obj(srv, ssn, "db", env.proj_name, "postgresql")
    db_user = get_webf_obj(srv, ssn, "db_user", env.proj_name)
    if db_user:
        del_webf_obj(srv, ssn, "db_user", env.proj_name, "postgresql")
    if isinstance(env.twitter_period, int):
        srv.delete_cronjob(ssn, "*/%s * * * * %s poll_twitter" % (
            env.twitter_period, env.manage))

    # Delete files/folders
    if exists(env.venv_path):
        run("rm -rf %s" % env.venv_path)
    if exists(env.repo_path):
        run("rm -rf %s" % env.repo_path)
    for template in get_templates().values():
        remote_path = template["remote_path"]
        if exists(remote_path):
            run("rm %s" % remote_path)

    # Update supervisor
    run("supervisorctl update")


##############
# Deployment #
##############

@task
@log_call
def restart():
    """
    Restart gunicorn worker processes for the project.
    If the processes are not running, they will be started.
    """
    pid_path = "%s/gunicorn.pid" % env.proj_path
    if exists(pid_path):
        run("supervisorctl restart gunicorn_%s" % env.proj_name)
    else:
        run("supervisorctl update")


@task
@log_call
def deploy():
    """
    Deploy latest version of the project.
    Backup current version of the project, push latest version of the project
    via version control or rsync, install new requirements, sync and migrate
    the database, collect any new static assets, and restart gunicorn's worker
    processes for the project.
    """
    if not exists(env.proj_path):
        if confirm("Project does not exist in host server: %s"
                   "\nWould you like to create it?" % env.proj_name):
            create()
        else:
            abort("Aborted at user request")

    # Backup current version of the project
    _print(blue("Backing up static files and database...", bold=True))
    with cd(env.proj_path):
        backup("last.db")
    if env.deploy_tool in env.vcs_tools:
        with cd(env.repo_path):
            if env.deploy_tool == "git":
                    run("git rev-parse HEAD > %s/last.commit" % env.proj_path)
            elif env.deploy_tool == "hg":
                    run("hg id -i > last.commit")
        with project():
            static_dir = static()
            if exists(static_dir):
                run("tar -cf static.tar --exclude='*.thumbnails' %s" %
                    static_dir)
    else:
        with cd(join(env.proj_path, "..")):
            excludes = ["*.pyc", "*.pio", "*.thumbnails"]
            exclude_arg = " ".join("--exclude='%s'" % e for e in excludes)
            run("tar -cf {0}.tar {1} {0}".format(env.proj_name, exclude_arg))

    # Deploy, update requirements, collect static assets, and migrate the DB
    _print(blue("Deploying the latest version of the project...", bold=True))
    with update_changed_requirements():
        if env.deploy_tool in env.vcs_tools:
            vcs_upload()
        else:
            rsync_upload()
    run("mkdir -p %s" % static())  # Create the STATIC_ROOT
    remote_path = static() + "/.htaccess"
    upload_template("deploy/htaccess", remote_path, backup=False)
    manage("collectstatic -v 0 --noinput")
    manage("migrate --noinput")

    # Upload templated config files
    _print(blue("Uploading configuration files...", bold=True))
    # Get the application port we saved on create() into the context
    with tempfile.TemporaryFile() as temp:
        get("%s/app.port" % env.proj_path, temp)
        temp.seek(0)
        port = temp.read()
        env.gunicorn_port = port.strip()
    for name in get_templates():
        upload_template_and_reload(name)
    restart()
    return True


@task
@log_call
def rollback():
    """
    Reverts project state to the last deploy.
    When a deploy is performed, the current state of the project is
    backed up. This includes the project files, the database, and all static
    files. Calling rollback will revert all of these to their state prior to
    the last deploy.
    """
    with update_changed_requirements():
        if env.deploy_tool in env.vcs_tools:
            with cd(env.repo_path):
                if env.deploy_tool == "git":
                        run("GIT_WORK_TREE={0} git checkout -f "
                            "`cat {0}/last.commit`".format(env.proj_path))
                elif env.deploy_tool == "hg":
                        run("hg update -C `cat last.commit`")
            with project():
                with cd(join(static(), "..")):
                    run("tar -xf %s/static.tar" % env.proj_path)
        else:
            with cd(env.proj_path.rsplit("/", 1)[0]):
                run("rm -rf %s" % env.proj_name)
                run("tar -xf %s.tar" % env.proj_name)
    with cd(env.proj_path):
        restore("last.db")
    restart()


@task
@log_call
def all():
    """
    Installs everything required on a new system and deploy.
    From the base software, up to the deployed project.
    """
    install()
    if create():
        deploy()


###############
# Maintenance #
###############

@task
@log_call
def pulldb():
    """
    Backup the remote database, download it, and restore it locally.
    """
    prompt = ("This will delete your development database and copy the contents from "
              "the production database. Continue?")
    if not confirm(prompt, default=False):
        abort("Aborting by user request.")
    backup("%s_production.sql" % env.proj_name)
    local("scp {0}@{1}:/home/{0}/{2}_production.sql .".format(
        env.user, env.host_string, env.proj_name))
    with fab_settings(warn_only=True):
        # This last part can output some errors, but the restoration goes well
        local_restore("%s_production.sql" % env.proj_name)


@task
@log_call
def pushdb():
    """
    Backup the local database, upload it, and restore it remotely.
    """
    prompt = ("This will delete your production database and copy the contents from "
              "the development database. Continue?")
    if not confirm(prompt, default=False):
        abort("Aborting by user request.")
    local_backup("%s_development.sql" % env.proj_name)
    local("scp {2}_development.sql {0}@{1}:/home/{0}/".format(
        env.user, env.host_string, env.proj_name))
    with fab_settings(warn_only=True):
        # This last part can output some errors, but the restoration goes well
        restore("%s_development.sql" % env.proj_name)


@task
@log_call
def pullmedia():
    """
    Downlaod the remote media files into the local MEDIA_ROOT.
    """
    cpmedia(upload=False)


@task
@log_call
def pushmedia():
    """
    Upload the local media files into the remote MEDIA_ROOT.
    """
    cpmedia(upload=True)


@task
@log_call
def setup_email():
    """
    Setup a mailbox to send out error emails to ADMINS.
    """
    if env.use_email == "#":
        abort("Please define email settings in the FABRIC dictionary first.")
    _print(blue("Setting up a Webfaction mailbox.", bold=True))
    srv, ssn, acn = get_webf_session()
    srv.create_mailbox(ssn, env.email_user)
    srv.change_mailbox_password(ssn, env.email_user, env.email_pass)
    srv.create_email(ssn, env.default_email, env.email_user)


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
