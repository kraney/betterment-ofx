# Betterment-ofx

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

I think I produce the right records for each item, but I am not a finance person
and can't swear to it.

## Quirks and oddities

I found that there are some quirks in how Betterment does things and in their
reports that make this an imperfect translation.

### Rounding errors

Betterment reports show shares and price to 3 decimal places. Quicken adjusts
the price to make the share count correct. However, this results in occasional
rounding errors that result in 0.0001 share adjustment records.

It's possible that the _price_ is more accurate, and I could adjust the share
count instead to avoid this. But I have not experimented to see if this produces
a better result.

### Sweep Accounts

They have a single sweep account shared across all taxable accounts, and another
shared across all IRAs. OFX seems to expect a distinct sweep account per trading
account.  So it's not clear how to map this correctly.

Currently, I mostly ignore the sweep account, just rolling the relevant
transactions into the records for the investing account, but this means that if
you have more than one account tied to the sweep account, the balance is hard to
reconcile.

I have thought about creating a "virtual" account for each sweep account, and
showing transfers to and from it. This would most closely match what Betterment
actually does, except for fabricating an account number for this made-up
account.

### End-of-quarter

Dividends that pay out in the last few days of the quarter show up as cash
transactions, but the corresponding security transaction does not appear. Most
(perhaps all?) of the time, the security transaction shows up the next quarter,
even though they are dated before that quarter starts.

For dividends that occur during most of the quarter, I ignore the cash
transaction and only report the security transaction. Otherwise it creates
duplicate records. But at the end of the quarter, I don't have the security
record.

Reporting only the cash transaction doesn't show which security it came from. If
I ignore the cash transaction, the cash balance is wrong. If I report it, then
next quarter when the security record comes in, it becomes a duplicate record.
If I ignore *that*, then you can never tell which security gave the dividend.

For now, I've elected to report the cash transaction at the end of the quarter,
and also the security transaction at the beginning of the next quarter. That
leaves a clean-up task for the user to go delete the less-useful cash records
once the more-useful security records come in. At least it's easier than typing
it all from scratch.

### End-of-year

I notice that there is a similar situation where I had some cash records on Dec
31. However, the corresponding security transaction was never reported, not on
the "correct" quarter, and not on the next quarter / next calendar year either.
Those records are just omitted completely. This makes it hard to reconcile
things perfectly, especially if the sweep account is shared between accounts.
But it's still better than leaving Betterment completely untracked in your
financial software, I suppose.


## Requirements

Download pdfbox from here: https://pdfbox.apache.org/

The code expects pdfbox-app-2.0.19.jar, in the current directory when you run
statement\_parse.py, and doesn't currently make any effort to check for other
versions.

You'll also need python 3.7+

## Running

I prefer to use a python virtual env to run this code.

```
pyvenv .venv
pip install -r requirements.txt
./statement_parse.py statement.pdf > statement.ofx
```

You should be able to import this ofx into your financial software.

## Reporting issues

It's fairly likely at this point you may run into issues importing your
statements, since up to now I'm only able to test against examples I have from
my own account. It's fairly _unlikely_ that I can resolve these issues unless
you are willing to provide a sample PDF along with your bug report.

## A note to Betterment

Please add native OFX output. Use this code as a starting place if you like. You
don't have to pay Intuit for an OFX license, it's an open standard. But Quicken
can import OFX, and so can lots of other stuff.

If I can do it, so can you - it'll be a lot easier working directly from raw
data instead of parsing junk out of a PDF like this.

Overall I rather like Betterment, but frankly this omitted feature seems kind of
embarrassing. And so do some of the reporting quirks.

## References

This reference was invaluable in producing valid OFX: https://schemas.liquid-technologies.com/OFX/2.1.1/?page=seclistmsgsrsv11.html
