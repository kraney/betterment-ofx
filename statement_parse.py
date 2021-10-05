#!/usr/bin/env python
import re
import subprocess
from decimal import Decimal
from datetime import datetime, timedelta
import pytz
import xml.etree.ElementTree as ET
import hashlib
import sys

from ofxtools.models import *
from ofxtools.utils import UTC
from ofxtools.header import make_header


DATEFMT1 = '%b %d, %Y'
DATEFMT2 = '%b %d %Y'
DATE = '([A-Za-z]{3} [0-9]+,? [0-9]{4})'
MONEY = '(-?\$[0-9,.]+)'
PCT = '(-?[0-9.,]+%)'
SHARES = '(-?[0-9.,]+)'
ACCT = '([0-9]*)'
PAREN = '\(([^)]+)\)'
SYMBOL = '([A-Z]+)'
local = pytz.timezone("America/Los_Angeles")
dollar_trans = str.maketrans("$,", "$,", "$,")


def datefrom(datestr):
    try:
        naive = datetime.strptime(datestr, DATEFMT1)
    except ValueError:
        naive = datetime.strptime(datestr, DATEFMT2)
    local_dt = local.localize(naive, is_dst=None)
    return local_dt.astimezone(pytz.utc)


def hashfrom(instr):
    return hashlib.md5(instr.encode()).hexdigest()


class Account(object):
    """
    This is the base class for an account. The Betterment statement is broken up into sections by account.
    This class and its subclasses will attempt to wrap up the data from the statement and hide any PDF weirdness
    in how the data is represented.
    """

    def __init__(self):
        self.data = []
        self.__account_no = None

    def extend(self, data):
        self.data.extend(data)

    @property
    def external(self):
        for line in self.data[:20]:
            if line.endswith('(External)'):
                return True
        return False

    @property
    def account_no(self):
        if self.__account_no:
            return self.__account_no + "-" + hashfrom(self.name)[:6]
        for line in self.data:
            match = re.search('Account #{acct}'.format(acct=ACCT), line)
            if match:
                return match.group(1) + "-" + hashfrom(self.name)[:6]
        return hashfrom(self.name)[:6]

    @account_no.setter
    def account_no(self, no):
        self.__account_no = no

    @property
    def all_investing(self):
        if 'Taxable Investing Account' in self.data:
            return True
        return False

    @property
    def beginning_balance(self):
        for item in self.data:
            match = re.search('Beginning Balance {paren} {money}'.format(
                money=MONEY, paren=PAREN), item)
            if match:
                return datefrom(match.group(1)), match.group(2).translate(dollar_trans)

    @property
    def ending_balance(self):
        for item in self.data:
            match = re.search('Ending Balance {paren} {money}'.format(
                money=MONEY, paren=PAREN), item)
            if match:
                return datefrom(match.group(1)), match.group(2).translate(dollar_trans)


class CashReserve(Account):
    """
    This class provides a high-level interface to get the data for a Cash Reserve account from the Betterment statement
    """
    @property
    def name(self):
        return "Cash Reserve"

    @property
    def net_deposited(self):
        for item in self.data:
            match = re.search('Deposits {money}'.format(
                money=MONEY), item)
            if match:
                return match.group(1).translate(dollar_trans)

    @property
    def interest_paid(self):
        for item in self.data:
            match = re.search('Interest Paid {money}'.format(
                money=MONEY), item)
            if match:
                return match.group(1).translate(dollar_trans)

    def holdings(self):
        """
        Returns the contents of the Holdings table in parsed form
        """
        try:
            start = self.data.index('TOTAL HOLDINGS')
            end = self.data.index('TOTAL PROGRAM BANK DETAILS')
        except ValueError:
            return
        holdings = self.data[start+2:end-1]
        for i in range(0, len(holdings), 3):
            if holdings[i] == 'TOTAL HOLDINGS':
                # repeated title on page change
                i += 4
                continue
            bank = holdings[i]
            match = re.search('^{money} {percent} {money} {money}$'.format(
                money=MONEY, percent=PCT), holdings[i+2])
            if not match:
                raise Exception("Could not match against " + holdings[i+2])
            yield (bank, match.group(1),
                   match.group(2).translate(dollar_trans),
                   match.group(3).translate(dollar_trans),
                   match.group(4).translate(dollar_trans))

    def activity(self):
        """
        Returns the contents of the Activity table in parsed form
        """
        try:
            start = self.data.index('ACTIVITY')
            end = self.data.index('TOTAL HOLDINGS')
        except ValueError:
            return
        for item in self.data[start+2:end]:
            if item == 'ACTIVITY' or item == 'Date Description Amount':
                # repeated title on page change
                continue
            match = re.search(
                '^{date} (.*) {money}$'.format(date=DATE, money=MONEY), item)
            if not match:
                raise Exception("Could not match against " + item)
                continue
            if match.group(2) == 'Beginning Balance':
                continue
            if match.group(2) == 'Ending Balance':
                continue
            yield datefrom(match.group(1)), match.group(2), match.group(3).translate(dollar_trans)


class Investment(Account):
    """
    This class provides a high-level interface to read data from any investment
    account in the Betterment statement
    """
    @property
    def name(self):
        for line in self.data:
            match = re.search('^([A-Za-z ]+)(?: \(.*\))?$', line)
            if match:
                return match.group(1)
        return None

    @property
    def is_ira(self):
        return 'IRA' in self.name

    def find_account_no(self, allsection):
        """
        Betterment doesn't put the account number in the heading for certain accounts. Lift the number
        from the redundant (but incomplete) data in the summary
        """
        if allsection and self.name == 'General Investing':
            self.account_no = allsection.account_no.split('-')[0]

    def holdings(self):
        """
        Returns the contents of the Holdings table in parsed form
        """
        try:
            start = self.data.index(
                'Type Description Ticker Shares Value Shares Value Shares Value')
        except ValueError:
            return
        saved = None
        for item in self.data[start+1:]:
            if item.startswith('Total '):
                break
            if item == 'Description Fund Shares Value Shares Value Shares Value':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            match = re.search('^(.*) {symbol} {shares} {money} {shares} {money} {shares} {money}$'.format(
                symbol=SYMBOL, money=MONEY, shares=SHARES), item)
            if not match:
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved = item
                continue
            yield (match.group(1), match.group(2),
                   match.group(3).translate(dollar_trans),
                   match.group(4).translate(dollar_trans),
                   match.group(5).translate(dollar_trans),
                   match.group(6).translate(dollar_trans),
                   match.group(7).translate(dollar_trans),
                   match.group(8).translate(dollar_trans))

    def dividends(self):
        """
        Returns the contents of the Dividends table in parsed form
        """
        try:
            start = self.data.index('Payment Date Ticker Description Amount')
        except ValueError:
            return
        saved = None
        for item in self.data[start+1:]:
            if item.startswith('Total '):
                break
            if item == 'Payment Date Ticker Description Amount':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            match = re.search('^{date} {symbol} (.*) {money}$'.format(
                date=DATE, symbol=SYMBOL, money=MONEY), item)
            if not match:
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved = item
                continue
            yield (datefrom(match.group(1)),
                   match.group(2),
                   match.group(3),
                   match.group(4).translate(dollar_trans))

    def activity_detail(self):
        """
        Returns the contents of the Activity Detail table in parsed form
        """
        try:
            start = self.data.index(
                'Transaction3 Date4 Ticker Price Shares Value')
        except ValueError:
            return
        event = None
        saved = None
        saveddate = None
        for item in self.data[start+1:]:
            if item.startswith('Total '):
                break
            if item == 'Transaction3 Date4 Ticker Price Shares Value':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            # the event header from the following line sometimes gets tacked on to the end
            match = re.search('^(.* )?{date} {symbol} {money} {shares} {money}[A-Za-z ]*$'.format(
                date=DATE, symbol=SYMBOL, money=MONEY, shares=SHARES), item)
            if not match:
                match = re.search('^(.*)? {money}$'.format(money=MONEY), item)
                if match:
                    event = match.group(1).strip()
                    if event == 'Advisory Fee':
                        yield (event, saveddate, None, None, None, match.group(2).translate(dollar_trans))
                    continue
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved = item
                continue
            if match.group(1):
                event = match.group(1).strip()
            saveddate = datefrom(match.group(2))
            yield (event, datefrom(match.group(2)), match.group(3),
                   match.group(4).translate(dollar_trans),
                   match.group(5).translate(dollar_trans),
                   match.group(6).translate(dollar_trans))


class CashActivity(object):
    """
    This class represents the "Cash Activity" content of the Betterment statement. It includes
    both the sweep account data and the security account data
    """

    def __init__(self):
        self.data = []

    def extend(self, data):
        """
        Used to add text content to the source data for this account
        """
        self.data.extend(data)

    # Betterment logs a redundant "account". This flag is set to true for the redundant account
    all_investing = False

    @ property
    def taxable(self):
        """
        Returns true if this account is a taxable account
        """
        for line in self.data[:20]:
            if line.endswith('(TAXABLE)'):
                return True
            if line.endswith('(IRA)'):
                return False
        return False

    def sweep_account_activity(self):
        """
        Return the contents of the sweep account activity table in parsed form
        """
        try:
            start = self.data.index(
                'Date Goal Description Transaction Balance')
        except ValueError:
            return
        if not self.data[start-5].startswith('SWEEP '):
            return
        saved = None
        for item in self.data[start+1:]:
            if item.startswith('Balance '):
                break
            if item == 'Date Goal Description Transaction Balance':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            match = re.search('^{date} (.+) ((?:Fees)|(?:\w+ (?:of|to|from) .+)) {money} {money}$'.format(
                date=DATE, money=MONEY), item)
            if not match:
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved = item
                continue
            yield (datefrom(match.group(1)), match.group(2), match.group(3),
                   match.group(4).translate(dollar_trans),
                   match.group(5).translate(dollar_trans))

    def security_account_activity(self):
        """
        Return the contents of the activity table for the security account in parsed form
        """
        try:
            start = self.data.index(
                'Date Goal Description Transaction Balance')
            while not self.data[start-1].startswith('SECURITIES ACCOUNT '):
                start = start+1 + \
                    self.data[start +
                              1:].index('Date Goal Description Transaction Balance')
        except ValueError:
            return
        saved = None
        for item in self.data[start+1:]:
            if item.startswith('Balance '):
                break
            if item == 'Date Goal Description Transaction Balance':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            match = re.search('^{date} (.+) ((?:Fees)|(?:\w+ (?:of|to|from) .+)) {money} {money}$'.format(
                date=DATE, money=MONEY), item)
            if not match:
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved = item
                continue
            yield (datefrom(match.group(1)), match.group(2), match.group(3),
                   match.group(4).translate(dollar_trans),
                   match.group(5).translate(dollar_trans))


def breakdown_by_account(textlist):
    """
    Attempt to break up the content of a statement by account, identifying the type of account
    """
    accounts = []
    currpage = []
    allinvesting = None
    for item in textlist:
        if item.startswith('ACTIVITY'):
            accounts.append(CashReserve())
            currpage.append(item)
        elif item.startswith('HOLDINGS'):
            if accounts and accounts[-1].all_investing:
                allinvesting = accounts[-1]
            accounts.append(Investment())
            currpage.append(item)
        elif re.match('SWEEP[A-Z ]*CASH ACTIVITY', item):
            accounts.append(CashActivity())
            currpage.append(item)
        elif re.match('Page [0-9]+ of [0-9]+', item):
            # starting a new page
            if len(accounts) > 0:
                accounts[-1].extend(currpage)
            currpage = []
            if allinvesting and \
                    isinstance(accounts[-1], Investment) and \
                    not accounts[-1].all_investing:
                accounts[-1].find_account_no(allinvesting)
        else:
            currpage.append(item)
    if len(accounts) > 0:
        accounts[-1].extend(currpage)
    return accounts


def get_bankmsgsrs(accounts):
    """
    This gathers the data for bank-like accounts and translates it to OFX structures
    """
    # First see if there's a cash reserves account
    account = None
    for acc in accounts:
        if isinstance(acc, CashReserve):
            account = acc
            break
    if not account:
        return None
    before, _ = account.beginning_balance
    asof, balance = account.ending_balance
    ledgerbal = LEDGERBAL(balamt=Decimal(balance),
                          dtasof=asof)
    trns = []
    for trndate, desc, amt in account.activity():
        amt = float(amt)
        if amt < 0:
            trntype = 'DEBIT'
        elif desc == 'Interest Payment':
            trntype = 'INT'
        else:
            trntype = 'CREDIT'
        trns.append(STMTTRN(trntype=trntype,
                            dtposted=trndate,
                            trnamt=Decimal(amt),
                            fitid=hashfrom(str(trndate) + desc),
                            name=desc[:32],
                            memo=desc))
    banktranlist = BANKTRANLIST(*trns, dtstart=before,
                                dtend=asof)
    acctfrom = BANKACCTFROM(
        bankid='BTRMNT', acctid=account.account_no, accttype='SAVINGS')
    stmtrs = STMTRS(curdef='USD',
                    bankacctfrom=acctfrom,
                    ledgerbal=ledgerbal,
                    banktranlist=banktranlist)
    status = STATUS(code=0, severity='INFO')
    stmttrnrs = STMTTRNRS(trnuid=hashfrom(
        str(before) + str(asof)), status=status, stmtrs=stmtrs)
    return BANKMSGSRSV1(stmttrnrs)


def get_invstmttrnrs(account, cash_taxable, cash_ira):
    """
    This gathers the data for an investment account and translates it to OFX structures
    """
    asof, balance = account.ending_balance
    before, _ = account.beginning_balance

    pos = []
    for desc, symbol, _, _, _, _, shares, value in account.holdings():
        if float(shares) == 0.0:
            continue
        secid = SECID(uniqueid=symbol, uniqueidtype='TICKER')
        pos.append(POSSTOCK(invpos=INVPOS(
            secid=secid,
            heldinacct='OTHER',
            postype='LONG',
            units=shares,
            unitprice=float(value)/float(shares),
            mktval=value,
            dtpriceasof=asof,
            memo=desc,
        )))
    invposlist = INVPOSLIST(*pos)
    trans = []
    recorded_dividends = {}
    for trndate, symbol, desc, amt in account.dividends():
        invtran = INVTRAN(fitid=hashfrom(
            str(trndate) + desc + str(amt)), dttrade=trndate, memo=desc)
        secid = SECID(uniqueid=symbol, uniqueidtype='TICKER')
        trans.append(INCOME(invtran=invtran,
                            secid=secid,
                            incometype='DIV',
                            total=amt,
                            subacctsec='OTHER',
                            subacctfund='OTHER'))
        # There is a quirk where dividends on last days of the quarter show up
        # in the cash account but not the activity, and are instead reported in
        # activity the next quarter.
        recorded_dividends.setdefault(amt, []).append(trndate)

    for desc, trndate, symbol, price, chg_shares, chg_value in account.activity_detail():
        invtran = INVTRAN(fitid=hashfrom(
            str(trndate) + str(desc) + str(price) + str(chg_shares)), dttrade=trndate, memo=desc)
        # if desc=='Dividend Reinvestment':
        # "reinvest" is actually a tracked cash in / buy, not a special REINV
        # where dividends are paid in stock
        #    trans.append(REINVEST(invtran=invtran,
        #                  secid=secid,
        #                  incometype='DIV',
        #                  units=chg_shares,
        #                  unitprice=price,
        #                  total=chg_value,
        #                  subacctsec='OTHER'))
        #    continue
        if desc and desc.find('Advisory Fee') != -1:
            stmttrn = STMTTRN(trntype='FEE',
                              dtposted=trndate,
                              trnamt=float(chg_value),
                              fitid=hashfrom(
                                  str(trndate) + desc + str(chg_value)),
                              name='Betterment',
                              memo=desc)
            trans.append(INVBANKTRAN(stmttrn=stmttrn,
                                     subacctfund='CASH'))
            if symbol is None:
                continue
        secid = SECID(uniqueid=symbol, uniqueidtype='TICKER')
        # Betterment sometimes reports "-0.000" due to rounding. It's still a
        # sale.
        if float(chg_shares) >= 0 and not chg_shares.startswith("-"):
            invbuy = INVBUY(invtran=invtran,
                            secid=secid,
                            units=chg_shares,
                            unitprice=price,
                            total=chg_value,
                            subacctsec='OTHER',
                            subacctfund='OTHER')
            trans.append(BUYMF(invbuy=invbuy, buytype='BUY'))
            continue
        invsell = INVSELL(invtran=invtran,
                          secid=secid,
                          units=abs(float(chg_shares)),
                          unitprice=price,
                          total=abs(float(chg_value)),
                          subacctsec='OTHER',
                          subacctfund='OTHER')
        trans.append(SELLMF(invsell=invsell, selltype='SELL'))

    if account.is_ira:
        cash = cash_ira
    else:
        cash = cash_taxable
    cashbal = 0
    if cash:
        for trndate, goal, desc, trn, cashbal in cash.sweep_account_activity():
            if goal.upper() != account.name:
                continue
            action, _, name = desc.split(' ', 2)
            if name == 'Securities Account':
                continue
            trntype = {
                'Deposit': 'DEP',
                'Transfer': 'XFER',
                'Withdrawal': 'CASH',
            }.get(action, 'OTHER')
            stmttrn = STMTTRN(trntype=trntype,
                              dtposted=trndate,
                              trnamt=trn,
                              fitid=hashfrom(str(trndate) + desc + str(trn)),
                              name=name,
                              memo=desc)
            trans.append(INVBANKTRAN(stmttrn=stmttrn,
                                     subacctfund='CASH'))
        for trndate, goal, desc, trn, _ in cash.security_account_activity():
            if goal.upper() != account.name:
                continue
            if desc == 'Fees':
                trntype = 'FEE'
                name = 'Betterment'
            else:
                action, _, name = desc.split(' ', 2)
                if name == 'Sweep Account':
                    continue
                if action == 'Settlement':
                    continue
                try:
                    if action == 'Payment' and trn in recorded_dividends:
                        for possible in recorded_dividends[trn]:
                            # if we recorded a dividend for the same amount within 5
                            # days before the cash record, assume it's the same one
                            if trndate - timedelta(days=5) <= possible and trndate >= possible:
                                raise Exception("skip it")
                except Exception:
                    continue
                trntype = {
                    'Transfer': 'XFER',
                    'Payment': 'DIV',
                }.get(action, 'OTHER')
            stmttrn = STMTTRN(trntype=trntype,
                              dtposted=trndate,
                              trnamt=trn,
                              fitid=hashfrom(str(trndate) + desc + str(trn)),
                              name=desc)
            bt = INVBANKTRAN(stmttrn=stmttrn,
                             subacctfund='OTHER')
            trans.append(bt)
    tranlist = INVTRANLIST(*trans, dtstart=before, dtend=asof)
    acctfrom = INVACCTFROM(brokerid='Betterment', acctid=account.account_no)
    # cashbal actually represents an aggregated sweep account, either all taxable or
    # all ira accounts rolled together. But I don't see a way to represent that. Nor
    # do I see a way to automatically split what's reported into separate balances
    invbal = INVBAL(availcash=cashbal, marginbalance=0, shortbalance=0)
    invstmtrs = INVSTMTRS(dtasof=asof,
                          curdef='USD',
                          invacctfrom=acctfrom,
                          invtranlist=tranlist,
                          invbal=invbal,
                          invposlist=invposlist)
    status = STATUS(code=0, severity='INFO')
    return INVSTMTTRNRS(trnuid=hashfrom(str(asof) + str(account.account_no)),
                        status=status,
                        invstmtrs=invstmtrs)


def get_investmsgsrs(accounts):
    """
    This generates OFX structures for all of the investment accounts
    """
    # first, find the cash transactions
    cash_taxable = None
    cash_ira = None
    for account in accounts:
        if not isinstance(account, CashActivity):
            continue
        if account.taxable:
            cash_taxable = account
        else:
            cash_ira = account
    st = []
    for account in accounts:
        if not isinstance(account, Investment):
            continue
        if account.all_investing:
            continue
        if account.external:
            continue
        st.append(get_invstmttrnrs(account, cash_taxable, cash_ira))
    return INVSTMTMSGSRSV1(*st)


def get_seclistmsgsrs(accounts):
    """
    This generates OFX structures for any securities / tickers mentioned in the statement
    """
    symbols = {}
    for account in accounts:
        if not isinstance(account, Investment):
            continue
        for name, symbol, _, _, _, _, _, _ in account.holdings():
            symbols.setdefault(symbol, name)
        # it's possible there's a security we no longer hold that's
        # mentioned from earlier transactions
        for _, symbol, name, _ in account.dividends():
            symbols.setdefault(symbol, name)
        for _, _, symbol, _, _, _ in account.activity_detail():
            if symbol:
                symbols.setdefault(symbol, symbol)
    stockinfo = []
    for symbol, name in symbols.items():
        secid = SECID(uniqueid=symbol, uniqueidtype='TICKER')
        secinfo = SECINFO(secid=secid, secname=name, ticker=symbol)
        stockinfo.append(STOCKINFO(secinfo=secinfo))
    return SECLISTMSGSRSV1(SECLIST(*stockinfo))


def get_ofx(accounts):
    """
    This generates the required sections for an OFX document by querying content
    from the high-level account objects
    """
    bankmsgsrs = get_bankmsgsrs(accounts)
    investmsgsrs = get_investmsgsrs(accounts)
    seclist = get_seclistmsgsrs(accounts)
    asof, _ = accounts[0].ending_balance

    fi = FI(org='Betterment', fid='9999')
    status = STATUS(code=0, severity='INFO')
    sonrs = SONRS(status=status,
                  dtserver=asof,
                  language='ENG', fi=fi)
    signonmsgs = SIGNONMSGSRSV1(sonrs=sonrs)
    return OFX(signonmsgsrsv1=signonmsgs,
               bankmsgsrsv1=bankmsgsrs,
               invstmtmsgsrsv1=investmsgsrs,
               seclistmsgsrsv1=seclist)


def load_file(filename):
    """
    Extract the text from the Betterment PDF and break it into a list of lines
    """
    p = subprocess.run([
        "java", "-jar", "pdfbox-app-2.0.19.jar", "ExtractText", "-console", filename
    ], capture_output=True)
    text = p.stdout.decode()

    return text.split('\n')


if __name__ == '__main__':
    filename = sys.argv[1]

    account = breakdown_by_account(load_file(filename))
    for a in account:
        if isinstance(a, CashActivity) or not a.account_no:
            continue
        sys.stderr.write(a.account_no)
        sys.stderr.write(" => ")
        sys.stderr.write(a.name)
        sys.stderr.write("\n")

    ofx = get_ofx(account)

    root = ofx.to_etree()
    header = str(make_header(version=220))
    message = ET.tostring(root).decode()

    print(header)
    print(message)
