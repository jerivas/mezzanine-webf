###################
# DEPLOY SETTINGS #
###################

# Copy this portion into the corresponding section of your local_settings.py.

# Domains for public site
ALLOWED_HOSTS = []

FABRIC = {
    "DEPLOY_TOOL": "git",  # Deploy with "git", "hg", or "rsync"
    "SSH_USER": "",  # Wefaction username
    # "SSH_PASS": "",  # SSH and Webfaction account password
    # "SSH_KEY_PATH":  "",  # Local path to SSH key file, for key-based auth
    "HOSTS": ["XX.XX.XX.XX"],  # The IP address of your Webfaction server
    "DOMAINS": ALLOWED_HOSTS,  # Edit domains in ALLOWED_HOSTS
    "LIVE_DOMAIN": "example.com",  # Domain to associate the app with
    "LIVE_SUBDOMAIN": "www",  # Subdomain to associate the app with (optional)
    "REQUIREMENTS_PATH": "requirements.txt",  # Project's pip requirements
    "LOCALE": "en_US.UTF-8",  # Should end with ".UTF-8"
    "NUM_WORKERS": 2,  # Limit the amount of workers for gunicorn
    # "DB_PASS": "",  # Live database password
    # "ADMIN_PASS": "",  # Live admin user password
    # "TWITTER_PERIOD": None,  # Minutes
    "SECRET_KEY": SECRET_KEY,
    "NEVERCACHE_KEY": NEVERCACHE_KEY,

    # Email settings
    # "EMAIL_USER": "",  # Webfaction mailbox username
    # "EMAIL_PASS": "",  # Webfaction mailbox password
    # "DEFAULT_EMAIL": "",  # Webfacion email address
}
