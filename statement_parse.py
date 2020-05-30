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


DATEFMT1='%b %d, %Y'
DATEFMT2='%b %d %Y'
DATE='([A-Za-z]{3} [0-9]+,? [0-9]{4})'
MONEY='(-?\$[0-9,.]+)'
PCT='(-?[0-9.,]+%)'
SHARES='(-?[0-9.,]+)'
ACCT='([0-9]*)'
PAREN='\(([^)]+)\)'
SYMBOL='([A-Z]+)'
local = pytz.timezone("America/Los_Angeles")
dollar_trans = str.maketrans("$,","$,","$,")

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
    def __init__(self):
        self.data=[]
        self.__account_no=None
    def extend(self, data):
        self.data.extend(data)

    @property
    def external(self):
        for line in self.data[:20]:
            if line.endswith('(EXTERNAL)'): return True
        return False

    @property
    def account_no(self):
        if self.__account_no:
            return self.__account_no
        for line in self.data:
            match = re.search('ACCT # {acct}'.format(acct=ACCT), line)
            if match:
                return match.group(1)
    @account_no.setter
    def account_no(self, no):
        self.__account_no = no

    @property
    def all_investing(self):
        if 'ALL INVESTING ACCOUNTS' in self.data:
            return True
        if 'ALL ACCOUNTS' in self.data:
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

    @property
    def net_change(self):
        for item in self.data:
            match = re.search('Net Change {money}'.format(
                money=MONEY), item)
            if match:
                return match.group(1).translate(dollar_trans)


class CashReserve(Account):
    @property
    def net_deposited(self):
        for item in self.data:
            match = re.search('Net Deposited {money}'.format(
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
        try:
            start = self.data.index('Holdings')
            end = self.data.index('Program Bank Details')
        except ValueError:
            return
        holdings = self.data[start+6:end-1]
        for i in range(0, len(holdings), 2):
            if holdings[i] == 'Holdings':
                # repeated title on page change
                i += 4
                continue
            bank = holdings[i]
            match = re.search('^(?:.*\*\*\*) {money} {percent} {money} {money}$'.format(
                money=MONEY, percent=PCT), holdings[i+1])
            if not match:
                raise Exception("Could not match against " + holdings[i+1])
            yield (bank, match.group(1),
                match.group(2).translate(dollar_trans),
                match.group(3).translate(dollar_trans),
                match.group(4).translate(dollar_trans))

    def activity(self):
        try:
            start = self.data.index('Activity')
            end = self.data.index('Holdings')
        except ValueError:
            return
        for item in self.data[start+2:end]:
            if item == 'Activity' or item == 'Date Description Amount':
                # repeated title on page change
                continue
            match = re.search('^{date} (.*) {money}$'.format(date=DATE, money=MONEY), item)
            if not match:
                raise Exception("Could not match against " + item)
                continue
            yield datefrom(match.group(1)), match.group(2), match.group(3).translate(dollar_trans)


class Investment(Account):
    @property
    def total_invested(self):
        for item in self.data:
            match = re.search('Total Invested {money}'.format(money=MONEY), item)
            if match:
                return match.group(1).translate(dollar_trans)

    @property
    def total_earned(self):
        for item in self.data:
            match = re.search('Total Earned {money}'.format(money=MONEY), item)
            if match:
                return match.group(1).translate(dollar_trans)

    @property
    def name(self):
        for line in self.data:
            match = re.search('^([A-Z ]+)(?: \(.*\))?$', line)
            if match:
                return match.group(1)
        return None

    @property
    def is_ira(self):
        return 'IRA' in self.name

    @property
    def subaccount(self):
        ret={}
        account=None
        for line in self.data:
            match = re.search('^(.*) \(Acct # {acct}\)'.format(acct=ACCT), line)
            if match:
                account = match.group(2)
            elif account:
                match = re.search('^([^(]*)( \(.*\))?$', line)
                if match:
                    ret[match.group(1).upper()] = account
        return ret

    def find_account_no(self, allsection):
        if allsection:
            self.account_no = allsection.subaccount[self.name]

    def holdings(self):
        try:
            start = self.data.index('Description Fund Shares Value Shares Value Shares Value')
        except ValueError:
            return
        saved=None
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
                saved=item
                continue
            yield (match.group(1), match.group(2),
                match.group(3).translate(dollar_trans),
                match.group(4).translate(dollar_trans),
                match.group(5).translate(dollar_trans),
                match.group(6).translate(dollar_trans),
                match.group(7).translate(dollar_trans),
                match.group(8).translate(dollar_trans))

    def dividends(self):
        try:
            start = self.data.index('Payment Date Fund Description Amount')
        except ValueError:
            return
        saved=None
        for item in self.data[start+1:]:
            if item.startswith('Total '):
                break
            if item == 'Payment Date Fund Description Amount':
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
                saved=item
                continue
            yield (datefrom(match.group(1)),
                match.group(2),
                match.group(3),
                match.group(4).translate(dollar_trans))

    def activity_detail(self):
        try:
            start = self.data.index('Transaction 2 Date 3 Fund Price Shares Value Shares Value')
        except ValueError:
            return
        event=None
        saved=None
        for item in self.data[start+1:]:
            if item.startswith('Total '):
                break
            if item == 'Transaction 2 Date 3 Fund Price Shares Value Shares Value':
                # repeated title on new page
                saved = None
                continue
            if saved:
                item = saved + " " + item
                saved = None
            match = re.search('^(.* )?{date} {symbol} {money} {shares} {money} {shares} {money}$'.format(
                date=DATE, symbol=SYMBOL, money=MONEY, shares=SHARES), item)
            if not match:
                # long names get wrapped and end up as a tiny line followed by
                # full line
                saved=item
                continue
            if match.group(1):
                event = match.group(1).strip()
            yield (event, datefrom(match.group(2)), match.group(3),
                match.group(4).translate(dollar_trans),
                match.group(5).translate(dollar_trans),
                match.group(6).translate(dollar_trans),
                match.group(7).translate(dollar_trans),
                match.group(8).translate(dollar_trans))



class CashActivity(object):
    def __init__(self):
        self.data=[]
    def extend(self, data):
        self.data.extend(data)
    all_investing=False

    @property
    def taxable(self):
        for line in self.data[:20]:
            if line.endswith('(TAXABLE)'): return True
            if line.endswith('(IRA)'): return False
        return False

    def sweep_account_activity(self):
        try:
            start = self.data.index('Date Goal Description Transaction Balance')
        except ValueError:
            return
        if not self.data[start-1].startswith('Sweep Account '):
            return
        saved=None
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
                saved=item
                continue
            yield (datefrom(match.group(1)), match.group(2), match.group(3),
                match.group(4).translate(dollar_trans),
                match.group(5).translate(dollar_trans))
        
    def security_account_activity(self):
        try:
            start = self.data.index('Date Goal Description Transaction Balance')
            while not self.data[start-1].startswith('Securities Account '):
                start = start+1+self.data[start+1:].index('Date Goal Description Transaction Balance')
        except ValueError:
            return
        saved=None
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
                saved=item
                continue
            yield (datefrom(match.group(1)), match.group(2), match.group(3),
                match.group(4).translate(dollar_trans),
                match.group(5).translate(dollar_trans))
        

def breakdown_by_account(textlist):
    accounts=[]
    currpage=[]
    allinvesting=None
    for item in textlist:
        if item.startswith('Net Deposited'):
            accounts.append(CashReserve())
            currpage.append(item)
        elif item.startswith('Total Invested'):
            if accounts and accounts[-1].all_investing:
                allinvesting=accounts[-1]
            accounts.append(Investment())
            currpage.append(item)
        elif item.startswith('CASH ACTIVITY '):
            accounts.append(CashActivity())
            currpage.append(item)
        elif re.match('Page [0-9]+ of [0-9]+', item):
            # starting a new page
            if len(accounts) > 0:
                accounts[-1].extend(currpage)
            currpage=[]
            if allinvesting and \
                    isinstance(accounts[-1], Investment) and \
                    not accounts[-1].all_investing and \
                    not accounts[-1].account_no:
                accounts[-1].find_account_no(allinvesting)
        else:
            currpage.append(item)
    if len(accounts) > 0:
        accounts[-1].extend(currpage)
    return accounts


def get_bankmsgsrs(accounts):
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
    trns=[]
    for trndate, desc, amt in account.activity():
        amt = float(amt)
        if amt < 0:
            trntype='DEBIT'
        elif desc == 'Interest Payment':
            trntype='INT'
        else:
            trntype='CREDIT'
        trns.append(STMTTRN(trntype=trntype,
                            dtposted=trndate,
                            trnamt=Decimal(amt),
                            fitid=hashfrom(str(trndate) + desc),
                            name=desc[:32],
                            memo=desc))
    banktranlist = BANKTRANLIST(*trns, dtstart=before,
                                dtend=asof)
    acctfrom = BANKACCTFROM(bankid='BTRMNT', acctid=account.account_no, accttype='SAVINGS')
    stmtrs = STMTRS(curdef='USD',
                    bankacctfrom=acctfrom,
                    ledgerbal=ledgerbal,
                    banktranlist=banktranlist)
    status = STATUS(code=0, severity='INFO')
    stmttrnrs = STMTTRNRS(trnuid=hashfrom(str(before) + str(asof)), status=status, stmtrs=stmtrs)
    return BANKMSGSRSV1(stmttrnrs)

def get_invstmttrnrs(account, cash_taxable, cash_ira):
    asof, balance = account.ending_balance
    before, _ = account.beginning_balance

    pos=[]
    for desc, symbol, _, _, _, _, shares, value in account.holdings():
        if float(shares) == 0.0:
            continue
        secid=SECID(uniqueid=symbol, uniqueidtype='TICKER')
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
    invposlist=INVPOSLIST(*pos)
    trans=[]
    recorded_dividends={}
    for trndate, symbol, desc, amt in account.dividends():
        invtran=INVTRAN(fitid=hashfrom(str(trndate) + str(amt)), dttrade=trndate, memo=desc)
        secid=SECID(uniqueid=symbol, uniqueidtype='TICKER')
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

    for desc, trndate, symbol, price, chg_shares, chg_value, _, _ in account.activity_detail():
        invtran=INVTRAN(fitid=hashfrom(str(trndate) + str(desc) + str(chg_shares)), dttrade=trndate, memo=desc)
        secid=SECID(uniqueid=symbol, uniqueidtype='TICKER')
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
        if desc=='Advisory Fee':
            stmttrn=STMTTRN(trntype='FEE',
                            dtposted=trndate,
                            trnamt=float(chg_value),
                            fitid=hashfrom(str(trndate) + desc + str(chg_value)),
                            name='Betterment',
                            memo=desc)
            trans.append(INVBANKTRAN(stmttrn=stmttrn,
                                     subacctfund='CASH'))
        if float(chg_shares) >= 0:
            invbuy=INVBUY(invtran=invtran,
                          secid=secid,
                          units=chg_shares,
                          unitprice=price,
                          total=chg_value,
                          subacctsec='OTHER',
                          subacctfund='OTHER')
            trans.append(BUYMF(invbuy=invbuy, buytype='BUY'))
            continue
        invsell=INVSELL(invtran=invtran,
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
            trntype={
                'Deposit': 'DEP',
                'Transfer': 'XFER',
                'Withdrawal': 'CASH',
            }.get(action, 'OTHER')
            stmttrn=STMTTRN(trntype=trntype,
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
                trntype='FEE'
                name='Betterment'
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
                trntype={
                    'Transfer': 'XFER',
                    'Payment': 'DIV',
                }.get(action, 'OTHER')
            stmttrn=STMTTRN(trntype=trntype,
                            dtposted=trndate,
                            trnamt=trn,
                            fitid=hashfrom(str(trndate) + desc + str(trn)),
                            name=desc)
            bt = INVBANKTRAN(stmttrn=stmttrn,
                             subacctfund='OTHER')
            trans.append(bt)
    tranlist=INVTRANLIST(*trans, dtstart=before, dtend=asof)
    acctfrom= INVACCTFROM(brokerid='Betterment', acctid=account.account_no)
    # cashbal actually represents an aggregated sweep account, either all taxable or
    # all ira accounts rolled together. But I don't see a way to represent that. Nor
    # do I see a way to automatically split what's reported into separate balances
    invbal=INVBAL(availcash=cashbal, marginbalance=0, shortbalance=0)
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
    # first, find the cash transactions
    cash_taxable = None
    cash_ira = None
    for account in accounts:
        if not isinstance(account, CashActivity):
            continue
        if account.taxable:
            cash_taxable = account
        else:
            case_ira = account
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
    symbols={}
    for account in accounts:
        if not isinstance(account, Investment):
            continue
        for name, symbol, _, _, _, _, _, _ in account.holdings():
            symbols.setdefault(symbol, name)
        # it's possible there's a security we no longer hold that's
        # mentioned from earlier transactions
        for _, symbol, name, _ in account.dividends():
            symbols.setdefault(symbol, name)
        for _, _, symbol, _, _, _, _, _ in account.activity_detail():
            symbols.setdefault(symbol, symbol)
    stockinfo=[]
    for symbol, name in symbols.items():
        secid = SECID(uniqueid=symbol, uniqueidtype='TICKER')
        secinfo = SECINFO(secid=secid, secname=name, ticker=symbol)
        stockinfo.append(STOCKINFO(secinfo=secinfo))
    return SECLISTMSGSRSV1(SECLIST(*stockinfo))

def get_ofx(accounts):
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

filename = sys.argv[1]
p = subprocess.run([
        "java", "-jar", "pdfbox-app-2.0.19.jar", "ExtractText", "-console", filename
    ], capture_output=True)
text = p.stdout.decode()

tl = text.split('\n')
account = breakdown_by_account(tl)

ofx = get_ofx(account)

root = ofx.to_etree()
header = str(make_header(version=220))
message = ET.tostring(root).decode()

print(header)
print(message)
