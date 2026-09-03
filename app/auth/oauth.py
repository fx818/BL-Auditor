from authlib.integrations.starlette_client import OAuth

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def build_oauth(settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="google",
        server_metadata_url=GOOGLE_METADATA_URL,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
