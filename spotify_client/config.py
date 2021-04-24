class Config(object):
    """Config module for setting up the client"""

    CLIENT_ID = None
    SECRET_KEY = None
    TIMEOUT_VALUE = None

    @classmethod
    def configure(cls, client_id: str, secret_key: str, timeout_value: float = None) -> None:
        """
        Configure the library with application-specific settings

        :param client_id: (str) Spotify client ID to use in requests
        :param secret_key: (str) Spotify secret key to use in requests
        :param timeout_value: (float) Amount (in seconds) to set for request timeout
        """
        cls.CLIENT_ID = client_id
        cls.SECRET_KEY = secret_key
        cls.TIMEOUT_VALUE = timeout_value

    @classmethod
    def clear_config(cls):
        """
        Clear the configuration set for the class
        """
        cls.CLIENT_ID = None
        cls.SECRET_KEY = None
        cls.TIMEOUT_VALUE = None
