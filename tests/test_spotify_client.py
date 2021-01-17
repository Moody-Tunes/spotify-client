import random
from base64 import b64encode
from datetime import datetime, timedelta
from unittest import mock
from urllib import parse

import pytest
from requests.exceptions import ConnectionError, HTTPError

from spotify_client.client import SpotifyClient
from spotify_client.config import Config
from spotify_client.exceptions import ClientException, ImproperlyConfigured, SpotifyException


def generate_random_unicode_string(length):
    # Generate a random string of non-ascii characters that is `length` characters long
    # From https://stackoverflow.com/questions/1477294/generate-random-utf-8-string-in-python
    # Credit: Jacob Wan

    include_ranges = [
        (0x0021, 0x0021),
        (0x0023, 0x0026),
        (0x0028, 0x007E),
        (0x00A1, 0x00AC),
        (0x00AE, 0x00FF),
        (0x0100, 0x017F),
        (0x0180, 0x024F),
        (0x2C60, 0x2C7F),
        (0x16A0, 0x16F0),
        (0x0370, 0x0377),
        (0x037A, 0x037E),
        (0x0384, 0x038A),
        (0x038C, 0x038C),
    ]

    alphabet = [
        chr(code_point) for current_range in include_ranges
        for code_point in range(current_range[0], current_range[1] + 1)
    ]

    return ''.join(random.choice(alphabet) for _ in range(length))


@pytest.fixture
def spotify_client():
    return SpotifyClient('test-client-id', 'test-secret-key')


class TestSpotifyClient(object):

    def test_auth_variables_are_read_from_config_if_configured(self):
        test_client_id = 'my-test-client-id'
        test_secret_key = 'my-test-secret-key'
        Config.configure(test_client_id, test_secret_key)

        client = SpotifyClient()

        assert client.client_id == test_client_id
        assert client.secret_key == test_secret_key

    def test_auth_variables_are_read_from_parameters_if_passed(self):
        test_client_id = 'my-test-client-id'
        test_secret_key = 'my-test-secret-key'

        config_client_id = 'config-client-id'
        config_secret_key = 'config-secret-key'
        Config.configure(config_client_id, config_secret_key)

        client = SpotifyClient(test_client_id, test_secret_key)

        assert client.client_id == test_client_id
        assert client.secret_key == test_secret_key

    def test_no_configuration_set_raises_error(self):
        Config.clear_config()

        with pytest.raises(ImproperlyConfigured):
            SpotifyClient()

    @mock.patch('requests.request')
    def test_make_auth_access_token_request_happy_path(self, mock_request, spotify_client):
        auth_code = 'test-auth-code'

        mock_response = mock.Mock()
        mock_response.json.return_value = {'access_token': auth_code}
        mock_request.return_value = mock_response

        # Calculate encoded auth header expected by Spotify
        auth_val = '{client_id}:{secret_key}'.format(client_id='test-client-id', secret_key='test-secret-key')

        auth_val = bytes(auth_val, encoding='utf-8')
        auth_header = b64encode(auth_val)

        expected_headers = {'Authorization': 'Basic {}'.format(auth_header.decode('utf8'))}
        expected_request_data = {'grant_type': 'client_credentials'}

        auth = spotify_client._make_auth_access_token_request()

        mock_request.assert_called_once_with(
            'POST',
            'https://accounts.spotify.com/api/token',
            data=expected_request_data,
            headers=expected_headers,
            params=None,
            json=None
        )

        assert auth == auth_code

    @mock.patch('requests.request')
    def test_make_auth_access_token_request_auth_code_not_found(self, mock_request, spotify_client):
        mock_response = mock.Mock()
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        auth = spotify_client._make_auth_access_token_request()

        assert auth is None

    @mock.patch('spotify_client.client.SpotifyClient._make_auth_access_token_request')
    def test_get_auth_access_token_success_sets_instance_variable(self, mock_auth_request, spotify_client):
        auth_code = 'test-auth-code'
        mock_auth_request.return_value = auth_code

        spotify_client._get_auth_access_token()

        assert spotify_client.auth_token == auth_code

    @mock.patch('spotify_client.client.SpotifyClient._make_auth_access_token_request')
    def test_get_auth_access_token_does_not_call_spotify_if_cached_token_found(self, mock_auth_request, spotify_client):
        auth_code = 'test-auth-code'
        spotify_client.auth_token = auth_code
        spotify_client.auth_token_last_refreshed = datetime.now()

        spotify_client._get_auth_access_token()

        mock_auth_request.assert_not_called()

    @mock.patch('spotify_client.client.SpotifyClient._make_auth_access_token_request')
    def test_get_auth_access_token_calls_spotify_if_cached_token_is_expired(self, mock_auth_request, spotify_client):
        auth_code = 'test-auth-code'
        spotify_client.auth_token = auth_code
        spotify_client.auth_token_last_refreshed = datetime.now() - timedelta(hours=1)

        spotify_client._get_auth_access_token()

        mock_auth_request.assert_called_once()

    @mock.patch('spotify_client.client.SpotifyClient._make_auth_access_token_request')
    def test_get_auth_access_token_success_raises_exception_for_missing_token(self, mock_auth_request, spotify_client):
        mock_auth_request.return_value = {}

        with pytest.raises(SpotifyException):
            spotify_client._get_auth_access_token()

    @mock.patch('requests.request')
    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    def test_make_spotify_request_happy_path(self, mock_auth, mock_request, spotify_client):
        auth_code = 'test-auth-code'
        dummy_response = {'status': 200, 'content': 'OK'}
        dummy_params = {'query': 'param'}
        dummy_data = {'key': 'value'}

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = None
        mock_response.json.return_value = dummy_response

        mock_auth.return_value = auth_code
        mock_request.return_value = mock_response

        resp = spotify_client._make_spotify_request('GET', '/dummy_endpoint', data=dummy_data, params=dummy_params)

        mock_request.assert_called_with(
            'GET',
            '/dummy_endpoint',
            params=dummy_params,
            data=dummy_data,
            headers={'Authorization': 'Bearer {}'.format(auth_code)},
            json=None
        )

        assert resp == dummy_response

    @mock.patch('requests.request')
    def test_make_spotify_request_uses_headers_if_passed(self, mock_request, spotify_client):
        dummy_response = {'status': 200, 'content': 'OK'}
        dummy_headers = {'Foo': 'bar'}
        dummy_params = {'query': 'param'}
        dummy_data = {'key': 'value'}

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = None
        mock_response.json.return_value = dummy_response
        mock_request.return_value = mock_response

        resp = spotify_client._make_spotify_request(
            'GET',
            '/dummy_endpoint',
            data=dummy_data,
            params=dummy_params,
            headers=dummy_headers
        )

        mock_request.assert_called_with(
            'GET',
            '/dummy_endpoint',
            params=dummy_params,
            data=dummy_data,
            headers=dummy_headers,
            json=None
        )

        assert resp == dummy_response

    @mock.patch('requests.request')
    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    def test_make_spotify_request_raises_spotify_exception_on_http_error(self, mock_auth, mock_request, spotify_client):
        auth_code = 'test-auth-code'
        mock_http_error = HTTPError()
        mock_http_error.response = mock.Mock()

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = mock_http_error

        mock_auth.return_value = auth_code
        mock_request.return_value = mock_response

        with pytest.raises(SpotifyException):
            spotify_client._make_spotify_request('GET', '/dummy_endpoint')

    @mock.patch('requests.request')
    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    def test_make_spotify_request_raises_spotify_exception_on_connection_error(
            self,
            mock_auth,
            mock_request,
            spotify_client
    ):
        auth_code = 'test-auth-code'

        mock_auth.return_value = auth_code
        mock_request.side_effect = ConnectionError

        with pytest.raises(SpotifyException):
            spotify_client._make_spotify_request('GET', '/dummy_endpoint')

    @mock.patch('requests.request')
    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    def test_make_spotify_request_raises_client_exception_on_unhandled_exception(
            self,
            mock_auth,
            mock_request,
            spotify_client
    ):
        auth_code = 'test-auth-code'
        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = Exception

        mock_auth.return_value = auth_code
        mock_request.return_value = mock_response

        with pytest.raises(ClientException):
            spotify_client._make_spotify_request('GET', '/dummy_endpoint')

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_playlists_for_category_happy_path(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'playlists': {
                'items': [{
                    'name': 'Super Dope',
                    'id': 'unique-id',
                    'owner': {
                        'id': 'unique-user-id'
                    },
                }],
            },
        }
        mock_request.return_value = mock_response

        expected_resp = [{
            'name': 'Super Dope'.encode('ascii', 'ignore'),
            'uri': 'unique-id',
            'user': 'unique-user-id'
        }]

        resp = spotify_client.get_playlists_for_category('category', 1)

        assert resp == expected_resp

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_happy_path(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code

        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'uri': 'song-uri',
                        'explicit': False,
                        'name': 'Glazed',
                        'artists': [{
                            'name': 'J Dilla'
                        }],
                    }
                }]
            }
        }
        mock_request.return_value = mock_response

        expected_return = {
            'name': 'Glazed',
            'artist': 'J Dilla',
            'code': 'song-uri'
        }

        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert expected_return, actual_return[0]

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_with_unicode_data(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code

        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}
        song_name = generate_random_unicode_string(10)
        song_artist = generate_random_unicode_string(10)

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'uri': 'song-uri',
                        'explicit': False,
                        'name': song_name,
                        'artists': [{
                            'name': song_artist
                        }],
                    }
                }]
            }
        }
        mock_request.return_value = mock_response

        expected_return = {
            'name': song_name,
            'artist': song_artist,
            'code': 'song-uri'
        }

        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert expected_return == actual_return[0]

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_excludes_song_already_seen(
            self,
            mock_request,
            mock_get_auth_token,
            spotify_client
    ):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code

        spotify_client.seen_songs = ['already-seen-code']
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'uri': 'already-seen-code',
                        'explicit': False,
                    }
                }]
            }
        }
        mock_request.return_value = mock_response

        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert not actual_return

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_excludes_song_is_explicit(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'uri': 'song-uri',
                        'explicit': True,
                    }
                }]
            }
        }
        mock_request.return_value = mock_response

        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert not actual_return

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_includes_explicit_song_if_specified(
            self,
            mock_request,
            mock_get_auth_token,
            spotify_client
    ):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'name': 'Glazed',
                        'uri': 'song-uri',
                        'explicit': True,
                        'artists': [{
                            'name': 'J Dilla'
                        }]
                    }
                }]
            }
        }

        mock_request.return_value = mock_response

        expected_return = {
            'name': 'Glazed',
            'artist': 'J Dilla',
            'code': 'song-uri'
        }
        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1, allow_explicit=True)
        assert expected_return == actual_return[0]

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_includes_non_explicit_song_if_specified(
            self,
            mock_request,
            mock_get_auth_token,
            spotify_client
    ):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': {
                        'name': 'Glazed',
                        'uri': 'song-uri',
                        'explicit': False,
                        'artists': [{
                            'name': 'J Dilla'
                        }]
                    }
                }]
            }
        }

        mock_request.return_value = mock_response

        expected_return = {
            'name': 'Glazed',
            'artist': 'J Dilla',
            'code': 'song-uri'
        }
        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1, allow_explicit=True)
        assert expected_return == actual_return[0]

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_songs_from_playlist_handles_empty_tracks(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [{
                    'track': None
                }]
            }
        }
        mock_request.return_value = mock_response

        actual_return = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert not actual_return

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_song_from_playlist_respects_limit(self, mock_request, mock_get_auth_token, spotify_client):
        auth_code = 'test-auth-code'
        mock_get_auth_token.return_value = auth_code
        mock_playlist = {'user': 'two-tone-killer', 'uri': 'beats-pub'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'tracks': {
                'items': [
                    {
                        'track': {
                            'uri': 'song-uri',
                            'explicit': False,
                            'name': 'Glazed',
                            'artists': [{
                                'name': 'J Dilla'
                            }],
                        },
                    },
                    {
                        'track': {
                            'uri': 'other-song-uri',
                            'explicit': False,
                            'name': 'King',
                            'artists': [{
                                'name': 'J Dilla'
                            }],
                        },
                    },
                ]
            }
        }
        mock_request.return_value = mock_response

        resp = spotify_client.get_songs_from_playlist(mock_playlist, 1)
        assert len(resp) == 1

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_audio_features_for_tracks_happy_path(self, mock_request, mock_get_auth_token, spotify_client):
        track = {'code': 'spotify:song:code'}
        tracks = [track]

        mock_get_auth_token.return_value = 'test-auth-code'

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'audio_features': [{
                'valence': .5,
                'energy': .5,
                'danceability': .5
            }]
        }
        mock_request.return_value = mock_response

        resp = spotify_client.get_audio_features_for_tracks(tracks)
        new_track = resp[0]

        assert new_track['energy'] == .5
        assert new_track['valence'] == .5
        assert new_track['danceability'] == .5

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_audio_features_for_tracks_handles_tracks_missing_features(
            self,
            mock_request,
            mock_get_auth_token,
            spotify_client
    ):
        mock_get_auth_token.return_value = 'test-auth-code'
        track = {'code': 'spotify:song:code'}
        tracks = [track]

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'audio_features': [{
                'valence': .5,
                'energy': 0,
                'danceability': 0
            }]
        }
        mock_request.return_value = mock_response

        resp = spotify_client.get_audio_features_for_tracks(tracks)
        new_track = resp[0]

        assert new_track['valence'] == .5
        assert new_track['energy'] == 0
        assert new_track['danceability'] == 0

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_audio_features_for_tracks_skips_tracks_missing_all_features(
            self,
            mock_request,
            mock_get_auth_token,
            spotify_client
    ):
        mock_get_auth_token.return_value = 'test-auth-code'
        track = {'code': 'spotify:song:code'}
        tracks = [track]

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'audio_features': [{}]
        }
        mock_request.return_value = mock_response

        resp = spotify_client.get_audio_features_for_tracks(tracks)
        new_track = resp[0]

        assert new_track.get('energy') is None
        assert new_track.get('valence') is None
        assert new_track.get('danceability') is None

    def test_build_spotify_oauth_confirm_link(self, spotify_client):
        state = 'user_id=1'
        scopes = ['playlist-modify-public user-top-read']
        redirect_uri = 'https://moodytunes.vm/moodytunes/spotify/callback/'

        url = spotify_client.build_spotify_oauth_confirm_link(state, scopes, redirect_uri)

        # Turn each query param to list, in the way urlparse will return
        query_params = {
            'client_id': ['test-client-id'],
            'response_type': ['code'],
            'redirect_uri': [redirect_uri],
            'state': [state],
            'scope': scopes
        }
        request = parse.urlparse(url)
        request_url = '{}://{}{}'.format(request.scheme, request.netloc, request.path)
        query_dict = parse.parse_qs(request.query)

        assert request_url == 'https://accounts.spotify.com/authorize'
        assert query_dict == query_params

    @mock.patch('requests.request')
    def test_get_user_tokens(self, mock_request, spotify_client):
        access_code = 'test-access-code'
        redirect_uri = 'https://moodytunes.vm/moodytunes/spotify/callback/'

        resp_data = {
            'access_token': 'some:access:token',
            'refresh_token': 'some:refresh:token'
        }

        mock_response = mock.Mock()
        mock_response.json.return_value = resp_data
        mock_request.return_value = mock_response

        user_tokens = spotify_client.get_access_and_refresh_tokens(access_code, redirect_uri)

        expected_request_data = {
            'grant_type': 'authorization_code',
            'code': access_code,
            'redirect_uri': redirect_uri
        }

        expected_headers = spotify_client._make_authorization_header()
        expected_response_data = {
            'access_token': 'some:access:token',
            'refresh_token': 'some:refresh:token'
        }

        mock_request.assert_called_once_with(
            'POST',
            'https://accounts.spotify.com/api/token',
            params=None,
            data=expected_request_data,
            headers=expected_headers,
            json=None
        )

        assert user_tokens == expected_response_data

    @mock.patch('requests.request')
    def test_refresh_access_token(self, mock_request, spotify_client):
        request_data = {'refresh_token': 'some:refresh:token'}

        mock_response = mock.Mock()
        mock_response.json.return_value = {'access_token': 'some:access:token'}
        mock_request.return_value = mock_response

        expected_headers = spotify_client._make_authorization_header()
        expected_response_data = {'access_token': 'some:access:token'}

        access_token = spotify_client.refresh_access_token(**request_data)

        request_data.update({'grant_type': 'refresh_token'})  # Update with constant grant_type from Spotify

        mock_request.assert_called_once_with(
            'POST',
            'https://accounts.spotify.com/api/token',
            params=None,
            headers=expected_headers,
            data=request_data,
            json=None
        )

        assert access_token == expected_response_data['access_token']

    @mock.patch('requests.request')
    def test_get_user_profile(self, mock_request, spotify_client):
        access_token = 'spotify:access:token'

        mock_profile_data = {'id': 'spotify-user-id'}

        mock_response = mock.Mock()
        mock_response.json.return_value = mock_profile_data
        mock_request.return_value = mock_response

        expected_headers = {'Authorization': 'Bearer {}'.format(access_token)}

        profile_data = spotify_client.get_user_profile(access_token)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/me',
            headers=expected_headers,
            params=None,
            data=None,
            json=None
        )

        assert profile_data == mock_profile_data

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_attributes_for_track(self, mock_request, mock_get_auth_token, spotify_client):
        mock_get_auth_token.return_value = 'test-auth-code'
        mock_song_code = 'spotify:track:1234567'
        mock_track_data = {
            'name': 'Sickfit',
            'artists': [{'name': 'Madlib'}],
            'album': {
                'href': 'https://example.com/album'
            }
        }

        expected_song_data = {
            'name': 'Sickfit',
            'artist': 'Madlib',
            'code': mock_song_code
        }

        mock_response = mock.Mock()
        mock_response.json.return_value = mock_track_data
        mock_request.return_value = mock_response

        song_data = spotify_client.get_attributes_for_track(mock_song_code)

        assert song_data == expected_song_data

    @mock.patch('requests.request')
    def test_get_users_playlist(self, mock_request, spotify_client):
        auth_code = 'spotify-auth-id'
        spotify_user_id = 'spotify:user:id'
        response_data = {'items': [{'name': 'test-playlist', 'id': '12345'}]}

        mock_response = mock.Mock()
        mock_response.json.return_value = response_data
        mock_request.return_value = mock_response

        expected_headers = {
            'Authorization': 'Bearer {}'.format(auth_code),
            'Content-Type': 'application/json'
        }

        resp = spotify_client.get_user_playlists(auth_code, spotify_user_id)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/users/{}/playlists'.format(spotify_user_id),
            params=None,
            headers=expected_headers,
            data=None,
            json=None
        )

        assert resp == response_data

    @mock.patch('requests.request')
    def test_create_playlist(self, mock_request, spotify_client):
        auth_code = 'spotify-auth-id'
        spotify_user_id = 'spotify:user:id'
        playlist_name = 'My Cool Playlist'
        playlist_id = 'spotify:playlist:id'

        mock_response = mock.Mock()
        mock_response.json.return_value = {'id': playlist_id}
        mock_request.return_value = mock_response

        expected_headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        expected_data = {
            'name': playlist_name,
            'public': True
        }

        retrieved_playlist_id = spotify_client.create_playlist(auth_code, spotify_user_id, playlist_name)

        mock_request.assert_called_once_with(
            'POST',
            'https://api.spotify.com/v1/users/{}/playlists'.format(spotify_user_id),
            params=None,
            headers=expected_headers,
            json=expected_data,
            data=None
        )

        assert retrieved_playlist_id == playlist_id

    @mock.patch('requests.request')
    def test_add_songs_to_playlist(self, mock_request, spotify_client):
        auth_code = 'spotify-auth-id'
        playlist_id = 'spotify:playlist:id'
        songs = ['spotify:track:1', 'spotify:track:2']

        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        expected_headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        expected_data = {'uris': songs}

        spotify_client.add_songs_to_playlist(auth_code, playlist_id, songs)

        mock_request.assert_called_once_with(
            'POST',
            'https://api.spotify.com/v1/playlists/{}/tracks'.format(playlist_id),
            params=None,
            headers=expected_headers,
            json=expected_data,
            data=None
        )

    def test_add_songs_to_playlist_raises_client_error_on_invalid_batch_size(self, spotify_client):
        auth_code = 'spotify-auth-id'
        playlist_id = 'spotify:playlist:id'
        songs = []

        for i in range(0, 200):
            songs.append(f'spotify:track:{i}')

        with pytest.raises(ClientException):
            spotify_client.add_songs_to_playlist(auth_code, playlist_id, songs)

    @mock.patch('requests.request')
    def test_delete_songs_from_playlist(self, mock_request, spotify_client):
        auth_code = 'spotify-auth-id'
        playlist_id = 'spotify:playlist:id'
        songs = ['spotify:track:1', 'spotify:track:2']

        mock_response = mock.Mock()
        mock_request.return_value = mock_response

        expected_headers = {'Authorization': 'Bearer {}'.format(auth_code)}

        expected_data = {'uris': songs}

        spotify_client.delete_songs_from_playlist(auth_code, playlist_id, songs)

        mock_request.assert_called_once_with(
            'DELETE',
            'https://api.spotify.com/v1/playlists/{}/tracks'.format(playlist_id),
            params=None,
            headers=expected_headers,
            json=expected_data,
            data=None
        )

    def test_delete_songs_from_playlist_raises_client_error_on_invalid_batch_size(self, spotify_client):
        auth_code = 'spotify-auth-id'
        playlist_id = 'spotify:playlist:id'
        songs = []

        for i in range(0, 200):
            songs.append(f'spotify:track:{i}')

        with pytest.raises(ClientException):
            spotify_client.delete_songs_from_playlist(auth_code, playlist_id, songs)

    @mock.patch('requests.request')
    def test_get_user_top_artists(self, mock_request, spotify_client):
        auth_code = 'spotify-auth-id'
        response_data = {
            'items': [
                {
                    "name": "Surf Curse",
                    "popularity": 63,
                    "type": "artist",
                    "uri": "spotify:artist:1gl0S9pS0Zw0qfa14rDD3D"
                },
                {
                    "name": "Madlib",
                    "popularity": 68,
                    "type": "artist",
                    "uri": "spotify:artist:5LhTec3c7dcqBvpLRWbMcf"
                },
                {
                    "name": "Elvis Depressedly",
                    "popularity": 47,
                    "type": "artist",
                    "uri": "spotify:artist:5a31Ij1sTxY9LUYVwgBp8m"
                }
            ]
        }

        mock_response = mock.Mock()
        mock_response.json.return_value = response_data
        mock_request.return_value = mock_response

        expected_headers = {'Authorization': 'Bearer {}'.format(auth_code)}
        expected_params = {'limit': 50}
        expected_response = ['Surf Curse', 'Madlib', 'Elvis Depressedly']

        retrieved_response = spotify_client.get_user_top_artists(auth_code, 50)

        mock_request.assert_called_with(
            'GET',
            'https://api.spotify.com/v1/me/top/artists',
            params=expected_params,
            headers=expected_headers,
            data=None,
            json=None
        )

        assert retrieved_response == expected_response

    @mock.patch('requests.request')
    @mock.patch('spotify_client.client.open')
    def test_upload_playlist_image(self, mock_open, mock_request, spotify_client):
        auth_code = 'test-spotify-auth-code'
        playlist_id = '12345'
        image_filepath = 'cover_file.jpg'
        image_data = b'my-image-data'

        mock_fp = mock.Mock()
        mock_fp.read.return_value = image_data
        mock_open.return_value.__enter__.return_value = mock_fp

        expected_data = b64encode(image_data)
        expected_headers = {
            'Authorization': 'Bearer {}'.format(auth_code),
            'Content-Type': 'image/jpeg'
        }

        spotify_client.upload_image_to_playlist(auth_code, playlist_id, image_filepath)

        mock_request.assert_called_once_with(
            'PUT',
            f'https://api.spotify.com/v1/playlists/{playlist_id}/images',
            params=None,
            headers=expected_headers,
            data=expected_data,
            json=None
        )

    def test_upload_playlist_image_raises_error_for_file_not_found(self, spotify_client):
        auth_code = 'test-spotify-auth-code'
        playlist_id = '12345'
        image_filepath = 'non_existent_file.jpg'

        with pytest.raises(ClientException):
            spotify_client.upload_image_to_playlist(auth_code, playlist_id, image_filepath)

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_search_handles_multiple_types_passed(self, mock_request, mock_get_auth_token, spotify_client):
        mock_get_auth_token.return_value = 'test-auth-code'
        query = 'genre:"hip hop"'
        search_types = ['track', 'artist']
        limit = 10

        expected_headers = {'Authorization': 'Bearer test-auth-code'}
        expected_params = {
            'q': query,
            'type': 'track,artist',
            'limit': limit,
            'market': 'US'
        }

        spotify_client.search(query, search_types, limit)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/search',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_search_handles_single_type_passed(self, mock_request, mock_get_auth_token, spotify_client):
        mock_get_auth_token.return_value = 'test-auth-code'
        query = 'genre:"hip hop"'
        search_types = 'track'
        limit = 10

        expected_headers = {'Authorization': 'Bearer test-auth-code'}
        expected_params = {
            'q': query,
            'type': 'track',
            'limit': limit,
            'market': 'US'
        }

        spotify_client.search(query, search_types, limit)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/search',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_search_uses_default_limit_if_none_passed(self, mock_request, mock_get_auth_token, spotify_client):
        mock_get_auth_token.return_value = 'test-auth-code'
        query = 'genre:"hip hop"'
        search_types = 'track'

        expected_headers = {'Authorization': 'Bearer test-auth-code'}
        expected_params = {
            'q': query,
            'type': 'track',
            'limit': spotify_client.MAX_SEARCH_SIZE,
            'market': 'US'
        }

        spotify_client.search(query, search_types)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/search',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    def test_search_raises_client_exception_if_invalid_limit_passed(self, spotify_client):
        query = 'genre:"hip hop"'
        search_types = 'track'
        limit = 100

        with pytest.raises(ClientException):
            spotify_client.search(query, search_types, limit)

    def test_search_raises_client_exception_if_invalid_search_type_passed(self, spotify_client):
        query = 'genre:"hip hop"'
        search_types = 'invalid-type'

        with pytest.raises(ClientException):
            spotify_client.search(query, search_types)

    @mock.patch('requests.request')
    def test_get_all_songs_from_playlist_with_no_pagination(self, mock_request, spotify_client):
        access_token = 'mock:access:token'
        playlist_id = 'test:playlist:id'

        response_data = {
            'items': [
                {"track": {"uri": "spotify:track:123456"}},
                {"track": {"uri": "spotify:track:234567"}},
                {"track": {"uri": "spotify:track:345678"}},
                {"track": {"uri": "spotify:track:456789"}},
            ],
            'next': None
        }

        expected_headers = {'Authorization': f'Bearer {access_token}'}
        expected_params = {'fields': 'items(track(uri)),next'}
        expected_response = [
            'spotify:track:123456',
            'spotify:track:234567',
            'spotify:track:345678',
            'spotify:track:456789'
        ]

        mock_response = mock.Mock()
        mock_response.json.return_value = response_data
        mock_request.return_value = mock_response

        resp = spotify_client.get_all_songs_from_user_playlist(access_token, playlist_id)

        assert resp == expected_response
        mock_request.assert_called_once_with(
            'GET',
            f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    @mock.patch('requests.request')
    def test_get_all_songs_from_playlist_with_pagination(self, mock_request, spotify_client):
        access_token = 'mock:access:token'
        playlist_id = 'test:playlist:id'

        response_data = [
            {
                'items': [
                    {"track": {"uri": "spotify:track:123456"}},
                    {"track": {"uri": "spotify:track:234567"}},
                    {"track": {"uri": "spotify:track:345678"}},
                    {"track": {"uri": "spotify:track:456789"}},
                ],
                'next': f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks?page=2'
            },
            {
                'items': [
                    {"track": {"uri": "spotify:track:098765"}},
                    {"track": {"uri": "spotify:track:987654"}},
                    {"track": {"uri": "spotify:track:876543"}},
                    {"track": {"uri": "spotify:track:765432"}},
                ],
                'next': None
            },
        ]

        expected_headers = {'Authorization': f'Bearer {access_token}'}
        expected_params = {'fields': 'items(track(uri)),next'}
        expected_response = [
            'spotify:track:123456',
            'spotify:track:234567',
            'spotify:track:345678',
            'spotify:track:456789',
            "spotify:track:098765",
            "spotify:track:987654",
            "spotify:track:876543",
            "spotify:track:765432",
        ]

        mock_response = mock.Mock()
        mock_response.json.side_effect = response_data
        mock_request.return_value = mock_response

        resp = spotify_client.get_all_songs_from_user_playlist(access_token, playlist_id)

        assert resp == expected_response

        expected_calls = [
            mock.call(
                'GET',
                f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks',
                headers=expected_headers,
                params=expected_params,
                data=None,
                json=None
            ),
            mock.call(
                'GET',
                f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks?page=2',
                headers=expected_headers,
                params=expected_params,
                data=None,
                json=None
            )
        ]

        mock_request.assert_has_calls(expected_calls, any_order=True)

    @mock.patch('requests.request')
    def test_get_recently_played_tracks_for_user(self, mock_request, spotify_client):
        auth_code = 'test-user-auth-code'

        expected_headers = {'Authorization': f'Bearer {auth_code}'}
        expected_params = {'limit': spotify_client.DEFAULT_RECENTLY_PLAYED_TRACKS_LIMIT}

        spotify_client.get_recently_played_tracks_for_user(auth_code)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/me/player/recently-played',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    @mock.patch('requests.request')
    def test_get_recently_played_tracks_for_user_with_limit(self, mock_request, spotify_client):
        auth_code = 'test-user-auth-code'
        limit = 5

        expected_headers = {'Authorization': f'Bearer {auth_code}'}
        expected_params = {'limit': limit}

        spotify_client.get_recently_played_tracks_for_user(auth_code, limit=limit)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/me/player/recently-played',
            headers=expected_headers,
            params=expected_params,
            data=None,
            json=None
        )

    @mock.patch('spotify_client.client.SpotifyClient._get_auth_access_token')
    @mock.patch('requests.request')
    def test_get_recommendations(self, mock_request, mock_get_auth_token, spotify_client):
        mock_get_auth_token.return_value = 'test-auth-code'
        target = 'valence'
        min_value = 0.25
        max_value = 0.5
        seed_tracks = ['test_code_1', 'test_code_2']
        limit = 5

        expected_headers = {'Authorization': 'Bearer test-auth-code'}
        expected_params = {
            'min_valence': min_value,
            'max_valence': max_value,
            'seed_tracks': seed_tracks,
            'limit': limit
        }

        spotify_client.get_recommendations(target, min_value, max_value, seed_tracks, limit)

        mock_request.assert_called_once_with(
            'GET',
            'https://api.spotify.com/v1/recommendations',
            params=expected_params,
            headers=expected_headers,
            data=None,
            json=None
        )

    @mock.patch('requests.request')
    def test_add_track_to_saved_songs(self, mock_request, spotify_client):
        auth_code = 'test-user-auth-code'
        song_uri = 'test-song-code'

        expected_headers = {'Authorization': f'Bearer {auth_code}'}
        expected_params = {'ids': song_uri}

        spotify_client.add_track_to_saved_songs(auth_code, song_uri)

        mock_request.assert_called_once_with(
            'PUT',
            'https://api.spotify.com/v1/me/tracks',
            params=expected_params,
            headers=expected_headers,
            json=None,
            data=None
        )

    def test_get_code_from_spotify_uri(self, spotify_client):
        song_code = 'spotify:track:19p0PEnGr6XtRqCYEI8Ucc'
        expected_code = '19p0PEnGr6XtRqCYEI8Ucc'

        code = spotify_client.get_code_from_spotify_uri(song_code)
        assert code == expected_code

    def test_batch_tracks_batches_list(self, spotify_client):
        items = [i for i in range(200)]
        batched_items = spotify_client.batch_tracks(items)

        assert len(batched_items) == 2

    def test_batch_tracks_returns_original_list_if_count_is_less_than_batch_size(self, spotify_client):
        items = [i for i in range(20)]
        batched_items = spotify_client.batch_tracks(items)

        assert len(batched_items) == 1

    def test_batch_tracks_batches_by_batch_size_if_provided(self, spotify_client):
        items = [i for i in range(50)]
        batched_items = spotify_client.batch_tracks(items, batch_size=10)

        assert len(batched_items) == 5

    def test_sanitize_log_data(self, spotify_client):
        data = {
            'code': 'super-secret-code',
            'foo': 'bar'
        }

        expected_sanitized_data = {
            'code': spotify_client.REDACT_VALUE,
            'foo': 'bar'
        }

        sanitized_data = spotify_client._sanitize_log_data(data)

        assert sanitized_data == expected_sanitized_data
