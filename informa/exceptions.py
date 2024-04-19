class AppError(Exception):
    def __str__(self):
        return str(self.__doc__)


class PluginNotFound(AppError):
    "Supplied plugin name cannot be found!"


class MailgunKeyMissing(AppError):
    "Environment var MAILGUN_KEY is missing. Are you running in DEBUG?"


class MailgunSendFailed(AppError):
    "Mailgun email send failed!"


class ReachedLastSeen(AppError):
    "Reached a value already seen in previous run"
