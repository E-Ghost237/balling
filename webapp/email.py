# ruff: noqa: E501 — the HTML email template below is markup, not code.
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from webapp.config import get_settings
from webapp.i18n import DEFAULT_LOCALE, t_locale

logger = logging.getLogger(__name__)

_LOGO_URL = "https://ballingpronostics.site/assets/logo.png"


def send_email(
    to: str, subject: str, body: str, code: str | None = None, locale: str = DEFAULT_LOCALE
) -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        logger.warning("Email not sent to %s (SMTP not configured): %s", to, subject)
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"Balling Predictions <{settings.smtp_from or settings.smtp_user}>"
    message["To"] = to
    message.attach(MIMEText(body, "plain"))
    message.attach(MIMEText(_render_html(subject, body, code, locale), "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(message["From"], [to], message.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def _render_html(subject: str, body: str, code: str | None, locale: str = DEFAULT_LOCALE) -> str:
    body_html = "".join(
        f'<p style="margin:0 0 12px;">{line}</p>' for line in body.strip().split("\n") if line.strip()
    )
    code_html = ""
    if code:
        code_html = f"""
        <div style="margin:24px 0;text-align:center;">
          <span style="display:inline-block;background:#f1f5f9;border-radius:8px;padding:14px 28px;
            font-size:28px;font-weight:600;letter-spacing:0.4em;color:#0f172a;font-family:monospace;">
            {code}
          </span>
        </div>
        """
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;padding:32px 16px;">
      <tr><td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0"
               style="max-width:480px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
          <tr>
            <td style="background:#0f172a;padding:32px 24px;text-align:center;">
              <img src="{_LOGO_URL}" alt="Balling Predictions" height="108" style="display:inline-block;border:0;" />
            </td>
          </tr>
          <tr>
            <td style="padding:32px;color:#0f172a;font-size:15px;line-height:1.6;">
              <h1 style="margin:0 0 16px;font-size:18px;font-weight:600;color:#0f172a;">{subject}</h1>
              {body_html}
              {code_html}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 24px;color:#94a3b8;font-size:12px;text-align:center;border-top:1px solid #e2e8f0;">
              {t_locale(locale, "email.footer")}
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
