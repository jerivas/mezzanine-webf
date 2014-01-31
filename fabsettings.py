# Copy this settings to your local_settings.py.
# Comment out the settings where you want to use defaults.

FABRIC = {
    # Webfaction SSH username
    "SSH_USER": "",
    # Webfaction SSH password (You'll need this even if you use
    # key-based auth because of Webafaction's API)
    "SSH_PASS":  "",
    # Local path to SSH key file, for key-based auth
    "SSH_KEY_PATH":  "",
    # The IP address of your Webfaction server
    "HOSTS": [""],
    # Unique identifier for project.
    # Default: container folder name.
    "PROJECT_NAME": "",
    # Absolute remote path for virtualenvs.
    # Default: $HOME/.virtualenvs
    "VIRTUALENV_HOME":  "",
    # Name of the remote virtualenv to use.
    # Default: PROJECT_NAME
    "VIRTUALENV_NAME":  "",
    # Path to pip requirements, relative to project.
    # Default: requirements/project.txt
    "REQUIREMENTS_PATH": "",
    # Locale for your live project. Should end with ".UTF-8"
    # Default: en_US.UTF-8
    "LOCALE": "",
    # Domain where the site will be deployed
    "LIVE_DOMAIN": "",
    # Subdomian where the site will be deployed
    "LIVE_SUBDOMAIN": "",
    # Git remote repo path for the project
    # Comment this out if you used prepare_webfaction
    # Default: $HOME/webapps/git/repos/PROJECT_NAME
    "REPO_PATH": "",
    # Live database user
    "DB_USER": "",
    # Live database password
    "DB_PASS": "",
    # Live admin user password
    "ADMIN_PASS": "",
    # Minutes between every time Twitter is polled
    # Optional, but requires mezzanine.twitter
    "TWITTER_PERIOD": "",
    # Make sure these keys are available here
    "SECRET_KEY": SECRET_KEY,
    "NEVERCACHE_KEY": NEVERCACHE_KEY,
}
