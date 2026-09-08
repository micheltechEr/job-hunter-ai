import pytest
from unittest.mock import MagicMock, patch
from app.services.gmail_service import GmailService, SCOPES

def test_gmail_service_scopes():
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES
    assert "https://www.googleapis.com/auth/userinfo.email" in SCOPES
    assert "openid" in SCOPES

def test_gmail_service_init():
    service = GmailService()
    assert service.credentials_path.name == "credentials.json"
    assert service.token_path.name == "token.json"

@patch("app.services.gmail_service.build")
@patch("app.services.gmail_service.Credentials")
def test_gmail_service_get_profile(mock_creds_cls, mock_build):
    mock_creds = MagicMock()
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

    mock_oauth_service = MagicMock()
    mock_oauth_service.userinfo().get().execute.return_value = {
        "email": "test@example.com"
    }
    mock_build.return_value = mock_oauth_service

    service = GmailService()
    profile = service.get_profile()

    assert profile["emailAddress"] == "test@example.com"
    mock_build.assert_called_once_with("oauth2", "v2", credentials=mock_creds)
