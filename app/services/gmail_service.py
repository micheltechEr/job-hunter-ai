import os
import json
import base64
import logging
from pathlib import Path
from typing import Optional

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings

logger = logging.getLogger("job_hunter.gmail_service")

# Scopes requested for sending and basic profile identification
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

class GmailService:
    def __init__(self):
        project_root = Path(__file__).resolve().parents[2]
        self.credentials_path = project_root / "credentials.json"
        self.token_path = project_root / "token.json"
        self.verifier_path = project_root / ".oauth_verifier.json"
        self._oauth_verifiers = {}
        
    def credentials_exist(self) -> bool:
        """Verifies if Google Auth client configuration credentials.json file is present."""
        return os.path.exists(self.credentials_path)

    def is_authenticated(self) -> bool:
        """Checks if active token.json contains valid/renewable authentication credentials."""
        if not os.path.exists(self.token_path):
            return False
        try:
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            if creds and creds.valid:
                return True
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self.token_path, "w") as token:
                    token.write(creds.to_json())
                return True
        except Exception as e:
            logger.warning(f"Gmail credentials expired or invalid ({e}). Resetting token status.")
            try:
                if os.path.exists(self.token_path):
                    os.remove(self.token_path)
            except Exception:
                pass
        return False

    def _save_verifier(self, state: str, verifier: str):
        self._oauth_verifiers[state] = verifier
        try:
            data = {}
            if os.path.exists(self.verifier_path):
                try:
                    with open(self.verifier_path, "r") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data[state] = verifier
            data["latest"] = verifier
            with open(self.verifier_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Could not persist oauth verifier to disk: {e}")

    def _load_verifier(self, state: Optional[str]) -> Optional[str]:
        if state and state in self._oauth_verifiers:
            return self._oauth_verifiers.pop(state)
        
        # Check disk cache
        if os.path.exists(self.verifier_path):
            try:
                with open(self.verifier_path, "r") as f:
                    data = json.load(f)
                if state and state in data:
                    val = data.pop(state)
                    with open(self.verifier_path, "w") as f:
                        json.dump(data, f)
                    return val
                return data.get("latest")
            except Exception as e:
                logger.warning(f"Could not read oauth verifier from disk: {e}")

        if self._oauth_verifiers:
            _, val = self._oauth_verifiers.popitem()
            return val
        return None

    def get_auth_url(self) -> str:
        """Generates the OAuth 2.0 authorization URL redirection link with PKCE state tracking."""
        if not self.credentials_exist():
            raise FileNotFoundError(
                f"credentials.json missing at {self.credentials_path}. "
                "Download your OAuth Credentials from Google Cloud Console."
            )
        
        flow = Flow.from_client_secrets_file(
            self.credentials_path,
            scopes=SCOPES,
            redirect_uri=settings.GOOGLE_REDIRECT_URI
        )
        # Force prompt=consent and access_type=offline to receive long-lived refresh_token
        auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
        if state and getattr(flow, "code_verifier", None):
            self._save_verifier(state, flow.code_verifier)
        return auth_url

    async def fetch_token_from_code(self, code: str, state: Optional[str] = None) -> str:
        """Exchanges redirect authorization code for tokens using stored PKCE code_verifier."""
        flow = Flow.from_client_secrets_file(
            self.credentials_path,
            scopes=SCOPES,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
            state=state
        )
        code_verifier = self._load_verifier(state)
        if code_verifier:
            flow.code_verifier = code_verifier
            flow.fetch_token(code=code, code_verifier=code_verifier)
        else:
            flow.fetch_token(code=code)
            
        creds = flow.credentials
        
        # Save token
        with open(self.token_path, "w") as token:
            token.write(creds.to_json())
            
        # Clean up verifier cache file if exists
        try:
            if os.path.exists(self.verifier_path):
                os.remove(self.verifier_path)
        except Exception:
            pass

        # Retrieve verified email address if permitted, fallback gracefully
        email_address = "Conta Gmail Conectada"
        try:
            oauth_service = build("oauth2", "v2", credentials=creds)
            userinfo = oauth_service.userinfo().get().execute()
            email_address = userinfo.get("email", email_address)
        except Exception as profile_err:
            logger.info(f"Gmail userinfo read notice: {profile_err}. Token saved and valid for send.")
            
        return email_address

    def _get_service(self):
        """Prepares the Google API Discovery Service."""
        if not self.is_authenticated():
            raise PermissionError("User is not authenticated with Gmail OAuth API.")
            
        creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        return build("gmail", "v1", credentials=creds)

    def get_profile(self) -> dict:
        """Get authenticating Gmail account user profile information."""
        try:
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            oauth_service = build("oauth2", "v2", credentials=creds)
            userinfo = oauth_service.userinfo().get().execute()
            return {
                "emailAddress": userinfo.get("email", "Conta Conectada"),
                "messagesTotal": 0
            }
        except Exception as e:
            logger.warning(f"Could not fetch Gmail userinfo (using fallback): {e}")
            return {"emailAddress": "Conta Conectada", "messagesTotal": 0}

    def send_email(self, subject: str, body: str, recipient: str, attachment_path: Optional[str] = None) -> dict:
        """Sends an email with optional path attachments using Gmail API."""
        try:
            service = self._get_service()
            
            # Format MIME message
            message = MIMEMultipart()
            message["to"] = recipient
            message["subject"] = subject
            
            # Encapsulate body text message
            message.attach(MIMEText(body, "plain", "utf-8"))
            
            # Encapsulate file attachments
            if attachment_path and os.path.exists(attachment_path):
                filename = os.path.basename(attachment_path)
                with open(attachment_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename= {filename}"
                )
                message.attach(part)
            
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            sent_message = service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info(f"Email successfully sent to {recipient} with message ID: {sent_message.get('id')}")
            return sent_message
            
        except Exception as e:
            logger.error(f"Failed to send email to {recipient}: {e}")
            raise RuntimeError(f"Erro ao enviar email via Gmail API: {str(e)}")


gmail_service = GmailService()
