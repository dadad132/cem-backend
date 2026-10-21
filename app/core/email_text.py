"""
Tidy the text of e-mails turned into tickets and comments.

Mail clients (Outlook especially) send plain text with Windows line endings,
"blank" lines that are really non-breaking or zero-width spaces, and
[cid:image001.png@...] placeholders where signature images sit. Shown as-is,
a ticket gets long stretches of empty space.
"""
from __future__ import annotations

import re
from typing import Optional

_INVISIBLE = re.compile('[​‌‍‎‏⁠﻿­͏]')
_ODD_SPACES = re.compile('[   　]')
_CID_PLACEHOLDER = re.compile(r'\[cid:[^\]\n]*\]', re.IGNORECASE)
_BLANK_RUNS = re.compile(r'\n{3,}')

# Inline images smaller than this are signature logos and social icons, not
# photos of the problem (a phone photo is hundreds of KB)
SIGNATURE_IMAGE_MAX_BYTES = 32 * 1024


def tidy_email_text(text: Optional[str]) -> Optional[str]:
    """Normalise line endings and spaces, drop image placeholders, and allow
    at most one empty line in a row. Indentation and quoted replies are kept."""
    if not text:
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _INVISIBLE.sub('', text)
    text = _ODD_SPACES.sub(' ', text)
    text = _CID_PLACEHOLDER.sub('', text)
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    text = _BLANK_RUNS.sub('\n\n', text)
    return text.strip()
