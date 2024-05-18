class AppError(Exception):
    def __str__(self):
        return str(self.__doc__)


class MailgunKeyMissing(AppError):
    "Environment var MAILGUN_KEY is missing. Are you running in DEBUG?"


class MailgunTemplateFail(AppError):
    "Mailgun template supplied without content"


class MailgunSendFailed(AppError):
    "Mailgun email send failed!"


class ReachedLastSeen(AppError):
    "Reached a value already seen in previous run"
