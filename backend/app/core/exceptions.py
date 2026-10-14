class EmailAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AccountInactiveError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class MissingAccessTokenError(Exception):
    pass
