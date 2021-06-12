class Playlist(object):
    """
    Represents a playlist from Spotify

    :ivar name: Name of the playlist
    :ivar uri: URI for the playlist on Spotify
    :ivar user: Spotify user who owns the playlist
    """

    def __init__(self, name: str, uri: str, user: str):
        self.name = name
        self.uri = uri
        self.user = user


class Song(object):
    """
    Represents a song from Spotify

    :ivar name: Name of the song
    :ivar code: URI for the song on Spotify
    :ivar artist: Name of the song artist
    """

    def __init__(self, name: str, code: str, artist: str):
        self.name = name
        self.code = code
        self.artist = artist

        # Placeholder values for song attributes
        self.valence = None
        self.energy = None
        self.danceability = None

    def update_attribute_values(self, valence: float, energy: float, danceability: float):
        self.valence = valence
        self.energy = energy
        self.danceability = danceability


class SpotifyAuth(object):
    """
    Represents the data for Spotify authentication token

    :ivar access_token: Access token for user on Spotify
    :ivar refresh_token: Refresh token for user on Spotify
    """

    def __init__(self, access_token: str, refresh_token: str):
        self.access_token = access_token
        self.refresh_token = refresh_token


class UserProfile(object):
    """
    Represents a Spotify user profile data

    :ivar user_id: Spotify User ID
    """

    def __init__(self, user_id: str):
        self.user_id = user_id