"""E-mailed tickets: no long empty stretches, and signature logos are not attachments."""
from __future__ import annotations

from email.message import EmailMessage

from app.core.email_text import SIGNATURE_IMAGE_MAX_BYTES, tidy_email_text
from app.core.email_to_ticket_v2 import _clean_email_body_standalone, extract_email_attachments

# What Outlook's plain-text part looks like: CRLF line endings, "blank" lines
# that are non-breaking spaces, a zero-width space, and a placeholder where the
# signature logo sits
OUTLOOK_PLAIN = (
    "Hi team,\r\n\r\n\xa0\r\n\xa0\r\nThe printer on the 2nd floor is offline again.\r\n"
    "\r\n\r\n\r\n​\r\n\r\n"
    "Kind regards\r\n\r\n[cid:image001.png@01DA1234.56789ABC]\r\n\r\n\r\n"
    "Jane Botha\r\nOffice Manager   \r\n"
    "\r\n> On Mon, Support wrote:\r\n>     indented quote\r\n"
)


def test_no_runs_of_empty_lines():
    text = tidy_email_text(OUTLOOK_PLAIN)
    assert '\r' not in text
    assert '\n\n\n' not in text, 'at most one empty line in a row'
    assert text.startswith('Hi team,\n\nThe printer')


def test_image_placeholders_and_invisible_characters_removed():
    text = tidy_email_text(OUTLOOK_PLAIN)
    assert '[cid:' not in text
    assert '​' not in text and '\xa0' not in text
    assert 'Kind regards\n\nJane Botha\nOffice Manager\n' in text


def test_quotes_and_indentation_kept():
    text = tidy_email_text(OUTLOOK_PLAIN)
    assert text.endswith('> On Mon, Support wrote:\n>     indented quote')


def test_email_body_cleaner_uses_it():
    assert _clean_email_body_standalone(OUTLOOK_PLAIN) == tidy_email_text(OUTLOOK_PLAIN)


def test_empty_text():
    assert tidy_email_text('') == ''
    assert tidy_email_text(None) is None


def _mail_with_images():
    msg = EmailMessage()
    msg['Subject'] = 'Printer'
    msg.set_content(OUTLOOK_PLAIN)
    msg.add_alternative('<p>Hi team</p><img src="cid:image001.png@01DA1234.56789ABC">', subtype='html')
    html = msg.get_payload()[1]
    # Signature logo: inline, small, with a filename (as Outlook sends it)
    html.add_related(b'\x89PNG' + b'0' * 2000, maintype='image', subtype='png',
                     cid='<image001.png@01DA1234.56789ABC>', filename='image001.png', disposition='inline')
    # A photo the customer pasted into the message: inline but large
    html.add_related(b'\xff\xd8\xff' + b'1' * (SIGNATURE_IMAGE_MAX_BYTES + 5000), maintype='image',
                     subtype='jpeg', cid='<photo1>', filename='IMG_1234.jpg', disposition='inline')
    # A real attachment, however small
    msg.add_attachment(b'%PDF-1.4 tiny', maintype='application', subtype='pdf', filename='quote.pdf')
    return msg


def test_signature_logos_are_not_attachments():
    names = [a['filename'] for a in extract_email_attachments(_mail_with_images())]
    assert 'image001.png' not in names
    assert 'IMG_1234.jpg' in names, 'a photo sent inline is still kept'
    assert 'quote.pdf' in names
