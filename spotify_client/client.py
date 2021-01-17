import copy
import logging
import random
from base64 import b64encode
from datetime import datetime, timedelta
from typing import List, Union
from urllib.parse import urlencode

import requests

from .config import Config
from .exceptions import ClientException, ImproperlyConfigured, SpotifyException


logger = logging.getLogger(__name__)


class SpotifyClient(object):
    """
    Wrapper around the Spotify API

    :ivar client_id:  Spotify client ID used for authenticating with API
    :ivar secret_key: Spotify secret key used for authenticating with API
    :ivar identifier: Identifier to include in log messages for identifying requests
    """

    PLAYLIST_BATCH_SIZE_LIMIT = 100
    MAX_SEARCH_SIZE = 50
    DEFAULT_RECENTLY_PLAYED_TRACKS_LIMIT = 30
    SEARCH_TYPE_OPTIONS = ['album', 'artist', 'playlist', 'track', 'show', 'episode']

    API_URL = 'https://api.spotify.com/v1'
    AUTH_URL = 'https://accounts.spotify.com/api/token'
    USER_AUTH_URL = 'https://accounts.spotify.com/authorize'

    REDACT_VALUE = '**********'
    REDACT_DATA_KEYS = ['Authorization', 'code', 'refresh_token', 'access_token']

    def __init__(self, client_id: str = None, secret_key: str = None, identifier: str = 'SpotifyClient'):
        self.client_id = client_id or Config.CLIENT_ID
        self.secret_key = secret_key or Config.SECRET_KEY

        if not all([self.client_id, self.secret_key]):
            raise ImproperlyConfigured(
                'Missing either client_id or secret_key variable. '
                'Please set these variables either in the Config class or as instance arguments'
            )

        self.fingerprint = identifier

        self.auth_token = None
        self.auth_token_last_refreshed = None
        self.seen_songs = []

    def _sanitize_log_data(self, data: dict) -> dict:
        """
        Redact sensitive data (auth headers, access tokens, etc.) from logging data and
        replace with a sanitized value.

        :param data: (dict) Request data to log that may contain sensitive information

        :return: (dict)
        """
        for name in data:
            if name in self.REDACT_DATA_KEYS:
                data[name] = self.REDACT_VALUE

        return data

    def _log(self, level: int, msg: str, extra: dict = None, exc_info: bool = False) -> None:
        """
        Log a message to the logger at a given level with optional extra info or traceback info.

        NOTE: Any data passed as `extra` should be a copy of the real data used in the code. This
        is because we do transformations on the data passed to sanitize sensitive values, so if we
        operate on the "real data" we could inadvertently update the actual data being used in the
        code.

        :param level: (int) Logging level to log at. Should be a constant from the `logging` library
        :param msg: (str) Log message to write to write
        :param extra: (dict) Optional payload of extra logging information
        :param exc_info: (bool) Include traceback information with log message
        """
        if extra is None:
            extra = {}

        extra.update({'fingerprint': self.fingerprint})

        # Redact sensitive information from logging data extra
        for key, data in extra.items():
            if isinstance(data, dict):
                extra[key] = self._sanitize_log_data(data)

        logger.log(level, msg, extra=extra, exc_info=exc_info)

    def _make_spotify_request(
            self,
            method: str,
            url: str,
            params: dict = None,
            data: Union[dict, bytes] = None,
            json: dict = None,
            headers: dict = None
    ) -> dict:
        """
        Make a request to the Spotify API and return the JSON response

        :param method: (str) HTTP method to use when sending request
        :param url: (str) URL to send request to
        :param params: (dict) GET query params to add to URL
        :param data: (dict or bytes) POST data to send in request
        :param json: (dict) JSON data to send in request
        :param headers: (dict) Headers to include in request

        :return (dict) Response content

        :raises: `SpotifyException` if request was unsuccessful
        :raises: `ClientException` if unexpected error encountered
        """

        if not headers:
            # Retrieve the header we need to make an auth request
            auth_token = self._get_auth_access_token()
            headers = {'Authorization': 'Bearer {}'.format(auth_token)}

        logging_params = copy.deepcopy(params)
        logging_data = copy.deepcopy(data)
        logging_json = copy.deepcopy(json)
        logging_headers = copy.deepcopy(headers)

        self._log(
            logging.INFO,
            'Making {method} request to Spotify URL: {url}'.format(
                method=method,
                url=url,
            ),
            extra={
                'request_method': method,
                'params': logging_params,
                'data': logging_data,
                'json': logging_json,
                'headers': logging_headers
            }
        )

        try:
            response = requests.request(
                method,
                url,
                params=params,
                data=data,
                json=json,
                headers=headers
            )

            response.raise_for_status()

            if response.text:
                response = response.json()

            self._log(logging.INFO, 'Successful request made to {}.'.format(url))
            self._log(
                logging.DEBUG,
                'Successful request made to {}.'.format(url),
                extra={'response_data': copy.deepcopy(response)}
            )

            return response

        except requests.exceptions.HTTPError as exc:
            response = exc.response

            self._log(
                logging.ERROR,
                'Received HTTPError requesting {}'.format(url),
                extra={
                    'request_method': method,
                    'data': logging_data,
                    'json': logging_json,
                    'params': logging_params,
                    'headers': logging_headers,
                    'response_code': response.status_code,
                    'response_reason': response.reason,
                    'response_data': response.text,
                },
                exc_info=True
            )

            raise SpotifyException('Received HTTPError requesting {}'.format(url)) from exc

        except requests.exceptions.ConnectionError as exc:
            self._log(
                logging.ERROR,
                'Received ConnectionError requesting {}'.format(url),
                extra={
                    'request_method': method,
                    'data': logging_data,
                    'json': logging_json,
                    'params': logging_params,
                    'headers': logging_headers,
                },
                exc_info=True
            )

            raise SpotifyException('Received ConnectionError requesting {}'.format(url)) from exc

        except Exception:
            self._log(logging.ERROR, 'Received unhandled exception requesting {}'.format(url), exc_info=True)

            raise ClientException('Received unhandled exception requesting {}'.format(url))

    def _get_auth_access_token(self) -> str:
        """
        Return the access token we need to make requests to Spotify.

        :return: (str) Key needed to authenticate with Spotify API

        :raises: `SpotifyException` if access token not retrieved
        """
        if not self.auth_token_last_refreshed or self.auth_token_last_refreshed < datetime.now() - timedelta(hours=1):
            access_token = self._make_auth_access_token_request()

            if access_token:
                self.auth_token = access_token
                self.auth_token_last_refreshed = datetime.now()
            else:
                self._log(logging.ERROR, 'Unable to retrieve access token from Spotify')
                raise SpotifyException('Unable to retrieve Spotify access token')

        return self.auth_token

    def _make_authorization_header(self) -> dict:
        """
        Build the Basic Authorization header used for Spotify API authentication

        :return: (str) Base 64 encoded string that contains the client ID and client secret key for application
        """
        auth_val = f'{self.client_id}:{self.secret_key}'
        auth_val = bytes(auth_val, encoding='utf-8')
        auth_header = b64encode(auth_val)

        return {'Authorization': 'Basic {}'.format(auth_header.decode('utf8'))}

    def _make_auth_access_token_request(self) -> str:
        """
        Get an access token from Spotify for authentication

        :return: (str) Token used for authentication with Spotify
        """
        headers = self._make_authorization_header()

        data = {'grant_type': 'client_credentials'}

        resp = self._make_spotify_request(
            'POST',
            self.AUTH_URL,
            data=data,
            headers=headers
        )

        return resp.get('access_token')

    def get_code_from_spotify_uri(self, code: str) -> str:
        """
        Get the Spotify code (alphanumeric value) from the Spotify song URI. Used in requests to Spotify
        for a track, as Spotify only cares about the alphanumeric value.

        Ex. Given 'spotify:track:19p0PEnGr6XtRqCYEI8Ucc', return '19p0PEnGr6XtRqCYEI8Ucc'

        :param code: (str) Full Spotify URI for a song

        :return: (str) Spotify code for the song
        """
        return code.split(':')[2]

    def batch_tracks(self, tracks: list, batch_size: int = None) -> List[list]:
        """
        Some Spotify endpoints have a limit on the number of tracks to send in one request. This method will
        take a list of tracks and create a list of batches for including in Spotify requests.

        :param tracks: (list) List of tracks to batch
        :param batch_size: (int) Optional size of batches to return

        :return: (list[list]) Original list of tracks, batched into lists of `batch_size`
        """
        batch_size = batch_size or self.PLAYLIST_BATCH_SIZE_LIMIT

        return [tracks[idx:idx + batch_size] for idx in range(0, len(tracks), batch_size)]

    def get_playlists_for_category(self, category: str, num_playlists: int) -> List[dict]:
        """
        Get a number of playlists from Spotify for a given category

        :param category: (str) Category ID of a genre in Spotify
        :param num_playlists: (int) Number of playlists to return

        :return: (list[dict]) Playlist mappings for the given category
            - name (str): Name of the playlist
            - uri (str): Spotify ID for the playlist
            - user (str): Spotify ID for the playlist owner
        """
        url = '{api_url}/browse/categories/{category_id}/playlists'.format(
            api_url=self.API_URL,
            category_id=category
        )

        params = {
            'country': 'US',
            'limit': num_playlists
        }

        response = self._make_spotify_request('GET', url, params=params)

        retrieved_playlists = []
        for playlist in response['playlists']['items']:
            payload = {
                'name': playlist['name'].encode('ascii', 'ignore'),
                'uri': playlist['id'],
                'user': playlist['owner']['id']
            }

            retrieved_playlists.append(payload)

        # Shuffle playlists to ensure freshness
        random.shuffle(retrieved_playlists)

        return retrieved_playlists

    def get_songs_from_playlist(self, playlist: dict, num_songs: int, allow_explicit: bool = False) -> List[dict]:
        """
        Get a number of songs randomly from the given playlist.
        List of songs is shuffled and the number of desired tracks are returned.
        :param playlist: (dict) Mapping of values needed to retrieve playlist tracks
        :param num_songs: (int) Number of songs to return from this playlist
        :param allow_explicit: (bool) Flag to indicate whether or not to return explicit songs (default False)

        :return: (list[dict]) Song mappings from the given playlist
            - name (str): Name of the song
            - artist (str): Name of the artist
            - code (str): Spotify ID of the song
        """
        url = '{api_url}/users/{user_id}/playlists/{playlist_id}'.format(
            api_url=self.API_URL,
            user_id=playlist['user'],
            playlist_id=playlist['uri']
        )

        params = {'fields': 'tracks(items(track(id,uri,name,artists,explicit)))'}

        response = self._make_spotify_request('GET', url, params=params)

        processed_tracks = 0
        retrieved_tracks = []

        tracks = response['tracks']['items']

        # Shuffle tracks to ensure freshness
        random.shuffle(tracks)

        # Process number of tracks requested, but if playlist does not have enough to return the full
        # amount we return what we get
        for track in tracks:
            if not track['track']:
                # Sometimes Spotify doesn't return anything for a track. Unsure why, but if the track is None
                # we should just skip it and keep going
                continue

            uri = track['track']['uri']
            is_explicit = track['track']['explicit']

            # Skip song if song has already been seen
            if uri in self.seen_songs:
                continue

            # Skip song if we shouldn't include explicit songs and song is explicit
            if not allow_explicit and is_explicit:
                continue

            payload = {
                'name': track['track']['name'],
                'artist': track['track']['artists'][0]['name'],
                'code': uri
            }

            retrieved_tracks.append(payload)
            self.seen_songs.append(uri)
            processed_tracks += 1

            if processed_tracks >= num_songs:
                break

        return retrieved_tracks

    def get_audio_features_for_tracks(self, tracks: List[dict]) -> List[dict]:
        """
        Get audio features (attributes we use for determining song emotion) for a number of tracks. Will update the
        tracks in place, each track in the list is a dictionary of values needed to create a Song object. This method
        returns the list of tracks updated with the tracks emotion attribute values.

        :param tracks: (list[dict]) Song mappings

        :return: (list[dict]) Song mappings + (energy, valence, danceability)
        """
        # Need to batch tracks as Spotify limits the number of tracks sent in one request
        batched_tracks = self.batch_tracks(tracks)

        for batch in batched_tracks:
            url = '{api_url}/audio-features'.format(api_url=self.API_URL)

            # Construct query params list from track ids in batch
            # Strip spotify:track: from the uri (Spotify just wants the id)
            track_ids = [self.get_code_from_spotify_uri(track['code']) for track in batch]
            params = {'ids': ','.join([track_id for track_id in track_ids])}

            response = self._make_spotify_request('GET', url, params=params)

            # Response is returned in the order requested (req:[1,2,3] -> res:[1,2,3])
            # If an object is not found, a null value is returned in the appropriate position
            for track, track_data in zip(batch, response['audio_features']):
                if track_data:
                    valence = track_data.get('valence')
                    energy = track_data.get('energy')
                    danceability = track_data.get('danceability')

                    # Skip tracks that are missing any of the attributes we're looking for
                    if not any([valence, energy, danceability]):
                        continue

                    track.update({
                        'valence': valence,
                        'energy': energy,
                        'danceability': danceability
                    })

        return tracks

    def build_spotify_oauth_confirm_link(self, state: str, scopes: List[str], redirect_url: str) -> str:
        """
        First step in the Spotify user authorization flow. This builds the request to authorize the application with
        Spotify. Note that this function simply builds the URL for the user to visit, the actual behavior for the
        authorization need to be made client-side.

        :param state: (str) State to pass in request. Used for validating redirect URI against request
        :param scopes: (list(str)) Spotify OAuth scopes to grant in authentication request
        :param redirect_url: (str) URL to redirect to after OAuth confirmation

        :return: (str) URL for Spotify OAuth confirmation
        """
        params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'scope': ' '.join(scopes),
            'redirect_uri': redirect_url,
            'state': state
        }

        return '{url}?{params}'.format(url=self.USER_AUTH_URL, params=urlencode(params))

    def get_access_and_refresh_tokens(self, code: str, redirect_url: str) -> dict:
        """
        Make a request to the Spotify authorization endpoint to obtain the access and refresh tokens for a user after
        they have granted our application permission to Spotify on their behalf.

        :param code: (str) Authorization code returned from initial request for Spotify OAuth
        :param redirect_url: (str) URL to redirect to after OAuth confirmation

        :return: (dict)
            - access_token (str)
            - refresh_token (str)
        """
        data = {
            'grant_type': 'authorization_code',  # Constant; From Spotify documentation
            'code': code,
            'redirect_uri': redirect_url,
        }

        headers = self._make_authorization_header()

        response = self._make_spotify_request('POST', self.AUTH_URL, data=data, headers=headers)

        return {
            'access_token': response['access_token'],
            'refresh_token': response['refresh_token']
        }

    def refresh_access_token(self, refresh_token: str) -> str:
        """
        Refresh application on behalf of user given a refresh token. On a successful response, will return an
        access token for the user good for the timeout period for Spotify authentication (One hour.)

        :param refresh_token: (str) Refresh token for user from Spotify

        :return: (str) New access token for user
        """
        data = {
            'grant_type': 'refresh_token',  # Constant; From Spotify documentation
            'refresh_token': refresh_token
        }

        headers = self._make_authorization_header()

        response = self._make_spotify_request('POST', self.AUTH_URL, headers=headers, data=data)

        return response['access_token']

    def get_user_profile(self, access_token: str) -> dict:
        """
        Get data on the user from Spotify API /me endpoint

        :param access_token: (str) OAuth token from Spotify for the user

        :return: (dict) Payload for the given user
        """
        url = '{api_url}/me'.format(api_url=self.API_URL)
        headers = {'Authorization': 'Bearer {}'.format(access_token)}

        return self._make_spotify_request('GET', url, headers=headers)

    def get_attributes_for_track(self, uri: str) -> dict:
        """
        Fetch song metadata for a singular track

        :param uri: (str) URI of song to search for on Spotify

        :return: (dict)
            - name (str)
            - artist (str)
            - code (str)
        """
        song_id = self.get_code_from_spotify_uri(uri)
        url = '{api_url}/tracks/{id}'.format(
            api_url=self.API_URL,
            id=song_id
        )

        track = self._make_spotify_request('GET', url)

        return {
            'name': track['name'],
            'artist': track['artists'][0]['name'],
            'code': uri
        }

    def get_user_playlists(self, auth_code: str, spotify_user_id: str) -> dict:
        """
        Get all playlists for the given Spotify user.

        :param auth_code: (str) Access token for user from Spotify
        :param spotify_user_id: (str) Spotify username for the given user

        :return: (dict) Spotify response for all users playlists
        """
        url = '{api_url}/users/{user_id}/playlists'.format(
            api_url=self.API_URL,
            user_id=spotify_user_id
        )

        headers = {
            'Authorization': 'Bearer {}'.format(auth_code),
            'Content-Type': 'application/json'
        }

        return self._make_spotify_request('GET', url, headers=headers)

    def create_playlist(self, auth_code: str, spotify_user_id: str, playlist_name: str) -> str:
        """
        Create a playlist for the given Spotify user. Note that this creates an empty playlist,
        a subsequent API call should be made to populate the playlist with songs.

        :param auth_code: (str) Access token for user from Spotify
        :param spotify_user_id: (str) Spotify username for the given user
        :param playlist_name: (str) Name of the playlist to be created

        :return: (str) Spotify playlist ID for the created playlist
        """
        url = '{api_url}/users/{user_id}/playlists'.format(
            api_url=self.API_URL,
            user_id=spotify_user_id
        )

        headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        data = {
            'name': playlist_name,
            'public': True
        }

        resp = self._make_spotify_request('POST', url, headers=headers, json=data)

        return resp['id']

    def add_songs_to_playlist(self, auth_code: str, playlist_id: str, songs: list) -> None:
        """
        Add songs to a specified playlist

        :param auth_code: (str) Access token for user from Spotify
        :param playlist_id: (str) Spotify playlist ID to add songs to
        :param songs: (list) Collection of Spotify track URIs to add to playlist
        """
        if len(songs) > self.PLAYLIST_BATCH_SIZE_LIMIT:
            raise ClientException(
                f'Invalid number of songs to add to playlist; \
                Must be equal to or less than {self.PLAYLIST_BATCH_SIZE_LIMIT}'
            )

        url = '{api_url}/playlists/{playlist_id}/tracks'.format(
            api_url=self.API_URL,
            playlist_id=playlist_id
        )

        headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        data = {'uris': songs}

        self._make_spotify_request('POST', url, headers=headers, json=data)

    def delete_songs_from_playlist(self, auth_code: str, playlist_id: str, songs: list) -> None:
        """
        Remove songs from a specified playlist

        :param auth_code: (str) Access token for user from Spotify
        :param playlist_id: (str) Spotify playlist ID to remove songs from
        :param songs: (list) Collection of Spotify track URIs to remove from playlist
        """
        if len(songs) > self.PLAYLIST_BATCH_SIZE_LIMIT:
            raise ClientException(
                f'Invalid number of songs to delete from playlist; \
                Must be equal to or less than {self.PLAYLIST_BATCH_SIZE_LIMIT}'
            )

        url = '{api_url}/playlists/{playlist_id}/tracks'.format(
            api_url=self.API_URL,
            playlist_id=playlist_id
        )

        headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        data = {'uris': songs}

        self._make_spotify_request('DELETE', url, headers=headers, json=data)

    def get_user_top_artists(self, auth_code: str, max_top_artists: int) -> List[str]:
        """
        Retrieve the top artists from Spotify for a user.

        :param auth_code: (str) Access token for user from Spotify
        :param max_top_artists: (int) Max number of top artists to retrieve

        :return: (list(str)) List of top artists for the user from Spotify
        """
        url = '{api_url}/me/top/artists'.format(api_url=self.API_URL)

        headers = {'Authorization': 'Bearer {}'.format(auth_code)}
        params = {'limit': max_top_artists}

        resp = self._make_spotify_request('GET', url, headers=headers, params=params)

        # Parse the response for the artist name values
        artists = []
        for item in resp['items']:
            artists.append(item['name'])

        return artists

    def upload_image_to_playlist(self, auth_code: str, playlist_id: str, image_filepath: str) -> None:
        """
        Upload a custom image for a playlist. Requires ugc-image-upload and
        playlist-modify-public/playlist-modify-private scopes from Spotify

        :param auth_code: (str) Access token for user who owns the playlist
        :param playlist_id: (str) Playlist ID from Spotify
        :param image_filepath: (str) Path to the image file to upload
        """
        url = '{api_url}/playlists/{playlist_id}/images'.format(api_url=self.API_URL, playlist_id=playlist_id)
        headers = {
            'Authorization': 'Bearer {}'.format(auth_code),
            'Content-Type': 'image/jpeg'
        }

        try:
            with open(image_filepath, 'rb') as image_file:
                image_data = b64encode(image_file.read())
        except FileNotFoundError:
            raise ClientException('File {} does not exist'.format(image_filepath))

        self._make_spotify_request('PUT', url, data=image_data, headers=headers)

    def search(self, query: str, search_types: Union[str, List], limit: int = None) -> dict:
        """
        Query the API for resources that match a given query.

        :param query: (str) Query to send to the endpoint
        :param search_types: (str | list) Single or multiple item types to search across. Must be one of
        [album, artist, playlist, track, show, episode]
        :param limit: (int) Maximum number of resources to return. Default to max of 50 resources

        :return: (dict) Response from API.
        See https://developer.spotify.com/documentation/web-api/reference/search/search/#fields-reference for full
        details.
        """
        url = '{api_url}/search'.format(api_url=self.API_URL)
        limit = limit or self.MAX_SEARCH_SIZE

        if limit > self.MAX_SEARCH_SIZE:
            raise ClientException(f'Invalid limit. Must be less than {self.MAX_SEARCH_SIZE}')

        if not isinstance(search_types, list):
            search_types = [search_types]

        # Validate that the search_types parameters are valid
        for _type in search_types:
            if _type not in self.SEARCH_TYPE_OPTIONS:
                raise ClientException(f'{_type} is not a valid search type. Options are {self.SEARCH_TYPE_OPTIONS}')

        params = {
            'q': query,
            'type': ','.join(search_types),
            'limit': limit,
            'market': 'US'
        }

        return self._make_spotify_request('GET', url, params=params)

    def get_all_songs_from_user_playlist(self, auth_code: str, playlist_id: str) -> List[str]:
        """
        Get all the song URIs from the playlist for the given user

        :param auth_code: (str) Access token for user who owns the playlist
        :param playlist_id: (str) Playlist ID from Spotify

        :return: List of Spotify URIs for songs in playlist
        """
        song_uris = []

        url = f'{self.API_URL}/playlists/{playlist_id}/tracks'
        headers = {'Authorization': f'Bearer {auth_code}'}
        params = {'fields': 'items(track(uri)),next'}

        while url:
            response = self._make_spotify_request('GET', url, headers=headers, params=params)

            tracks = response['items']
            for track in tracks:
                if track['track'] and track['track']['uri'] not in song_uris:
                    song_uris.append(track['track']['uri'])

            url = response['next']

        return song_uris

    def get_recently_played_tracks_for_user(self, auth_code: str, limit: int = None) -> dict:
        """
        Retrieve the top `limit` tracks that a user has listened to on Spotify
        - Requires the `user-read-recently-played` Spotify OAuth scope

        :param auth_code: (str) Access token for user from Spotify
        :param limit: (int) Number of tracks to return (default to self.DEFAULT_RECENTLY_PLAYED_TRACKS_LIMIT)

        :return: (dict) Response of play history objects from Spotify API
        """
        headers = {'Authorization': f'Bearer {auth_code}'}
        params = {'limit': limit or self.DEFAULT_RECENTLY_PLAYED_TRACKS_LIMIT}

        return self._make_spotify_request(
            'GET',
            f'{self.API_URL}/me/player/recently-played',
            headers=headers,
            params=params
        )

    def get_recommendations(
            self,
            target: str,
            min_value: float,
            max_value: float,
            seed_tracks: List[str],
            limit: int
    ):
        """
        Make a request to get recommendations for the target values based on the tracks supplied
        as the seed entity batch. This will return songs from the Spotify API whose attributes
        most closely match the seed tracks attributes for the given target

        :param target: (str) Spotify attribute value to use in recommendation query
        :param min_value: (float) Minimum target value to use in recommendation query
        :param max_value: (float) Maximum target value to use in recommendation query
        :param seed_tracks: (List[str]) List of Spotify song ids to use as entity batch
        :param limit: (int) Max number of results to return from recommendation query

        :return: (dict) Recommendations object from Spotify API
        """
        return self._make_spotify_request(
            'GET',
            f'{self.API_URL}/recommendations',
            params={
                f'min_{target}': min_value,
                f'max_{target}': max_value,
                'seed_tracks': seed_tracks,
                'limit': limit
            }
        )

    def add_track_to_saved_songs(self, auth_code: str, song_uri: str):
        """
        Add a Spotify song to a users saved songs library

        :param auth_code: (str) Access token for user from Spotify
        :param song_uri: (str) Spotify URI code for song to save
        """
        return self._make_spotify_request(
            'PUT',
            f'{self.API_URL}/me/tracks',
            params={'ids': song_uri},
            headers={'Authorization': f'Bearer {auth_code}'}
        )
