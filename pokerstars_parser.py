import re
import copy
import os
import numpy as np
import pandas as pd

#### EXCEPTIONS ####

class HandParseException(Exception):
    pass

####

def find_files(directory, pattern, results=None):
    # print(directory)
    if results is None:
        results = []
    entries = os.listdir(directory)
    for x in entries:
        fullpath = os.path.join(directory, x)
        if os.path.isdir(fullpath):
            find_files(fullpath, pattern, results)
        elif re.fullmatch(pattern, x):
            results.append(fullpath)
    return results       

def parse_directory(directory, verbosity=1):
    hhfiles = find_files(directory, '.*[.]txt')
    res = []
    handcount = 0
    errcounts = {}
    def prt(s='', lvl=1, end='\n'):
        if lvl <= verbosity:
            print(s, end=end)
    for i, fn in enumerate(hhfiles):
        #print(fn)
        prt("{}/{}".format(i + 1, len(hhfiles)), end='')
        newres, errors = parse_hhfile(fn)
        handcount += len(newres)
        prt(": {:,}".format(handcount), end='')
        if errors:
            prt(" ({} errors)".format(len(errors)))
            _, errs = zip(*errors)
            for err in errs:
                errmsg = err.args[0]
                if not errmsg in errcounts:
                    errcounts[errmsg] = 0
                errcounts[errmsg] += 1
        else:
            prt()
        res += newres
    return res, errcounts

def parse_hhfile(fn):
    errors = []
    with open(fn) as f:
        txt = f.read()
    s = txt
    res = []
    for i in range(1000000):
        m = re.search('PokerStars Hand #[0-9]+: .*\n', s)
        if not m:
            break  # EOF
        s2 = s[m.end():]
        next_m = re.search('PokerStars Hand #[0-9]+: .*\n', s2)
        if next_m:
            endidx = next_m.start() + (m.end() - m.start())
        else:
            endidx = len(s)
        hand = s[m.start():endidx]
        try:
            parsed = parse_hand(hand)
        except HandParseException as err:
            errors.append((i, err))
        s = s[endidx:]
        res.append(parsed)
    return res, errors

def parse_header(s):
    d = {}
    d['hand_no'] = int(s[s.find('#')+1:s.find(':')])
    #print(d['hand_no'])
    d['game'] = s[s.find(':')+1:s.find('(')].strip()
    m = re.search('[(].[0-9.]+/.[0-9.]+ [A-Z]{3}[)]', s)
    stakestr = s[m.start():m.end()]
    d['sb'] = float(stakestr[2:stakestr.find('/')])
    d['bb'] = float(stakestr[stakestr.find('/')+2:stakestr.find(' ')])
    d['currency'] = stakestr[-4:-1]
    d['timestamp'] = pd.Timestamp(s[s.find('-')+1:], tz='US/Eastern')
    return d

def parse_street(s, pot_now, baseline=None, antes=None):
    if baseline:
        minv = copy.deepcopy(baseline)
        if antes:
            for name in minv:
                minv[name] -= antes[name]
        himark = max(minv.values())
    else:
        minv = {}
        himark = 0
    lines = s.splitlines()
    sel_lines = []
    for line in lines:
        if line.startswith('Dealt to'):
            continue
        if "removed from the table" in line:
            continue
        #if line.startswith("Uncalled bet"):
        #    continue
        if re.fullmatch('.* collected .[0-9.]+ from pot', line):
            continue
        if "doesn't show hand" in line:
            continue
        if line.endswith("has timed out"):
            continue
        if line.endswith('has timed out while disconnected'):
            continue
        if line.endswith('is disconnected '):
            continue
        if line.endswith('is connected '):
            continue
        if line.endswith('leaves the table'):
            continue
        if re.fullmatch('.+ joins the table at seat #[0-9][ ]*', line):
            continue
        if re.fullmatch('.+ said, ".*"', line):
            continue
        sel_lines.append(line)
    lines = sel_lines
    header = lines[0]
    lines = lines[1:]
    streetstr = re.findall('[*]{3} [A-Z ]+ [*]{3}', header)[0][4:-4]
    if streetstr == 'HOLE CARDS':
        street = 'preflop'
        cards = []
    else:
        assert ' ' not in streetstr
        street = streetstr.lower()
        cards = re.findall('[[][A-Z a-z0-9]+[]]', header)[-1][1:-1].split(' ')
    actions = []
    uncalled_bet = None
    for line in lines:
        if line.startswith('Uncalled bet'):
            amt = float(re.findall('[(].[0-9.]+[)]', line)[0][2:-1])
            name = line[line.find('returned to ')+12:]
            uncalled_bet = (amt, name)
            break
        name = line[:line.find(':')]
        action = re.findall(': [a-z]+', line)[0][2:-1]
        m = re.search('(raises|calls|bets) .[0-9.]+', line)  # we take the first number
        amt = None
        if m:
            if name not in minv:
                minv[name] = 0
            amtstr = line[m.start():m.end()]
            amt = float(amtstr.split(' ')[-1][1:])
            to_call = himark - minv[name]
            if action == 'bet':
                himark = amt
                pot_now += amt
                minv[name] += amt
            elif action == 'raise':
                pot_now += amt + to_call
                strend = line[m.end():]
                m = re.search('.[0-9.]+', strend)
                amtstr = strend[m.start():m.end()]
                himark = float(amtstr.split(' ')[-1][1:])
                minv[name] = himark
            elif action == 'call':
                pot_now += amt
                minv[name] += amt
            else:
                raise Exception("Unexpected investment action: {}".format(action))
        actions.append((name, action, amt, pot_now, himark))
    if baseline:
        for name in minv:
            minv[name] += antes[name]
    return actions, cards, uncalled_bet, minv

# OBSOLETE
def calc_minv(actions, baseline=None, ante=0):
    assert ante >= 0
    names,_,_,_,_ = zip(*actions)
    ante = 0
    if baseline:
        minv = copy.deepcopy(baseline)
        if ante > 0:
            for name in minv:
                minv[name] -= ante
    else:
        minv = {x: 0 for x in names}
    #prevsize = max(minv.values()) - min(minv.values())  # big blind - ante
    for t in actions:
        name = t[0]
        action = t[1]
        amt = t[2]
        himark = t[4]
        if amt:
            if action == 'raise':
                minv[name] += amt - himark - minv[name] - ante
            elif action == 'bet':
                minv[name] += amt
            elif action == 'call':
                minv[name] += amt
            else:
                raise Exception("Unexpected investment action: {}".format(action))
    return minv

def parse_hand(s):
    if "*** FIRST SHOW DOWN ***" in s:
        raise HandParseException("Run-it-twice parsing is not supported yet")
    if "*** SUMMARY ***" not in s:
        raise HandParseException("Incomplete hand history")
    #nicksyms = '[a-zA-Z0-9_.öÖäÄ ]'  # too many to list really ...
    lines = s.splitlines()
    header = lines[0]
    s = "\n".join(lines[1:])
    d = parse_header(header)
    #uncalled_bet_lines = [x for x in lines if x.startswith('Uncalled bet')]
    #uncalled_bets = []
    #for line in uncalled_bet_lines:
    #    uncalled_bets.append(float(re.findall('[(].[0-9.]+[)]', line)[0][2:-1]))
    #assert len(uncalled_bets) < 2
    #d['uncalled_bets'] = sum(uncalled_bets)
    table_line = s[:s.find('\n')]
    d['table_name'] = re.findall("'.*'", table_line)[0][1:-1]
    seatdefs = re.findall('Seat [0-9]: .*[(].[0-9.]+ in chips[)]', s)
    sd_dict = {}
    for sd in seatdefs:
        seat_no = int(sd[sd.find(' ')+1])
        nick = re.findall(': .* [(]', sd)[0][1:-1].strip()
        sd_dict[seat_no] = nick
        # assert re.fullmatch(nicksyms + '+', nick)  # just making sure i got the naming convention right
    d['sd_dict'] = sd_dict
    relpos_dict = {}
    btn_seat = int(s[s.find('#')+1])
    relpos_dict[sd_dict[btn_seat]] = 'BTN'
    summarylines = lines[lines.index('*** SUMMARY ***')+1:]
    for line in summarylines:
        if '(small blind)' in line:
            name = re.findall('Seat [0-9]: [^(]+ [(]', line)[0][8:-2]
            relpos_dict[name] = 'SB'
        elif '(big blind)' in line:
            name = re.findall('Seat [0-9]: [^(]+ [(]', line)[0][8:-2]
            relpos_dict[name] = 'BB'
    cur_suffix = 1
    for i in range(btn_seat - 2, -10, -1):
        seat_no = (i % 9) + 1
        if seat_no in sd_dict:
            nick = sd_dict[seat_no]
            if nick in relpos_dict:
                break
            relpos_dict[nick] = 'BTN+' + str(cur_suffix)
            cur_suffix += 1
    d['relpos_dict'] = relpos_dict
    ps_dict = {name: 0 for name in sd_dict.values()}
    posts = re.findall('.*: posts [a-z &]+.[0-9.]+\n', s)
    posts = [x[:-1] for x in posts]  # remove newline
    antes = {}
    extra_antes = {name: 0 for name in sd_dict.values()}
    for ps in posts:
        nick = ps[:ps.find(':')]
        #if 'big blind' in ps:
        #    relpos_dict[nick] = 'BB'
        #elif 'small blind' in ps:
        #    relpos_dict[nick] = 'SB'
        amount = float(ps.split(' ')[-1][1:])
        ps_dict[nick] += amount
        if re.fullmatch('.*: posts the ante .[0-9.]+', ps):
            antes[nick] = amount
        if d['relpos_dict'][nick] not in ['SB', 'BB']:
            if 'posts small & big blinds' in ps or 'posts small blind' in ps:
                extra_antes[nick] = d['sb']
    ante = 0
    if antes:
        assert len(antes) == len(ps_dict)
        assert len(set(antes.values())) == 1
        ante = list(antes.values())[0]
    d['ante'] = ante
    d['post_dict'] = ps_dict
    #extra_antes = {}
    #for name, val in ps_dict.items():
    #    if d['relpos'][name] == 'BB':
    #        extra = max(0, val - d['bb'] - d['ante'])
    #        if extra > 0.0001:
    #            raise Exception("Shouldn't happen")
    #    elif d['relpos'][name] == 'SB':
    #        extra = max(0, val - d['sb'] - d['ante'])
    #        if extra > 0.0001:
    #            raise Exception("Shouldn't happen")
    ##    else:
    #        extra = max(0, val - d['bb'] - d['ante'])
    #    extra_antes[name] = extra  # capture if someone posts both small and big blind
    #for post in ps_dict:
    #    if 
    d['extra_antes'] = extra_antes
    implied_antes = (pd.Series(antes) + pd.Series(extra_antes)).to_dict()
    assert len(relpos_dict) == len(sd_dict) == len(ps_dict)
    m = re.search('Dealt to .* [[].. ..[]]', s)
    if not m:
        d['hero'] = None
        d['holecards'] = None
    else:
        heroline = s[m.start():m.end()]
        d['hero'] = heroline[9:heroline.find('[')].strip()
        holecards = heroline[heroline.find('[')+1:heroline.find(']')]
        d['holecards'] = holecards.split(' ')
    potrake = re.findall('Total pot .+ [|] Rake .[0-9.]+', s)[0]
    spl = potrake.split('|')
    rake = float(re.findall('[0-9.]+', spl[1])[0])
    spl = re.findall('[0-9.]+', spl[0])
    totalpot = float(spl[0])
    pots = [float(x[:-1]) for x in spl[1:]]
    d['totalpot'] = totalpot
    d['pots'] = pots  # only when there are side pots
    d['rake'] = rake
    #ed = {name: 0 for name in sd_dict.values()}
    #minv = {'preflop': copy.deepcopy(ed), 'flop': copy.deepcopy(ed),
    #        'turn': copy.deepcopy(ed), 'river': copy.deepcopy(ed)}
    def get_street(start, end):
        sloc = s.find(start)
        eloc = s.find(end)
        last_street = False
        if eloc < 0:
            eloc = s.find('*** SUMMARY ***')
            last_street = True
        return s[sloc:eloc], last_street
    streetparams = [
        ('preflop', ('*** HOLE CARDS ***', '*** FLOP ***')),
        ('flop', ('*** FLOP ***', '*** TURN ***')),
        ('turn', ('*** TURN ***', '*** RIVER ***')),
        ('river', ('*** RIVER ***', '*** SHOW DOWN ***'))
    ]
    minv = {}
    act_dict = {}
    comcards = []
    d['last_street'] = 'showdown'
    pot_now = sum(ps_dict.values())
    uncalled_bet = None
    for t in streetparams:
        ss, last_street = get_street(*t[1])
        baseline = None
        street = t[0]
        if street == 'preflop':
            baseline = ps_dict
        himark = 0
        if baseline:
            himark = max(baseline.values()) - min(baseline.values())  # bb - ante
        actions, cards, ucb, mistr = parse_street(ss, pot_now, baseline, implied_antes)
        if not uncalled_bet:
            uncalled_bet = ucb  # in case hand is all-in before river
        minv[street] = mistr
        if actions:
            pot_now = actions[-1][3]
        #minv[street] = calc_minv(actions, baseline, ante)
        act_dict[street] = actions
        comcards += cards
        if last_street:
            d['last_street'] = street
            break
    if uncalled_bet:
        streets, _ = zip(*streetparams)
        for street in streets[::-1]:
            if street in minv:
                name = uncalled_bet[1]
                if name in minv[street]:
                    minv[street][uncalled_bet[1]] -= uncalled_bet[0]
                    break
                #for t in act_dict[street][::-1]:
                #    action = t[1]
                #    if action == 'raise':
                #        name = t[0]
                #        to_call = t[5]
                #        assert name == uncalled_bet[1]
                #        prev_inv = minv[street][name] - to_call
                #        # this amount will be added to pot for rake considerations
                #        minv[street][name] -= prev_inv
                #        break
                #lastact = act_dict[street][-1]
                #assert uncalled_bet[1] 
    minvtot = {name: 0 for name in sd_dict.values()}
    for name in sd_dict.values():
        for ms in minv.values():
            if name in ms:
                minvtot[name] += ms[name]
    minv['total'] = minvtot
    d['minv'] = minv
    d['act_dict'] = act_dict
    d['com_cards'] = comcards
    d['uncalled_bet'] = uncalled_bet
    winners = []
    for line in summarylines:
        if re.fullmatch('Seat [0-9]: .* collected [(].[0-9.]+[)]', line):
            amt = float(re.findall('[(].[0-9.]+[)]', line)[0][2:-1])
            name = re.findall('Seat [0-9]: [^(]+ [(]', line)[0][8:-1]
            if 'collected' in name:
                name = name[:-10]
            else:
                name = name[:-1]
            name = re.sub('[(].*[)]', '', name).strip()
            winners.append((name, amt))
        if re.fullmatch("Seat [0-9]: .+ showed [[].+[]] and won [(].[0-9.]+[)] with .*", line):
            amt = float(re.findall('[(].[0-9.]+[)]', line)[0][2:-1])
            name = re.findall('Seat [0-9]: .+ showed', line)[0][8:-7]
            name = re.sub('[(].*[)]', '', name).strip()
            winners.append((name, amt))
    d['winners'] = winners
    d['totalpot_no_rake'] = d['totalpot'] - d['rake']
    names, amts = zip(*winners)
    assert np.isclose(d['totalpot_no_rake'], sum(amts))
    rakecontrib = {}
    for t in winners:
        name = t[0]
        amt = t[1]
        rakecontrib[name] = (amt / d['totalpot_no_rake']) * d['rake']
    d['rake_contrib'] = rakecontrib
    if not uncalled_bet:
        ucb = 0
    else:
        ucb = uncalled_bet[0]
    #return d
    assert np.isclose(d['totalpot'], sum(minvtot.values()))
    return d
