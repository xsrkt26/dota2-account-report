#!/usr/bin/env python3
"""Read-only public OpenDota capture, descriptive analysis and text evidence export."""
import argparse
import collections
import datetime as dt
import email.utils
import hashlib
import itertools
import json
import math
import pathlib
import statistics as st
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://api.opendota.com/api/'
STEAM_BASE = 76561197960265728
MAX_ACCOUNT = 2**32 - 1
PROJECT = ('gold_per_min', 'xp_per_min', 'hero_damage', 'tower_damage',
           'last_hits', 'hero_healing', 'cluster')
CORE = ('match_id', 'hero_id', 'player_slot', 'start_time', 'duration', 'lobby_type', 'radiant_win')


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def account_id(value):
    value = str(value).strip()
    if '://' in value:
        url = urllib.parse.urlparse(value)
        parts = url.path.strip('/').split('/')
        if url.hostname not in ('steamcommunity.com', 'www.steamcommunity.com') or len(parts) != 2 or parts[0] != 'profiles':
            raise ValueError('Resolve vanity URLs or match-only inputs to a verified account ID first.')
        value = parts[1]
    if not value.isascii() or not value.isdecimal():
        raise ValueError('Expected a numeric Account ID, SteamID64, or numeric Steam profile URL.')
    number = int(value)
    if STEAM_BASE < number <= STEAM_BASE + MAX_ACCOUNT:
        number -= STEAM_BASE
    if not 0 < number <= MAX_ACCOUNT:
        raise ValueError('Not a valid public individual Account ID / SteamID64.')
    return number


class Client:
    def __init__(self, out, refresh=False):
        self.raw = pathlib.Path(out) / 'raw'
        self.raw.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.previous_request = 0

    def get(self, name, route):
        url = BASE + route
        path = self.raw / (name + '.json')
        meta_path = self.raw / (name + '.source.json')
        if path.exists() and meta_path.exists() and not self.refresh:
            meta = read(meta_path)
            if meta.get('source') != url or meta.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError(f'Cache URL/hash mismatch: {path}. Use a new investigation directory.')
            return read(path)
        for attempt in range(3):
            time.sleep(max(0, 1.05 - (time.monotonic() - self.previous_request)))
            self.previous_request = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'DotaPublicEvidenceAudit/1.0'})
                with urllib.request.urlopen(req, timeout=25) as response:
                    raw = response.read()
                data = json.loads(raw)
                if isinstance(data, dict) and data.get('error'):
                    raise ValueError(str(data['error']))
                path.write_bytes(raw)
                write(meta_path, {'source': url, 'fetched_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                                 'sha256': hashlib.sha256(raw).hexdigest()})
                print(f'Saved {name}', flush=True)
                return data
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                wait = error.headers.get('Retry-After', '')
                if wait.isdecimal():
                    seconds = float(wait)
                else:
                    try:
                        retry_at = email.utils.parsedate_to_datetime(wait)
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
                        seconds = max(0, (retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        seconds = 3 * (attempt + 1)
                if seconds > 30:
                    raise RuntimeError(f'Rate limited: retry after {wait} seconds; collected files are retained.') from error
                time.sleep(seconds)
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))


def deduplicate(rows):
    found = {}
    duplicates = 0
    for row in rows:
        mid = row.get('match_id')
        if type(mid) is not int or mid <= 0:
            raise ValueError('Missing or invalid match_id.')
        if mid in found:
            if found[mid] != row:
                raise ValueError(f'Conflicting duplicate match {mid}; investigate rather than silently choosing a row.')
            duplicates += 1
        else:
            found[mid] = dict(row)
    return found, duplicates


def merge(base, projected):
    a, adup = deduplicate(base)
    b, bdup = deduplicate(projected)
    for mid in a.keys() & b.keys():
        for key in CORE:
            if a[mid].get(key) is not None and b[mid].get(key) is not None and a[mid][key] != b[mid][key]:
                raise ValueError(f'Core-field conflict: match {mid}, {key}.')
        for key in PROJECT:
            if key in b[mid]:
                if key not in a[mid] or b[mid][key] is not None:
                    a[mid][key] = b[mid][key]
    return list(a.values()), {'base_duplicate_rows_removed': adup, 'projection_duplicate_rows_removed': bdup,
                             'base_without_projection': sorted(a.keys() - b.keys()),
                             'projection_without_base': sorted(b.keys() - a.keys())}


def normalize(rows, offset=8, heroes=None):
    tz = dt.timezone(dt.timedelta(hours=offset))
    unique, duplicates = deduplicate(rows)
    result = []
    for row in unique.values():
        slot = row.get('player_slot')
        if type(slot) is not int or slot not in (*range(5), *range(128, 133)):
            raise ValueError(f'Invalid player_slot in match {row["match_id"]}.')
        if type(row.get('radiant_win')) is not bool:
            raise ValueError(f'Unknown match outcome in match {row["match_id"]}.')
        if type(row.get('start_time')) is not int or row['start_time'] <= 0:
            raise ValueError(f'Unknown start_time in match {row["match_id"]}.')
        m = dict(row)
        m['win'] = (slot < 128) == m['radiant_win']
        m['date_local'] = dt.datetime.fromtimestamp(m['start_time'], tz).isoformat()
        h = (heroes or {}).get(str(m.get('hero_id')), {})
        m['hero_name'] = h.get('localized_name') or m.get('hero_name') or f'Hero {m.get("hero_id")}'
        result.append(m)
    return sorted(result, key=lambda m: (m['start_time'], m['match_id'])), duplicates


def aggregate_check(rows, wl=None, heroes=None):
    ranked = [m for m in rows if m.get('lobby_type') == 7]
    win = sum(m['win'] for m in ranked)
    out = {'ranked_games': len(ranked), 'wins': win, 'losses': len(ranked)-win,
           'wl_endpoint_matches': None, 'hero_endpoint_mismatches': None}
    if wl is not None:
        out['wl_endpoint_matches'] = wl.get('win') == win and wl.get('lose') == len(ranked)-win
        out['independent_wl'] = wl
    if heroes is not None:
        actual = collections.defaultdict(lambda: [0, 0])
        for m in ranked:
            actual[m['hero_id']][0] += 1
            actual[m['hero_id']][1] += m['win']
        expected = {h['hero_id']: [h['games'], h['win']] for h in heroes}
        out['hero_endpoint_mismatches'] = [h for h in sorted(actual.keys() | expected.keys())
                                            if actual.get(h, [0, 0]) != expected.get(h, [0, 0])]
    return out


def pages(client, account, prefix, projected, size, max_pages):
    rows, seen, page_count, reason = [], set(), 0, 'page_limit_reached'
    for index in range(max_pages):
        params = [('limit', size), ('offset', len(rows))]
        if projected:
            params.extend(('project', p) for p in PROJECT)
        route = f'players/{account}/matches?' + urllib.parse.urlencode(params)
        page = client.get(f'{prefix}_{index:03d}', route)
        page_count += 1
        if not isinstance(page, list):
            raise ValueError('Match endpoint did not return an array.')
        ids = {x.get('match_id') for x in page}
        if page and not (ids - seen):
            reason = 'repeated_page_no_new_ids'
            break
        rows.extend(page)
        seen.update(ids)
        if len(page) < size:
            reason = 'short_or_empty_page'
            break
    return rows, {'pages': page_count, 'stop_reason': reason, 'raw_rows': len(rows),
                  'visible_history_may_be_incomplete': True}


def collect(args):
    account = account_id(args.account)
    out = pathlib.Path(args.out)
    old = out / 'collection.json'
    if old.exists() and read(old).get('account_id') != account:
        raise ValueError('This directory belongs to another account; use a new directory.')
    client = Client(out, args.refresh)
    profile = client.get('profile', f'players/{account}')
    remote_account = profile.get('profile', {}).get('account_id')
    if remote_account is not None and remote_account != account:
        raise ValueError('Profile account ID does not match requested account.')
    heroes = client.get('hero_constants', 'constants/heroes')
    wl = client.get('ranked_wl', f'players/{account}/wl?lobby_type=7')
    hs = client.get('ranked_heroes', f'players/{account}/heroes?lobby_type=7')
    base, coverage_base = pages(client, account, 'base', False, args.page_size, args.max_pages)
    projected, coverage_projected = pages(client, account, 'projected', True, args.page_size, args.max_pages)
    merged, joined = merge(base, projected)
    rows, _ = normalize(merged, args.utc_offset, heroes)
    validation = aggregate_check(rows, wl, hs)
    metadata = {'account_id': account, 'steamid64': str(STEAM_BASE + account),
                'collected_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'utc_offset_hours': args.utc_offset, 'base_paging': coverage_base,
                'projected_paging': coverage_projected, 'join': joined, 'validation': validation}
    write(out / 'history.json', rows)
    write(out / 'collection.json', metadata)
    print(json.dumps(metadata, ensure_ascii=False))


def stats(rows):
    n = len(rows)
    wins = sum(m['win'] for m in rows)
    result = {'games': n, 'wins': wins, 'losses': n-wins,
              'winrate_pct': round(100*wins/n, 2) if n else None,
              'first': rows[0]['date_local'] if n else None,
              'last': rows[-1]['date_local'] if n else None,
              'match_ids': [m['match_id'] for m in rows]}
    for field in ('duration', 'kills', 'deaths', 'assists', *PROJECT[:-1]):
        values = [m[field] for m in rows if type(m.get(field)) in (int, float) and math.isfinite(m[field])]
        result[field] = {'observed': len(values), 'missing': n-len(values),
                         'mean': round(st.mean(values), 3) if values else None,
                         'median': st.median(values) if values else None}
    rates = [m['hero_damage']*60/m['duration'] for m in rows
             if type(m.get('hero_damage')) in (int, float) and type(m.get('duration')) in (int, float)
             and math.isfinite(m['hero_damage']) and m['duration'] > 0]
    result['hero_damage_per_min_median'] = round(st.median(rates), 3) if rates else None
    result['zero_tower_damage'] = sum(m.get('tower_damage') == 0 for m in rows)
    result['zero_kills_and_assists'] = sum(m.get('kills') == 0 and m.get('assists') == 0 for m in rows)
    return result


def grouped(rows, key):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[str(key(row))].append(row)
    return {k: stats(v) for k, v in sorted(groups.items())}


def loss_runs(rows, minimum=2):
    result = []
    cursor = 0
    for won, group in itertools.groupby(rows, lambda m: m['win']):
        chunk = list(group)
        if not won and len(chunk) >= minimum:
            result.append({'run': stats(chunk),
                           'before_30_visible': stats(rows[max(0, cursor-30):cursor]),
                           'after_30_visible': stats(rows[cursor+len(chunk):cursor+len(chunk)+30])})
        cursor += len(chunk)
    return sorted(result, key=lambda v: (-v['run']['games'], v['run']['first']))


def stat_line(label, val):
    return f"{label}: {val['games']} games; {val['wins']}W/{val['losses']}L; {val['winrate_pct']}%"


def split_text(out, prefix, lines, limit=180000):
    out = pathlib.Path(out)
    parts, chunk, size = [], [], 0
    for line in lines:
        line = str(line) + '\n'
        bsize = len(line.encode('utf-8'))
        if chunk and size + bsize > limit:
            parts.append(''.join(chunk)); chunk = []; size = 0
        chunk.append(line); size += bsize
    if chunk:
        parts.append(''.join(chunk))
    names = []
    for index, text in enumerate(parts, 1):
        path = out / f'{prefix}_{index:02d}.txt'
        path.write_text(f'Part {index}/{len(parts)}\n' + text, encoding='utf-8')
        names.append(path.name)
    return names


def analyze(args):
    path = pathlib.Path(args.history)
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    heroes_path = pathlib.Path(args.heroes) if args.heroes else path.parent / 'raw/hero_constants.json'
    heroes = read(heroes_path) if heroes_path.exists() else None
    rows, duplicates = normalize(read(path), args.utc_offset, heroes)
    ranked = [m for m in rows if m.get('lobby_type') == 7]
    start = dt.date.fromisoformat(args.start) if args.start else None
    until = dt.date.fromisoformat(args.until) if args.until else None
    if start and until and start > until:
        raise ValueError('--from must be on or before --until.')
    selected = [m for m in ranked if (not start or dt.date.fromisoformat(m['date_local'][:10]) >= start)
                and (not until or dt.date.fromisoformat(m['date_local'][:10]) <= until)]
    wl_path = path.parent / 'raw/ranked_wl.json'
    hs_path = path.parent / 'raw/ranked_heroes.json'
    validation = aggregate_check(rows, read(wl_path) if wl_path.exists() else None,
                                 read(hs_path) if hs_path.exists() else None)
    metadata_path = path.parent / 'collection.json'
    metadata = read(metadata_path) if metadata_path.exists() else None
    all_h = grouped(ranked, lambda m: m['hero_name'])
    selected_h = grouped(selected, lambda m: m['hero_name'])
    per_hero_runs = {name: loss_runs([m for m in selected if m['hero_name'] == name], 5)[:3]
                     for name in selected_h}
    parsed_losses = [m['match_id'] for m in reversed(selected) if not m['win'] and m.get('version') is not None][:8]
    low_losses = sorted((m for m in selected if not m['win'] and m.get('hero_damage') is not None),
                        key=lambda m: (m['hero_damage'], -m['start_time']))[:6]
    controls = []
    for m in low_losses:
        pool = [c for c in selected if c['hero_id'] == m['hero_id'] and c['win']]
        if pool:
            controls.append(min(pool, key=lambda c: abs(c['start_time']-m['start_time']))['match_id'])
        losses = [c for c in selected if c['hero_id'] == m['hero_id'] and not c['win']
                  and c['match_id'] != m['match_id'] and type(c.get('duration')) in (int, float)]
        if losses:
            controls.append(max(losses, key=lambda c: c['duration'])['match_id'])
    result = {'scope': {'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'source_history': str(path.resolve()), 'account_id': metadata.get('account_id') if metadata else None,
                         'collection': metadata, 'all_public_rows': len(rows), 'duplicates_removed': duplicates,
                         'utc_offset_hours': args.utc_offset, 'from_inclusive': args.start, 'until_inclusive': args.until},
              'validation': validation, 'ranked_all': stats(ranked), 'selected_ranked': stats(selected),
              'all_ranked_heroes': all_h, 'selected_heroes': selected_h,
              'selected_months': grouped(selected, lambda m: m['date_local'][:7]),
              'selected_party_sizes': grouped(selected, lambda m: m.get('party_size') if m.get('party_size') is not None else 'unknown'),
              'selected_hero_party': grouped(selected, lambda m: f"{m['hero_name']} | party={m.get('party_size') if m.get('party_size') is not None else 'unknown'}"),
              'selected_loss_runs': loss_runs(selected)[:20],
              'selected_hero_appearance_loss_runs': {k: v for k, v in per_hero_runs.items() if v},
              'under_15_minutes': stats([m for m in selected if type(m.get('duration')) in (int, float) and m['duration'] < 900]),
              'at_least_15_minutes': stats([m for m in selected if type(m.get('duration')) in (int, float) and m['duration'] >= 900]),
              'candidate_detail_ids': list(dict.fromkeys(parsed_losses + [m['match_id'] for m in low_losses] + controls)),
              'interpretation': 'Descriptive statistics and investigation leads only; no automated misconduct finding.'}
    write(out / 'analysis.json', result)
    write(out / 'ranked_records.json', selected)
    lines = ['PUBLIC OPENDOTA DESCRIPTIVE EVIDENCE', f"Account ID: {result['scope']['account_id'] or 'not supplied in source metadata'}",
             f'UTC offset: {args.utc_offset:+g}; selected dates inclusive: {args.start or "all"} to {args.until or "all"}',
             'Missing values remain unknown. All ranked outcomes retained. Public history can be incomplete.',
             'Hero appearance runs may contain other-hero wins; 15 minutes is descriptive, not a violation threshold.',
             stat_line('Entire ranked sample', result['ranked_all']), stat_line('Selected ranked sample', result['selected_ranked']),
             'Independent aggregate check: ' + json.dumps(validation),
             'Collection and coverage: ' + json.dumps(metadata, ensure_ascii=False), '', 'ALL RANKED HERO TOTALS']
    lines += [stat_line(k, v) for k, v in sorted(all_h.items(), key=lambda kv: -kv[1]['games'])]
    lines += ['', 'SELECTED SAME-WINDOW HERO TOTALS'] + [stat_line(k, v) for k, v in selected_h.items()]
    lines += ['', 'SELECTED HERO / PARTY COMPARISONS'] + [stat_line(k, v) for k, v in result['selected_hero_party'].items()]
    lines += ['', 'SELECTED VISIBLE RANKED LOSING RUNS']
    for run in result['selected_loss_runs']:
        v = run['run']
        lines += [stat_line(f"{v['first']} to {v['last']}", v), 'IDs: ' + ', '.join(map(str, v['match_ids']))]
    lines += ['', 'Source history SHA-256: ' + result['scope']['input_sha256']]
    if metadata:
        aid = metadata['account_id']
        lines += [f'Source history endpoint: {BASE}players/{aid}/matches',
                  f'Independent ranked heroes: {BASE}players/{aid}/heroes?lobby_type=7',
                  f'Independent ranked totals: {BASE}players/{aid}/wl?lobby_type=7',
                  'Each raw response has its own UTC capture time and SHA-256 in raw/*.source.json.']
    (out / 'evidence_summary_EN.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    record_lines = ['Selected public ranked records, including ALL wins and losses.',
                    'Date with UTC offset | Match | Hero | Party (None=unknown) | Result | Duration seconds | K/D/A | Hero damage | Tower damage']
    for m in selected:
        record_lines.append(f"{m['date_local']} | {m['match_id']} | {m['hero_name']} | {m.get('party_size')} | {'W' if m['win'] else 'L'} | {m.get('duration')} | {m.get('kills')}/{m.get('deaths')}/{m.get('assists')} | {m.get('hero_damage')} | {m.get('tower_damage')}")
    files = split_text(out, 'ranked_matches_EN', record_lines)
    print(json.dumps({'ranked': len(ranked), 'selected': len(selected), 'validation': validation,
                      'text_files': ['evidence_summary_EN.txt', *files], 'candidate_detail_ids': result['candidate_detail_ids']}))


def sum_known(players, field):
    values = [p.get(field) for p in players]
    return sum(values) if all(type(v) in (int, float) for v in values) else None


def inspect_match(data, account):
    ps = data.get('players', [])
    targets = [p for p in ps if p.get('account_id') == account]
    if len(targets) != 1:
        raise ValueError(f"Account {account} not uniquely visible in match {data.get('match_id')}.")
    t = targets[0]
    if type(t.get('player_slot')) is not int or t['player_slot'] not in (*range(5), *range(128, 133)) or type(data.get('radiant_win')) is not bool:
        raise ValueError('Match outcome or target slot is unavailable.')
    team = [p for p in ps if type(p.get('player_slot')) is int and (p['player_slot'] < 128) == (t['player_slot'] < 128)]
    roster = [p.get('account_id') for p in team]
    roster_complete = len(team) == 5 and all(type(i) is int and 0 < i <= MAX_ACCOUNT for i in roster) and len(set(roster)) == 5
    five_stack = len(team) == 5 and t.get('party_id') is not None and all(p.get('party_size') == 5 and p.get('party_id') == t['party_id'] for p in team)
    players_by_slot = {p['player_slot']: p for p in ps if 'player_slot' in p}
    chat = []
    for msg in data.get('chat') or []:
        if msg.get('type') != 'chat':
            continue
        speaker = players_by_slot.get(msg.get('player_slot'))
        same_team = (speaker['player_slot'] < 128) == (t['player_slot'] < 128) if speaker else None
        same_party = (same_team and speaker['party_id'] == t['party_id']) if speaker and speaker.get('party_id') is not None and t.get('party_id') is not None else None
        chat.append({**msg, 'speaker_account_id': speaker.get('account_id') if speaker else None,
                     'speaker_is_target': speaker.get('account_id') == account if speaker else False,
                     'same_team_as_target': same_team, 'same_party_as_target': same_party})
    fields = ('account_id', 'player_slot', 'hero_id', 'kills', 'deaths', 'assists', 'hero_damage',
              'tower_damage', 'last_hits', 'neutral_kills', 'lane_kills', 'denies', 'leaver_status',
              'party_id', 'party_size', 'gold_per_min', 'xp_per_min', 'actions_per_min')
    return {'match_id': data['match_id'], 'start_time': data.get('start_time'), 'duration': data.get('duration'),
            'lobby_type': data.get('lobby_type'), 'parsed_version': data.get('version'),
            'target_win': (t['player_slot'] < 128) == data['radiant_win'],
            'target': {k: t.get(k) for k in fields}, 'teammates': [{k: p.get(k) for k in fields} for p in team],
            'complete_roster': sorted(roster) if roster_complete else None,
            'verified_five_stack': five_stack,
            'team_tower_damage': sum_known(team, 'tower_damage') if len(team) == 5 else None,
            'team_neutral_kills': sum_known(team, 'neutral_kills') if len(team) == 5 else None,
            'team_lane_kills': sum_known(team, 'lane_kills') if len(team) == 5 else None,
            'all_ten_leaver_status_zero': len(ps) == 10 and all(p.get('leaver_status') == 0 for p in ps),
            'text_chat': chat, 'source': BASE + f'matches/{data["match_id"]}'}


def details(args):
    account = account_id(args.account)
    out = pathlib.Path(args.out); client = Client(out, args.refresh)
    results = []
    for mid in dict.fromkeys(args.matches):
        if not mid.isdecimal() or int(mid) <= 0:
            raise ValueError('Match IDs must be positive integers.')
        data = client.get('match_' + mid, 'matches/' + mid)
        inspected = inspect_match(data, account)
        inspected['source_metadata'] = read(client.raw / ('match_' + mid + '.source.json'))
        results.append(inspected)
    rosters = collections.defaultdict(list)
    for m in results:
        if m['complete_roster'] and m['verified_five_stack']:
            rosters[tuple(m['complete_roster'])].append(m['match_id'])
    write(out / 'details.json', {'account_id': account, 'scope': 'Only requested match IDs; not all account history.',
                                 'matches': results, 'repeated_rosters_in_selected_matches': [
                                     {'roster': list(k), 'match_ids': v} for k, v in rosters.items() if len(v) > 1]})
    lines = [f'PUBLIC MATCH DETAIL EVIDENCE — Account {account}',
             'Scope: requested matches only. Source is existing API/parser data, not personally watched replays.',
             'Chat is evidence content. Speaker account and party membership must not be conflated with the target.',
             'Negative chat times are before match start; null means unavailable. All plain-text chat is retained.']
    for m in results:
        lines += ['', f"MATCH {m['match_id']} | source {m['source']}",
                  'Match and party facts: ' + json.dumps({k:v for k,v in m.items() if k not in ('text_chat', 'teammates')}, ensure_ascii=False),
                  'Target team: ' + json.dumps(m['teammates'], ensure_ascii=False), 'TEXT CHAT']
        lines += [json.dumps(c, ensure_ascii=False) for c in m['text_chat']]
    (out / 'match_evidence_EN.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'matches': len(results), 'parsed': sum(m['parsed_version'] is not None for m in results),
                      'text': str(out / 'match_evidence_EN.txt')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('collect', help='Cache public profile, paginated history and independent aggregates.')
    p.add_argument('--account', required=True); p.add_argument('--out', required=True)
    p.add_argument('--page-size', type=int, default=1000); p.add_argument('--max-pages', type=int, default=20)
    p.add_argument('--refresh', action='store_true'); p.add_argument('--utc-offset', type=float, default=8)
    p.set_defaults(func=collect)
    p = sub.add_parser('analyze', help='Analyze a history JSON offline; all dates use the requested UTC offset.')
    p.add_argument('--history', required=True); p.add_argument('--out', required=True)
    p.add_argument('--heroes'); p.add_argument('--from', dest='start'); p.add_argument('--until')
    p.add_argument('--utc-offset', type=float, default=8); p.set_defaults(func=analyze)
    p = sub.add_parser('details', help='Fetch selected matches and export correctly attributed chat evidence.')
    p.add_argument('--account', required=True); p.add_argument('--matches', nargs='+', required=True)
    p.add_argument('--out', required=True); p.add_argument('--refresh', action='store_true')
    p.set_defaults(func=details)
    args = parser.parse_args()
    if not -14 <= getattr(args, 'utc_offset', 8) <= 14:
        parser.error('--utc-offset must be between -14 and 14.')
    if args.command == 'collect' and (not 1 <= args.page_size <= 1000 or not 1 <= args.max_pages <= 100):
        parser.error('Use page-size 1..1000 and max-pages 1..100.')
    try:
        args.func(args)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'Error: {error}\nExisting captured files are retained.\n')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
