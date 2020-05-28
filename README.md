## Purpose

This tool attempts to translate a Betterment statement PDF into an OFX file,
suitable for import into Quicken (and perhaps other tools as well.)

The reason this works from the statement PDF is that the CSV that's available
from Betterment lacks any investment data, it's strictly cash transactions. This
attempts to report all activity.

While every attempt has been made to make this parsing robust, it's still likely
to be fragile for unexpected data, for account types I have no examples of, or
for PDF format changes. PDF is really painful to work from.

I also attempted to keep this straight Python, but ultimately had to give up on
the various python PDF parsing libraries. All failed in some respect; most often
because all the text jumbled together and structure was lost. (This is more a
limitation of PDF than the libraries' fault.) The java-based pdfbox was the only
tool I found that extracted enough structure to work from.

## Requirements

Download pdfbox from here: https://pdfbox.apache.org/

## References

This reference was invaluable in producing valid OFX: https://schemas.liquid-technologies.com/OFX/2.1.1/?page=seclistmsgsrsv11.html
