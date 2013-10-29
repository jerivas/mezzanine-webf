bind = "127.0.0.1:%(gunicorn_port)s"
workers = 2
errorlog = "/home/%(user)s/logs/user/gunicorn_%(proj_name)s_error.log"
accesslog = "/home/%(user)s/logs/user/gunicorn_%(proj_name)s_access.log"
loglevel = "error"
proc_name = "%(proj_name)s"
