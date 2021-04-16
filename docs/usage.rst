Usage
================

First install the package with pip

.. code-block:: bash

   pip install spotify-client

Then import the client for usage in your application:

.. code-block:: python

   from spotify_client import SpotifyClient

   SPOTIFY_CLIENT_ID = 'client_id_from_spotify'
   SPOTIFY_SECRET_KEY = 'secret_key_from_spotify'

   client = SpotifyClient(
      SPOTIFY_CLIENT_ID,
      SPOTIFY_SECRET_KEY,
      identifier='test-spotify-client'
   )

You'll need to generate your client ID and secret keys for authenticating with Spotify from their API console. You can
find the steps for doing that in the `Spotify documentation <https://developer.spotify.com/documentation/general/guides/app-settings/>`_.

Optionally, you can also configure the client authentication configuration one time in a setup script by using the
Config class:

.. code-block:: python

   from spotify_client import SpotifyClient
   from spotify_client.config import Config

   SPOTIFY_CLIENT_ID = 'client_id_from_spotify'
   SPOTIFY_SECRET_KEY = 'secret_key_from_spotify'

   Config.configure(SPOTIFY_CLIENT_ID, SPOTIFY_SECRET_KEY)

   client = SpotifyClient()

You can optionally pass an identifier to the constructor. This will be used in logging messages by the client to
uniquely identify logs for the client instance.
