import unittest
from unittest.mock import MagicMock, patch
from app.services.gmail_service import GmailService, SCOPES

class TestGmailService(unittest.TestCase):
    def test_gmail_service_scopes(self):
        self.assertIn("https://www.googleapis.com/auth/gmail.send", SCOPES)
        self.assertIn("https://www.googleapis.com/auth/userinfo.email", SCOPES)
        self.assertIn("openid", SCOPES)

    def test_gmail_service_init(self):
        service = GmailService()
        self.assertEqual(service.credentials_path.name, "credentials.json")
        self.assertEqual(service.token_path.name, "token.json")

    @patch("app.services.gmail_service.build")
    @patch("app.services.gmail_service.Credentials")
    def test_gmail_service_get_profile(self, mock_creds_cls, mock_build):
        mock_creds = MagicMock()
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        mock_oauth_service = MagicMock()
        mock_oauth_service.userinfo().get().execute.return_value = {
            "email": "test@example.com"
        }
        mock_build.return_value = mock_oauth_service

        service = GmailService()
        profile = service.get_profile()

        self.assertEqual(profile["emailAddress"], "test@example.com")
        mock_build.assert_called_once_with("oauth2", "v2", credentials=mock_creds)

if __name__ == "__main__":
    unittest.main()
