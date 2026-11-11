"""Independent check of every number in the V3 letter, from the raw FDA records.

Run:  python3 -I verify_v3.py [manuscript.html] [--api]
Takes only the frozen dictionaries from pipeline.py (norm, classify, brand, reaction regexes). Counting, dates, ROR, CI and P are
written here separately from pipeline.py and audit.py. Writes out/v3_record_ledger.csv (one row per hair-care hair-loss report)
and out/v3_number_ledger.csv (each claim, recomputed). With a manuscript, every number in it must be a recomputed claim or an
allowed structural number. --api spot-checks records against the live openFDA API.
"""
import collections, csv, importlib.util, json, math, os, random, re, statistics, sys, time, urllib.parse, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pipeline', os.path.join(HERE, 'pipeline.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
Z = statistics.NormalDist().inv_cdf(.975)
C = collections.Counter
MON = dict(zip('jan feb mar apr may jun jul aug sep oct nov dec'.split(), range(1, 13)))
EXP, CUT = 'Expedited (15-Day)', '20231229'
SURGE = {'wen', 'monat', 'devacurl', 'olaplex'}
HAIRCATS = {'hair_care', 'hair_dye_bleach', 'hair_relaxer_straightener'}
GROUPS = dict(P.REACTIONS)


def event_month(s, received):
    """(year, month) from 'dd-Mon-yy', 'yy-Mon' or 'Mon-yy'; (year, None) from 'yyyy'; None if blank."""
    if not s:
        return None
    y4 = lambda yy: 2000 + yy if 2000 + yy <= int(received[:4]) else 1900 + yy
    p = s.split('-')
    if len(p) == 3:
        return y4(int(p[2])), MON[p[1].lower()]
    if len(p) == 2:
        return (y4(int(p[0])), MON[p[1].lower()]) if p[0].isdigit() else (y4(int(p[1])), MON[p[0].lower()])
    return (int(s), None) if s.isdigit() else None


def ror(a, b, c, d):
    lg, se = math.log(a * d / (b * c)), math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    return math.exp(lg), math.exp(lg - Z * se), math.exp(lg + Z * se), lg, se


def compare(x, y):
    d, se = x[3] - y[3], math.hypot(x[4], y[4])
    return math.exp(d), math.exp(d - Z * se), math.exp(d + Z * se), 2 * (1 - statistics.NormalDist().cdf(abs(d / se)))


def f1(v):
    return f'{v:.1f}'


def num(n):  # JAMA style: thin space from 10 000
    return f'{n:,}'.replace(',', ' ') if n >= 10000 else str(n)


def pct(k, n):
    return f'{100 * k / n:.1f}%'


def pval(p):
    return f'P = {p:.2f}'.replace('0.', '.')


with zipfile.ZipFile(P.ZIP) as z:
    raw = json.loads(z.read(z.namelist()[0]))
meta, reports = raw['meta'], raw['results']
assert len({x['report_number'] for x in reports}) == len(reports)
R = []
for x in reports:
    sus = sorted({P.norm(q.get('product_name', '')) for q in x.get('products', []) if q.get('role') == 'Suspect'})
    cats = {P.classify(n) for n in sus}
    brands = {P.brand(n) for n in sus} - {''}
    pts = x.get('reactions', [])
    pat = x.get('patient', {})
    first = x['initial_received_date']
    R.append(dict(id=x['report_number'], sus=sus, cats=cats, brands=brands, pts=pts, first=first, type=x['report_type'], late=first >= CUT,
                  talc='talc_powder' in cats, hc='hair_care' in cats, anyhair=bool(cats & HAIRCATS), age=pat.get('age'), sex=pat.get('gender'),
                  ev=x.get('event_date'), evm=event_month(x.get('event_date'), first), out=x.get('outcomes', []),
                  hl=any(GROUPS['hair_loss'].search(t.lower()) for t in pts), alo=any('alopecia' in t.lower() for t in pts),
                  mal=any(GROUPS['malignancy'].search(t.lower()) for t in pts)))
for r in R:
    r['lor'] = bool(r['brands'] & P.LOREAL)
    r['surge'] = bool(r['brands'] & SURGE)
    r['grp'] = next((b for b in P.SURGE_BRANDS if b in r['brands']), 'loreal' if r['lor'] else 'other')
NT = [r for r in R if not r['talc']]
H = [r for r in NT if r['anyhair']]
U = [r for r in NT if r['hc'] and r['hl']]
pre, post = [r for r in U if not r['late']], [r for r in U if r['late']]
lor = [r for r in post if r['grp'] == 'loreal']


def cells(rows, outcome='hl'):
    a = sum(r['hc'] and r[outcome] for r in rows)
    b = sum(r['hc'] and not r[outcome] for r in rows)
    c = sum(not r['hc'] and r[outcome] for r in rows)
    return a, b, c, len(rows) - a - b - c


def strict_key(r):
    return tuple(r['sus']), frozenset(r['pts']), r['age'], r['sex'], r['ev']


def dedup(rows, need_date):
    seen, keep = set(), []
    for r in sorted(rows, key=lambda r: (r['first'], r['id'])):
        k = strict_key(r)
        if k in seen and (r['ev'] or not need_date):
            continue
        seen.add(k)
        keep.append(r)
    return keep


L = []  # (claim id, text expected in the letter, what it is)


def claim(cid, text, what=''):
    L.append((cid, str(text), what))


def rorrow(cid, rows, what, outcome='hl', table=True):
    t = cells(rows, outcome)
    e, lo, hi, *_ = ror(*t)
    claim(cid + '_ci', f'{f1(e)} ({f1(lo)}-{f1(hi)})' if table else f'{f1(e)} (95% CI, {f1(lo)}-{f1(hi)})', what)
    if table:
        claim(cid + '_hc', f'{num(t[0])}/{num(t[1])}', what + ', hair-care cells')
        claim(cid + '_cmp', f'{num(t[2])}/{num(t[3])}', what + ', comparator cells')
    return ror(*t), t


# --- File and talc
claim('n_total', num(len(R)), 'reports in file')
exp = [r for r in R if r['type'] == EXP]
talc = [r for r in exp if r['talc']]
claim('n_exp', num(len(exp)), 'expedited reports')
claim('n_talc', num(len(talc)), 'expedited reports naming talc-associated powder')
claim('pct_talc', pct(len(talc), len(exp)), 'talc share of expedited')
mal = sum(r['mal'] for r in talc)
claim('n_mal', num(mal), 'talc reports with a malignancy term')
claim('pct_mal', pct(mal, len(talc)), 'malignancy share of talc')
lag = [(int(r['first'][:4]) * 12 + int(r['first'][4:6])) - (r['evm'][0] * 12 + r['evm'][1]) for r in talc if r['evm'] and r['evm'][1]]
claim('n_lag', num(len(lag)), 'talc reports with month-level event date')
claim('lag_median', f'{statistics.median(lag):.0f} months', 'median event-to-receipt interval')
nontalc_exp = [r for r in exp if not r['talc']]
claim('n_exp_nontalc', len(nontalc_exp), 'remaining expedited')
clusters = C(k for r in nontalc_exp for k in {g for g, p in GROUPS.items() if any(p.search(t.lower()) for t in r['pts'])})
assert clusters.most_common(1)[0][0] == 'hair_loss', clusters
claim('n_exp_hl', clusters['hair_loss'], 'hair loss in remaining expedited')
claim('pct_exp_hl', pct(clusters['hair_loss'], len(nontalc_exp)), 'hair loss share of remaining expedited')

# --- Channel and concentration
claim('n_pre', len(pre), 'hair-care hair-loss, before')
claim('n_pre_direct', sum(r['type'] == 'Direct' for r in pre), 'before, direct')
claim('pct_pre_direct', pct(sum(r['type'] == 'Direct' for r in pre), len(pre)), 'before, direct share')
claim('n_post', len(post), 'hair-care hair-loss, after')
post_exp = [r for r in post if r['type'] == EXP]
post_dir = [r for r in post if r['type'] == 'Direct']
pre_dir = [r for r in pre if r['type'] == 'Direct']
assert len(post_exp) + len(post_dir) == len(post)
claim('n_post_exp', len(post_exp), 'after, expedited')
claim('pct_post_exp', pct(len(post_exp), len(post)), 'after, expedited share')
claim('n_lor', len(lor), "after, L'Oreal-owned")
claim('pct_lor', pct(len(lor), len(post)), "after, L'Oreal share")
ola = [r for r in post if r['grp'] == 'olaplex']
ogx = [r for r in post if r['grp'] == 'ogx']
claim('n_ola', len(ola), 'after, Olaplex')
claim('pct_ola', pct(len(ola), len(post)), 'after, Olaplex share')
claim('n_ogx', len(ogx), 'after, OGX')
claim('pct_ogx', pct(len(ogx), len(post)), 'after, OGX share')
names = C(n for r in lor for n in r['sus'] if P.brand(n) in P.LOREAL)
claim('n_lor_names', len(names), "L'Oreal product names")
claim('pct_top_product', pct(names.most_common(1)[0][1], len(lor)), 'most-named product share')
claim('n_lor_days', len({r['first'] for r in lor}), "L'Oreal receipt dates")
named = ('kerastase|keratase', 'redken', 'pureology', 'biolage', r'\bmatrix\b', r'\bl ?oreal', 'garnier|fructis', 'shu uemura', 'dark (and )?lovely',
         r'softsheen|soft sheen|\bssc\b|wave nouveau', 'mizani', 'pulp riot', 'kiehl', 'cerave')
claim('n_lor_brands', sum(any(re.search(b, n) for n in names) for b in named), "L'Oreal-owned brands named")
peak = {'wen': '2016', 'monat': '2018', 'devacurl': '2020', 'olaplex': '2023'}
pk = {b: sum(r['grp'] == b and r['first'][:4] == y for r in U) for b, y in peak.items()}
for b, n in pk.items():
    claim('peak_' + b, n, f'{b} reports in peak year')
claim('n_U', len(U), 'all hair-care hair-loss')
claim('n_peaks', sum(pk.values()), 'four peak-year episodes')
claim('pct_peaks', pct(sum(pk.values()), len(U)), 'four peak-year episodes, share')

# --- Completeness by stream
miss = lambda rows, f: pct(sum(not r[f] for r in rows), len(rows))
claim('age_pre_dir', miss(pre_dir, 'age'), 'age missing, direct before')
claim('age_post_dir', miss(post_dir, 'age'), 'age missing, direct after')
claim('n_post_dir', len(post_dir), 'direct after')
claim('age_post_exp', miss(post_exp, 'age'), 'age missing, expedited after')
claim('ev_pre_dir', miss(pre_dir, 'ev'), 'event date missing, direct before')
claim('ev_post_dir', miss(post_dir, 'ev'), 'event date missing, direct after')
claim('ev_post_exp', miss(post_exp, 'ev'), 'event date missing, expedited after')
lor_e = [r for r in post_exp if r['grp'] == 'loreal']
ola_e = [r for r in post_exp if r['grp'] == 'olaplex']
claim('ev_lor_e', miss(lor_e, 'ev'), "event date missing, L'Oreal expedited")
claim('ev_ola_e', miss(ola_e, 'ev'), 'event date missing, Olaplex expedited')
claim('sex_lor_e', miss(lor_e, 'sex'), "sex missing, L'Oreal expedited")
claim('sex_ola_e', miss(ola_e, 'sex'), 'sex missing, Olaplex expedited')
claim('age_talc', miss(talc, 'age'), 'age missing, talc expedited')
claim('ev_talc', miss(talc, 'ev'), 'event date missing, talc expedited')
dated = [r for r in post if r['ev']]
claim('n_dated', len(dated), 'later reports with event date')
recent = sum(r['evm'][0] >= 2024 for r in dated)
claim('n_recent', recent, 'dated later reports with events in 2024-2025')
claim('pct_recent', pct(recent, len(dated)), 'dated later reports, recent share')
claim('n_lor_dated', sum(bool(r['ev']) for r in lor), "L'Oreal reports with event date")
claim('pct_lor_dated', pct(sum(bool(r['ev']) for r in lor), len(lor)), "L'Oreal reports with event date, share")
claim('pct_undated', miss(post, 'ev'), 'later reports without event date')
serious = sum('Other Serious or Important Medical Event' in r['out'] for r in post_exp)
claim('n_serious', serious, 'expedited hair loss coded other serious')
claim('pct_serious', pct(serious, len(post_exp)), 'expedited hair loss coded other serious, share')

# --- RORs
main, _ = rorrow('main', H, 'main ROR')
claim('main_text', f'{f1(main[0])} (95% CI, {f1(main[1])}-{f1(main[2])})', 'main ROR in text')
before, _ = rorrow('before', [r for r in H if not r['late']], 'before')
after, _ = rorrow('after', [r for r in H if r['late']], 'after')
claim('before_text', f'{f1(before[0])} (95% CI, {f1(before[1])}-{f1(before[2])})', 'before in text')
claim('after_text', f'{f1(after[0])} (95% CI, {f1(after[1])}-{f1(after[2])})', 'after in text')
k = compare(before, after)
claim('ratio', f'{k[0]:.2f} (95% CI, {k[1]:.2f}-{k[2]:.2f})', 'ratio of RORs')
claim('ratio_p', pval(k[3]), 'ratio of RORs P')
maximal = dedup(NT, False)
Hm = [r for r in maximal if r['anyhair']]
km = compare(ror(*cells([r for r in Hm if not r['late']])), ror(*cells([r for r in Hm if r['late']])))
claim('ratio_max', f'{km[0]:.2f} (95% CI, {km[1]:.2f}-{km[2]:.2f})', 'ratio of RORs, maximal duplicate removal')
claim('ratio_max_p', pval(km[3]), 'ratio P, maximal duplicate removal')
ex4, _ = rorrow('ex4', [r for r in H if not r['surge']], 'excluding 4 brands')
exl, _ = rorrow('exl', [r for r in H if not r['lor']], "excluding L'Oreal")
exb, _ = rorrow('exb', [r for r in H if not r['surge'] and not r['lor']], 'excluding both')
claim('ex4_text', f'{f1(ex4[0])} (95% CI, {f1(ex4[1])}-{f1(ex4[2])})', 'excluding 4 brands in text')
claim('exb_text', f'{f1(exb[0])} (95% CI, {f1(exb[1])}-{f1(exb[2])})', 'excluding both in text')
rorrow('alo', H, 'alopecia terms only', 'alo')
conservative = dedup(NT, True)
rorrow('cons', [r for r in conservative if r['anyhair']], 'conservative duplicates')
rorrow('max', Hm, 'maximal duplicates')
rorrow('allnt', NT, 'all non-talc comparator')
split = lambda rows, outcome='hl': compare(ror(*cells([r for r in rows if not r['late']], outcome)), ror(*cells([r for r in rows if r['late']], outcome)))
for cid, rows, outcome in (('main', H, 'hl'), ('cons', [r for r in conservative if r['anyhair']], 'hl'), ('max', Hm, 'hl'), ('alo', H, 'alo'), ('exl', [r for r in H if not r['lor']], 'hl'),
                           ('ex4', [r for r in H if not r['surge']], 'hl'), ('exb', [r for r in H if not r['surge'] and not r['lor']], 'hl'), ('allnt', NT, 'hl')):
    q = split(rows, outcome)
    claim('period_' + cid, f'{q[0]:.2f} ({q[1]:.2f}-{q[2]:.2f})', 'ratio of RORs, before vs after')
a_, b_, c_, d_ = cells(H)
claim('prr', f1((a_ / (a_ + b_)) / (c_ / (c_ + d_))), 'proportional reporting ratio, main analysis')
claim('dup_cons', len(H) - sum(r['anyhair'] for r in conservative), 'hair reports removed, conservative')
claim('dup_max', len(H) - len(Hm), 'hair reports removed, maximal')
assert not [r for r in H if r['surge'] and r['lor']], 'brand exclusions overlap'
assert not [r for r in H if r['surge'] and not r['hc']], 'surge brands in comparator'
cmp_lor = sum(r['lor'] and not r['hc'] for r in H)
claim('cmp_lor', cmp_lor, "comparator reports naming L'Oreal-owned brands")
fix = lambda bad: [r for r in H if not (r['hc'] and bad(r))]
fl, fb = ror(*cells(fix(lambda r: r['lor']))), ror(*cells(fix(lambda r: r['lor'] or r['surge'])))
claim('fixed_lor', f'{f1(fl[0])} (95% CI, {f1(fl[1])}-{f1(fl[2])})', "L'Oreal excluded, comparator fixed")
claim('fixed_both', f'{f1(fb[0])} (95% CI, {f1(fb[1])}-{f1(fb[2])})', 'both excluded, comparator fixed')
claim('n_2003', sum(r['first'][:4] == '2003' for r in U), 'hair-care hair-loss reports received in 2003, off the figure')

# Kappa: agreement, kappa and weights are recomputed here from the key and ratings. CIs come from kappa_validation.py, whose statistics are self-tested.
RATED = os.path.join(HERE, 'review', 'kappa_ratings', 'kappa_reviewer_workbook_completed_molly_mishulin.xlsx')
if os.path.exists(RATED):
    spec = importlib.util.spec_from_file_location('kv', os.path.join(HERE, 'kappa_validation.py'))
    K = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(K)
    key, strata, manifest = K.load_key()
    rated = K.merge(K.read_ratings(RATED), key, set(manifest['categories']) | {manifest['fallback']})
    n = len(rated)
    agree = sum(r['dictionary'] == r['human'] for r in rated)
    d, h = C(r['dictionary'] for r in rated), C(r['human'] for r in rated)
    pe = sum(d[k] * h[k] for k in d) / n / n
    kap = (agree / n - pe) / (1 - pe)
    size = C(r['stratum'] for r in rated)
    w = [strata[r['stratum']]['frame_names'] / size[r['stratum']] for r in rated]
    tot = sum(w)
    dw, hw = collections.Counter(), collections.Counter()
    for r, x in zip(rated, w):
        dw[r['dictionary']] += x / tot
        hw[r['human']] += x / tot
    po_w = sum(x for r, x in zip(rated, w) if r['dictionary'] == r['human']) / tot
    pe_w = sum(dw[k] * hw[k] for k in dw)
    kap_w = (po_w - pe_w) / (1 - pe_w)
    neff = tot ** 2 / sum(x * x for x in w)
    wl, wh = K.wilson(po_w * neff, neff)
    boot = K.bootstrap(rated, strata)
    claim('k_cover', pct(sum(strata[s]['frame_names'] for s in size), manifest['frame_names']), 'names in sampled strata')
    claim('k_agree', f'{agree} of {n}', 'names agreed')
    claim('k_pct', pct(agree, n), 'agreement')
    claim('k_kappa', f'κ = {kap:.2f} [95% CI, {boot["kappa"][0]:.2f}-{boot["kappa"][1]:.2f}]', 'Cohen kappa')
    claim('k_po_w', f'{100 * po_w:.1f}% (95% CI, {100 * wl:.1f}-{100 * wh:.1f})', 'weighted agreement')
    claim('k_kappa_w', f'{kap_w:.2f} (95% CI, {boot["kappa_names"][0]:.2f}-{boot["kappa_names"][1]:.2f})', 'weighted kappa')
    for s_ in ('hair_dye_bleach', 'hair_relaxer_straightener', 'unclassified'):
        g = [r for r in rated if r['stratum'] == s_]
        claim('k_stratum_' + s_, pct(sum(r['dictionary'] == r['human'] for r in g), len(g)), 'agreement in stratum')

out = os.path.join(HERE, 'out')
with open(os.path.join(out, 'v3_record_ledger.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow('report_number first_received period report_type group age_present sex_present event_date_present suspect_products reactions'.split())
    for r in sorted(U, key=lambda r: r['first']):
        w.writerow([r['id'], r['first'], 'after' if r['late'] else 'before', r['type'], r['grp'], int(bool(r['age'])), int(bool(r['sex'])), int(bool(r['ev'])), '; '.join(r['sus']), '; '.join(sorted(set(r['pts'])))])
with open(os.path.join(out, 'v3_number_ledger.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['claim', 'recomputed', 'what'])
    w.writerows(L)
print(f'{len(L)} claims recomputed from {len(R)} raw records; file last_updated {meta["last_updated"]}; hair-care hair-loss records in ledger {len(U)}')

# Reasons a number may appear without being a claim: dates, reference numbers, identifiers, definitions.
paths = [a for a in sys.argv[1:] if not a.startswith('--')]
if paths:
    html = open(paths[0], encoding='utf-8').read()
    body = re.sub(r'<style.*?</style>', ' ', html, flags=re.S)
    letter = body[body.index('<h2>Introduction'):body.index('<!--end-letter-->')]
    text = re.sub(r'<sup>[^<]*</sup>', ' ', letter)
    text = re.sub(r'<[^>]+>', ' ', text).replace('&#8201;', ' ').replace(' ', ' ')
    text = re.sub(r'\s+', ' ', text)
    tok = lambda t: re.findall(r'\d{2} \d{3}(?!\d)|\d+(?:\.\d+)?|\.\d+', t)
    found, known = set(tok(text)), {x for c in L for x in tok(c[1])}
    print('Claims with a number absent from the letter (audit-only is fine):', [c[0] for c in L if not set(tok(c[1])) <= found])
    # Dates, thresholds, regulatory facts and study design, none of them computed from the data.
    allowed = set('0 15 29 2023 2024 2025 2026 2001 2016 2018 2020 2003 2004 26 127 3.9 17 250 95 2 4 10'.split()) | {'21 000'}
    print('Numbers in the letter with no recomputed claim:', sorted(found - known - allowed))
    print('Structural numbers used:', sorted(found & allowed - known))
if '--api' in sys.argv:
    def api(q, extra=''):
        url = 'https://api.fda.gov/cosmetic/event.json?' + q + extra
        req = urllib.request.Request(url, headers={'User-Agent': 'cosmetic-ae-verify/1.0'})
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            return {'error': e.code}
    log = [f"API last_updated {api('limit=1')['meta']['last_updated']}, total {api('limit=1')['meta']['results']['total']} (file {len(R)})"]
    for t, n in C(r['type'] for r in R).items():  # shortcut: the count endpoint is not offered for this field, so each type is searched
        time.sleep(.35)
        total = api('search=' + urllib.parse.quote(f'report_type:"{t}"'), '&limit=1').get('meta', {}).get('results', {}).get('total')
        log.append(f'Report type {t}: live {total}, file {n}')
    rng = random.Random(2026)
    sample = rng.sample(post, 20) + rng.sample(pre, 15) + rng.sample(talc, 15)
    ok = bad = 0
    for r in sample:
        time.sleep(.35)
        got = api('search=' + urllib.parse.quote(f'report_number:"{r["id"]}"'), '&limit=1').get('results', [{}])[0]
        same = (got.get('report_type') == r['type'] and got.get('initial_received_date') == r['first'] and sorted(got.get('reactions', [])) == sorted(r['pts'])
                and got.get('event_date') == r['ev'] and bool(got.get('patient', {}).get('age')) == bool(r['age']))
        ok += same
        bad += not same
        if not same:
            log.append(f'MISMATCH {r["id"]}')
    log.append(f'{ok} of {ok + bad} random records match the live API on type, first receipt, reactions, event date, age presence')
    for term in ('redken', 'kerastase', 'pureology', 'olaplex', 'biolage'):
        time.sleep(.35)
        q = f'products.product_name:{term}+AND+reactions.exact:"Alopecia"+AND+initial_received_date:[{CUT}+TO+20251231]'
        live_n = api('search=' + q, '&limit=1').get('meta', {}).get('results', {}).get('total')
        file_n = sum(1 for r in R if r['late'] and 'Alopecia' in r['pts'] and any(term in n.split() for n in r['sus']))
        log.append(f'{term}: live {live_n}, file {file_n}')
    print('\n'.join(log))
    open(os.path.join(out, 'v3_api_spot_check.txt'), 'w').write('\n'.join(log) + '\n')
