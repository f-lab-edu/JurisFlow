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


class WithdrawalRateLimitError(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
