import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an OAuth token for Google Drive API access."
    )
    parser.add_argument(
        "--client-secrets",
        default="client_secret.json",
        help="Path to the OAuth client secret JSON downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--token-file",
        default="oauth_token.json",
        help="Where to write the authorized-user OAuth token JSON.",
    )
    args = parser.parse_args()

    client_secrets = Path(args.client_secrets)
    token_file = Path(args.token_file)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets),
        DRIVE_SCOPES,
    )
    credentials = flow.run_local_server(port=0)

    token_file.write_text(credentials.to_json(), encoding="utf-8")
    print(f"OAuth token written to {token_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
