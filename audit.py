"""Independent audit of pipeline.py: recounts everything from the raw records with its own 2x2 and CI code.

Run after pipeline.py:  python3 -I audit.py > out/audit_results.txt
Reuses only pipeline.py's dictionaries (norm, classify, brand, brand_group). Exploratory unless the letter says otherwise.
"""
import collections, csv, datetime, importlib.util, json, math, os, random, re, statistics, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pipeline', os.path.join(HERE, 'pipeline.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
C = collections.Counter
MOCRA = '20231229'
HAIR = {'hair_care', 'hair_dye_bleach', 'hair_relaxer_straightener'}
HL = re.compile(r'alopecia|hypotrichosis|madarosis|hair thinning', re.I)
ALOPECIA = re.compile(r'alopecia', re.I)
MAL = re.compile(r'cancer|carcinoma|mesothelioma|neoplasm malignant|malignan|leukaemia|lymphoma|sarcoma|melanoma', re.I)
MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
# Talc sensitivity definitions (name-based only: product composition is not in the data).
NARROW = re.compile(r'talc|shower to shower|johnson.*powder|\bj (and )?j\b.*powder')
BROAD_RX = re.compile(r'ovarian|mesothelioma', re.I)


def section(t):
    print(f'\n## {t}')


def pc(k, n):
    return f'{k}/{n} ({100 * k / n:.1f}%)' if n else f'{k}/0'


def parse_event(s, received):
    """(year, month or None, day or None). 'dd-Mon-yy' is a full date; 'yy-Mon' and 'Mon-yy' are month precision."""
    if not s:
        return None
    full = lambda yy: 2000 + yy if 2000 + yy <= int(received[:4]) else 1900 + yy
    m = re.fullmatch(r'(\d\d)-([A-Za-z]{3})-(\d\d)', s)
    if m:
        return full(int(m[3])), MONTHS[m[2].lower()], int(m[1])
    m = re.fullmatch(r'(\d\d)-([A-Za-z]{3})', s) or None
    if m:
        return full(int(m[1])), MONTHS[m[2].lower()], None
    m = re.fullmatch(r'([A-Za-z]{3})-(\d\d)', s)
    if m:
        return full(int(m[2])), MONTHS[m[1].lower()], None
    m = re.fullmatch(r'(\d{4})', s)
    return (int(m[1]), None, None) if m else None


def two_by_two(rows, exposed, outcome):
    a = b = c = d = 0
    for r in rows:
        e, o = exposed(r), outcome(r)
        a += e and o
        b += e and not o
        c += (not e) and o
        d += (not e) and not o
    return a, b, c, d


def ror_ci(a, b, c, d):
    if min(a, b, c, d) == 0:
        a, b, c, d = (v + .5 for v in (a, b, c, d))
    lr = math.log(a) + math.log(d) - math.log(b) - math.log(c)
    se = (1 / a + 1 / b + 1 / c + 1 / d) ** .5
    return math.exp(lr), math.exp(lr - 1.96 * se), math.exp(lr + 1.96 * se), lr, se


def show(label, rows, exposed=lambda r: 'hair_care' in r['cats'], outcome=lambda r: r['hl']):
    t = two_by_two(rows, exposed, outcome)
    est, lo, hi, lr, se = ror_ci(*t)
    print(f'{label:<62} a={t[0]:<5} b={t[1]:<5} c={t[2]:<5} d={t[3]:<6} ROR {est:6.2f} ({lo:.2f}-{hi:.2f})')
    return lr, se


def interaction(name, x, y):
    z = (x[0] - y[0]) / (x[1] ** 2 + y[1] ** 2) ** .5
    print(f'  Interaction {name}: ratio of RORs {math.exp(x[0] - y[0]):.2f} '
          f'(95% CI {math.exp(x[0] - y[0] - 1.96 * (x[1] ** 2 + y[1] ** 2) ** .5):.2f}-{math.exp(x[0] - y[0] + 1.96 * (x[1] ** 2 + y[1] ** 2) ** .5):.2f}), '
          f'z={z:.2f}, two-sided p={math.erfc(abs(z) / 2 ** .5):.3f}')


with zipfile.ZipFile(P.ZIP) as z:
    reports = json.loads(z.read(z.namelist()[0]))['results']
rows = []
for x in reports:
    names = sorted({P.norm(q.get('product_name', '')) for q in x.get('products', []) if q.get('role') != 'Concomitant'})
    pts = x.get('reactions', [])
    ev = parse_event(x.get('event_date'), x['initial_received_date'])
    rows.append(dict(x=x, names=names, cats={P.classify(n) for n in names}, brands={P.brand(n) for n in names} - {''}, pts=pts,
                     hl=any(HL.search(t) for t in pts), alo=any(ALOPECIA.search(t) for t in pts), type=x['report_type'],
                     date=x['initial_received_date'], year=x['initial_received_date'][:4], after=x['initial_received_date'] >= MOCRA, ev=ev))
for r in rows:
    r['group'] = P.brand_group(r['brands'])
    r['talc'] = 'talc_powder' in r['cats']
bg = [r for r in rows if not r['talc']]
hair = [r for r in bg if r['cats'] & HAIR]
U = [r for r in bg if 'hair_care' in r['cats'] and r['hl']]
post = [r for r in U if r['after']]
pre = [r for r in U if not r['after']]
EXP = 'Expedited (15-Day)'

section('1. Report classification and timing')
print('total', len(rows), '| types', C(r['type'] for r in rows).most_common())
for t in ('Pre-MoCRA', 'Direct', EXP, 'Non-Expedited (Periodic)'):
    s = [r for r in rows if r['type'] == t]
    print(f"{t}: first receipt {min(r['date'] for r in s)}-{max(r['date'] for r in s)}; latest {min(r['x']['latest_received_date'] for r in s)}-"
          f"{max(r['x']['latest_received_date'] for r in s)}; version 1 {pc(sum(r['x']['report_version'] == '1' for r in s), len(s))}; "
          f"legacy id {pc(sum('legacy_report_id' in r['x'] for r in s), len(s))}; "
          f"id shape {C(re.sub(r'[0-9]', '9', r['x'].get('legacy_report_id', 'none'))[:9] for r in s).most_common(3)}")
print('Earliest version-1 expedited receipt', min(r['date'] for r in rows if r['type'] == EXP and r['x']['report_version'] == '1'))
early = [r for r in rows if r['type'] == EXP and not r['after']]
print('Expedited dated before MoCRA', len(early), '| version 1:', sum(r['x']['report_version'] == '1' for r in early), '| talc:', sum(r['talc'] for r in early),
      '| latest receipt min', min(r['x']['latest_received_date'] for r in early))
print('Direct reports per year 2021-2025', [(y, sum(r['type'] == 'Direct' and r['year'] == y for r in rows)) for y in '2021 2022 2023 2024 2025'.split()])
print('Hair-care hair-loss by period and type', sorted(C(('after' if r['after'] else 'before', r['type']) for r in U).items()))

section('2. Event date versus FDA receipt (hair-care hair-loss reports)')
print('Event-date shapes (all)', C(re.sub(r'[A-Za-z]+', 'M', re.sub(r'\d', '9', r['x'].get('event_date', 'none'))) for r in rows).most_common())
print('yy-Mon first field max (a value above 31 proves it is a year):', max(int(r['x']['event_date'][:2]) for r in rows if re.fullmatch(r'\d\d-[A-Za-z]{3}', r['x'].get('event_date', ''))))
print('Parsed event after receipt (impossible, parser check):', sum(r['ev'] is not None and r['ev'][0] > int(r['year']) for r in rows))


def lag_months(r):
    y, m, _ = r['ev']
    return None if m is None else (int(r['date'][:4]) - y) * 12 + int(r['date'][4:6]) - m


def describe(label, s):
    d = [r for r in s if r['ev']]
    lags = [v for v in map(lag_months, d) if v is not None]
    before = [r for r in d if r['ev'][0] <= 2023]
    q = statistics.quantiles(lags, n=4) if len(lags) > 3 else [None] * 3
    print(f"{label:<34} n={len(s):<5} event date present {pc(len(d), len(s))}; event in 2023 or earlier {pc(len(before), len(d))}; "
          f'lag months median {q[1]} IQR {q[0]}-{q[2]}; >12 mo {pc(sum(v > 12 for v in lags), len(lags))}')


lor = [r for r in post if r['group'] == 'loreal_group']
non = [r for r in post if r['group'] != 'loreal_group']
print("Later hair-care hair-loss naming any L'Oreal brand", sum(bool(r['brands'] & P.LOREAL) for r in post), '| Figure 1 group', len(lor), '| reassigned to a surge group', [(r['group'], r['names'][:3]) for r in post if r['brands'] & P.LOREAL and r['group'] != 'loreal_group'][:6])
for label, s in (('Before MoCRA', pre), ('After MoCRA', post), ('After: L\'Oreal group', lor), ('After: other', non),
                 ('After: expedited', [r for r in post if r['type'] == EXP]), ('After: direct', [r for r in post if r['type'] == 'Direct'])):
    describe(label, s)
print('After-MoCRA event years', sorted(C(r['ev'][0] for r in post if r['ev']).items()))

section('3. Seriousness and hair-loss terms')
print('Hair-loss PTs in hair-care reports', C(t for r in U for t in set(r['pts']) if HL.search(t)).most_common())
print('Alopecia-term reports', pc(sum(r['alo'] for r in U), len(U)))
for label, s in (('Before', pre), ('After', post), ('After expedited', [r for r in post if r['type'] == EXP]), ('After direct', [r for r in post if r['type'] == 'Direct'])):
    o = C(k for r in s for k in (r['x'].get('outcomes') or ['(none)']))
    print(label, len(s), [(k, pc(v, len(s))) for k, v in o.most_common()])

section('4. Disproportionality: complete 2x2 tables (a exposed+event, b exposed, c other+event, d other)')
main = show('PRIMARY within hair products, all years', hair)
show('All non-talc reports, all years', bg)
show('Non-hair cosmetics as comparator', [r for r in bg if 'hair_care' in r['cats'] or not r['cats'] & HAIR])
show('Skin care only as comparator', [r for r in bg if 'hair_care' in r['cats'] or 'skin_care' in r['cats']])
a = show('Within hair, received before Dec 29 2023', [r for r in hair if not r['after']])
b = show('Within hair, received on/after Dec 29 2023', [r for r in hair if r['after']])
interaction('before vs after (within hair)', a, b)
a2 = show('All non-talc, before', [r for r in bg if not r['after']])
b2 = show('All non-talc, after', [r for r in bg if r['after']])
interaction('before vs after (all non-talc)', a2, b2)
d = show('Within hair, Direct reports only', [r for r in hair if r['type'] == 'Direct'])
e = show('Within hair, Expedited reports only', [r for r in hair if r['type'] == EXP])
interaction('direct vs expedited (within hair)', d, e)
section('5. Sensitivity (all within hair products unless stated)')
surge = {'wen', 'monat', 'devacurl', 'olaplex'}
show('Alopecia terms only (no hypotrichosis, madarosis)', hair, outcome=lambda r: r['alo'])
show('Excluding WEN', [r for r in hair if 'wen' not in r['brands']])
show('Excluding WEN, Monat, DevaCurl, Olaplex', [r for r in hair if not r['brands'] & surge])
named_lor = lambda r: bool(r['brands'] & P.LOREAL)  # any L'Oreal brand named, not the priority group used for Figure 1
show("Excluding L'Oreal brands, all years", [r for r in hair if not named_lor(r)])
show("Excluding L'Oreal brands, after only", [r for r in hair if not named_lor(r) and r['after']])
show("Excluding surge and L'Oreal brands", [r for r in hair if not r['brands'] & surge and not named_lor(r)])
print('Overlap in hair universe: surge', sum(bool(r['brands'] & surge) for r in hair), "| L'Oreal", sum(named_lor(r) for r in hair), '| both', sum(bool(r['brands'] & surge) and named_lor(r) for r in hair),
      "| comparator reports naming surge", sum(bool(r['brands'] & surge) and 'hair_care' not in r['cats'] for r in hair), "| comparator naming L'Oreal", sum(named_lor(r) and 'hair_care' not in r['cats'] for r in hair))
print("Comparator L'Oreal names", C(n for r in hair if named_lor(r) and 'hair_care' not in r['cats'] for n in r['names']).most_common(8))
hc_only = lambda r, bad: 'hair_care' in r['cats'] and bad(r)
show('Surge brands removed from hair-care side only', [r for r in hair if not hc_only(r, lambda q: q['brands'] & surge)])
show("L'Oreal removed from hair-care side only", [r for r in hair if not hc_only(r, named_lor)])
show("Both removed from hair-care side only", [r for r in hair if not hc_only(r, lambda q: q['brands'] & surge or named_lor(q))])
show('Hair-care names with shampoo or conditioner only', [r for r in hair if 'hair_care' not in r['cats'] or any(re.search('shampoo|conditioner|cleansing', n) for n in r['names'])])
show('Excluding surge brands, before only', [r for r in hair if not r['brands'] & surge and not r['after']])
show('Excluding reports naming 2+ hair categories', [r for r in hair if len(r['cats'] & HAIR) == 1])
show('Single suspect product reports only', [r for r in hair if len(r['names']) == 1])
seen, ded = set(), []
for r in sorted(bg, key=lambda r: r['date']):
    key = (tuple(r['names']), frozenset(r['pts']), r['x'].get('patient', {}).get('age'), r['x'].get('patient', {}).get('gender'), r['x'].get('event_date'))
    if r['x'].get('event_date') and key in seen:
        continue
    seen.add(key)
    ded.append(r)
print('Non-talc after strict de-duplication', len(ded), '(removed', len(bg) - len(ded), ')')
show('De-duplicated', [r for r in ded if r['cats'] & HAIR])
show('De-duplicated, before', [r for r in ded if r['cats'] & HAIR and not r['after']])
show('De-duplicated, after', [r for r in ded if r['cats'] & HAIR and r['after']])

# Looser rule: the strict one needs an event date, which most recent reports lack, so it cannot see duplicates among them.
seen, loose = set(), []
for r in sorted(bg, key=lambda r: r['date']):
    key = (tuple(r['names']), frozenset(r['pts']), r['x'].get('patient', {}).get('age'), r['x'].get('patient', {}).get('gender'), r['x'].get('event_date'))
    if key in seen:
        continue
    seen.add(key)
    loose.append(r)
print('Non-talc after looser de-duplication (missing event date allowed to match)', len(loose), '(removed', len(bg) - len(loose), ')')
lh = [r for r in loose if r['cats'] & HAIR]
la = show('Loose de-duplication', lh)
lb = show('Loose de-duplication, before', [r for r in lh if not r['after']])
lc = show('Loose de-duplication, after', [r for r in lh if r['after']])
interaction('before vs after (loose de-duplication)', lb, lc)
lpost = [r for r in loose if 'hair_care' in r['cats'] and r['hl'] and r['after']]
print('Loose: after hair-care hair-loss', len(lpost), "| L'Oreal group", pc(sum(r['group'] == 'loreal_group' for r in lpost), len(lpost)), '| expedited', pc(sum(r['type'] == EXP for r in lpost), len(lpost)))

# Why 69 and 360 differ: tiers from strictest to loosest, with what each match actually rests on.
keyf = lambda r: (tuple(r['names']), frozenset(r['pts']), r['x'].get('patient', {}).get('age'), r['x'].get('patient', {}).get('gender'), r['x'].get('event_date'))
gr = collections.defaultdict(list)
for r in sorted(bg, key=lambda r: (r['date'], r['x']['report_number'])):
    gr[keyf(r)].append(r)
extra = [(k, r, g[0]) for k, g in gr.items() for r in g[1:]]
print('Report numbers unique:', len({r['x']['report_number'] for r in rows}) == len(rows))
print('Extras under loosest rule', len(extra), '| hair universe', sum(bool(r['cats'] & HAIR) for _, r, _ in extra), '| hair-care hair-loss', sum('hair_care' in r['cats'] and r['hl'] for _, r, _ in extra))
print('By fields present (date,age,sex):', sorted(C((bool(k[4]), bool(k[2]), bool(k[3])) for k, _, _ in extra).items(), reverse=True))
print('Same first-receipt day as kept report', pc(sum(r['date'] == q['date'] for _, r, q in extra), len(extra)), '| same report type', pc(sum(r['type'] == q['type'] for _, r, q in extra), len(extra)))
print('Single product and single reaction', pc(sum(len(r['names']) == 1 and len(set(r['pts'])) == 1 for _, r, _ in extra), len(extra)))
tiers = {'T1 age+sex+date all present': lambda k, r, q: k[2] and k[3] and k[4], 'T2 event date present (V1 rule)': lambda k, r, q: k[4],
         'T3 same receipt day or T2': lambda k, r, q: k[4] or r['date'] == q['date'], 'T4 all identical incl missing (V2 table rule)': lambda k, r, q: True}
for name, f in tiers.items():
    drop = {r['x']['report_number'] for k, r, q in extra if f(k, r, q)}
    keep = [r for r in hair if r['x']['report_number'] not in drop]
    post_k = [r for r in U if r['after'] and r['x']['report_number'] not in drop]
    print(f"{name}: removed non-talc {len(drop)}; from hair universe {len(hair) - len(keep)}; later hair-care hair-loss {len(post_k)}; L'Oreal {pc(sum(r['group'] == 'loreal_group' for r in post_k), len(post_k))}")
    show('  within hair', keep)

section('6. Talc and powder definition')
named = lambda r, rx: any(rx.search(n) for n in r['names'])
powder = re.compile(r'powder|talc|asbestos|shower to shower')
defs = {'narrow (talc or J&J powder named)': lambda r: named(r, NARROW), 'current dictionary': lambda r: r['talc'],
        'broad (any powder, or ovarian/mesothelioma reaction)': lambda r: r['talc'] or named(r, powder) or any(BROAD_RX.search(t) for t in r['pts'])}
mand = [r for r in rows if r['type'] == EXP]
for k, f in defs.items():
    keep = [r for r in rows if not f(r)]
    print(f"{k}: mandatory share {pc(sum(f(r) for r in mand), len(mand))}; all reports {pc(sum(f(r) for r in rows), len(rows))}; comparison pool {len(keep)}")
    show('  within hair', [r for r in keep if r['cats'] & HAIR])
    show('  all remaining', keep)
tm = [r for r in mand if r['talc']]
print('Talc mandatory: malignancy', pc(sum(any(MAL.search(t) for t in r['pts']) for r in tm), len(tm)), '| ovarian', pc(sum(any('ovarian' in t.lower() for t in r['pts']) for r in tm), len(tm)),
      '| mesothelioma', pc(sum(any('mesothelioma' in t.lower() for t in r['pts']) for r in tm), len(tm)), '| name says talc', pc(sum(any('talc' in n for n in r['names']) for r in tm), len(tm)),
      '| J&J or Shower to Shower', pc(sum(named(r, NARROW) for r in tm), len(tm)))
describe('Talc mandatory reports', tm)
full = [r for r in tm if re.fullmatch(r'\d\d-[A-Za-z]{3}-\d\d', r['x'].get('event_date', ''))]
ym = [r for r in tm if re.fullmatch(r'\d\d-[A-Za-z]{3}', r['x'].get('event_date', ''))]
print('Talc lag: all month-level', len([1 for r in tm if r['ev'] and r['ev'][1]]), 'median', statistics.median(v for v in (lag_months(r) for r in tm if r['ev']) if v is not None),
      '| full dates only', len(full), 'median', statistics.median(lag_months(r) for r in full), '| yy-Mon only', len(ym), 'median', statistics.median(lag_months(r) for r in ym))
fields = C(int(r['x']['event_date'][:2]) for r in rows if re.fullmatch(r'\d\d-[A-Za-z]{3}', r['x'].get('event_date', '')))
print('yy-Mon first field: count', sum(fields.values()), '| values 26-31', sum(v for k, v in fields.items() if k >= 26), '| range', min(fields), max(fields))
describe('Non-talc mandatory reports', [r for r in mand if not r['talc']])
print('Dictionary talc class not in narrow definition', C(n for r in rows for n in r['names'] if P.classify(n) == 'talc_powder' and not NARROW.search(n)).most_common(15))
print('Hair-loss reports excluded as talc', sum(r['talc'] and r['hl'] for r in rows), '| hair-care reports excluded as talc', sum(r['talc'] and bool(r['cats'] & HAIR) for r in rows))

section("7. L'Oreal-group concentration, reports received after Dec 29 2023")
print('Hair-care hair-loss after', len(post), "| L'Oreal group", pc(len(lor), len(post)), '| mandatory', pc(sum(r['type'] == EXP for r in lor), len(lor)))
print('By brand', C(b for r in lor for b in r['brands'] & P.LOREAL).most_common())
print('Top products', C(n for r in lor for n in r['names'] if P.brand(n) in P.LOREAL).most_common(15))
print('Other groups', C(r['group'] for r in non).most_common())
print('Receipt month counts', sorted(C(r['date'][:6] for r in lor).items()))
days = C(r['date'] for r in lor)
print('Receipt days', len(days), '| busiest', days.most_common(6))
print('Report versions', C(r['x']['report_version'] for r in lor).most_common())
same = C((tuple(r['names']), frozenset(r['pts']), r['x'].get('patient', {}).get('age'), r['x'].get('patient', {}).get('gender'), r['x'].get('event_date')) for r in lor)
print('Identical product+reaction+age+sex+event-date groups (size>1)', sum(v > 1 for v in same.values()), 'covering', sum(v for v in same.values() if v > 1), 'reports; with event date only', sum(v > 1 and k[4] is not None for k, v in same.items()))
samep = C((tuple(r['names']), r['x'].get('event_date')) for r in lor if r['x'].get('event_date'))
print('Same product list and event date', sum(v > 1 for v in samep.values()), 'groups covering', sum(v for v in samep.values() if v > 1), 'reports')
print('Same product list only: distinct lists', len(C(tuple(r['names']) for r in lor)), '| largest', C(tuple(r['names']) for r in lor).most_common(3))
print('Suspect products per report', sorted(C(len(r['names']) for r in lor).items()))
ages = [float(r['x']['patient']['age']) for r in lor if r['x'].get('patient', {}).get('age') and r['x']['patient'].get('age_unit') == 'Year(s)']
print('Age known', pc(len(ages), len(lor)), 'median', statistics.median(ages) if ages else None, '| female', pc(sum(r['x'].get('patient', {}).get('gender') == 'Female' for r in lor), len(lor)))
allpost = [r for r in bg if r['after'] and r['group'] == 'loreal_group']
print("All non-talc reports naming L'Oreal group after", len(allpost), '| hair loss', pc(sum(r['hl'] for r in allpost), len(allpost)), '| hair-care category', pc(sum('hair_care' in r['cats'] for r in allpost), len(allpost)))
print("Same brands before MoCRA: hair-care hair-loss", sum(r['group'] == 'loreal_group' for r in pre), '| all non-talc', sum(r['group'] == 'loreal_group' and not r['after'] for r in bg))
mn = [r for r in bg if r['type'] == EXP]
print("Non-talc expedited reports", len(mn), "| L'Oreal group", pc(sum(r['group'] == 'loreal_group' for r in mn), len(mn)), '| first word of product names', C(n.split()[0] for r in mn for n in r['names'] if n).most_common(20))

section('8. Demographic completeness (hair-care hair-loss reports)')
for label, s in (('Before', pre), ('After', post)):
    print(label, len(s), 'age missing', pc(sum(not r['x'].get('patient', {}).get('age') for r in s), len(s)), '| sex missing', pc(sum(not r['x'].get('patient', {}).get('gender') for r in s), len(s)),
          '| female', pc(sum(r['x'].get('patient', {}).get('gender') == 'Female' for r in s), len(s)), '| event date missing', pc(sum(not r['x'].get('event_date') for r in s), len(s)))

section('9. Classifier audit')
mentions = C(n for r in rows for n in r['names'])
multi = {n: [k for k, p in P.CATEGORIES if p.search(n)] for n in mentions}
coll = {n: v for n, v in multi.items() if len(v) > 1}
print('Distinct names', len(mentions), '| matching 2+ categories', len(coll), '| mentions affected', sum(mentions[n] for n in coll), 'of', sum(mentions.values()))
print('Top collisions (winner first)', C(tuple(v[:2]) for n, v in coll.items() for _ in range(mentions[n])).most_common(12))
hc = [n for n in mentions if P.classify(n) == 'hair_care']
print('Hair-care names', len(hc), '| also skin/lip/nail words', [(n, mentions[n]) for n in sorted(hc, key=lambda n: -mentions[n]) if re.search(r'\b(lotion|body|face|facial|lip|nail|skin|eye)\b', n)][:12])
print('Non-talc hair-loss reports with no hair category', len([r for r in bg if r['hl'] and not r['cats'] & HAIR]), C(n for r in bg if r['hl'] and not r['cats'] & HAIR for n in r['names']).most_common(20))
print('Reports with 2+ categories (non-talc)', pc(sum(len(r['cats']) > 1 for r in bg), len(bg)), '| 2+ hair categories', sum(len(r['cats'] & HAIR) > 1 for r in bg))

# Stratified blind sheet for the human reviewer: the assigned label stays out of the file.
strata = {'hair_care': 80, 'hair_dye_bleach': 50, 'hair_relaxer_straightener': 40, 'unclassified': 30, 'skin_care': 20}
pool = sorted(n for r in bg for n in r['names'])
pool = sorted(set(pool))
rng = random.Random(2026)
sample = [n for k, v in strata.items() for n in rng.sample([n for n in pool if P.classify(n) == k and n not in coll], v)]
sample += rng.sample(sorted(n for n in pool if n in coll), 30)
rng.shuffle(sample)
assert len(sample) == len(set(sample)) == 250
with open(os.path.join(P.OUT, 'validation_stratified_for_reviewer.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['product_name', 'reviewer_category'])
    w.writerows([n, ''] for n in sample)
print('Stratified reviewer sheet written:', len(sample), 'names;', C(P.classify(n) for n in sample).most_common())

section('10. Cross-check of pipeline table4_ror.csv against brute-force recount')
bad, pvals = 0, []
with open(os.path.join(P.OUT, 'table4_ror.csv')) as f:
    t4 = list(csv.DictReader(f))
grp = dict((k, p) for k, p in P.REACTIONS)
for row in t4:
    t = two_by_two(bg, lambda r: row['category'] in r['cats'], lambda r: any(grp[row['reaction_group']].search(x.lower()) for x in r['pts']))
    bad += t != tuple(int(row[k]) for k in 'abcd')
    est, lo, hi, lr, se = ror_ci(*t)
    bad += abs(est / float(row['ror']) - 1) > .01
    pvals.append(math.erfc(abs(lr / se) / 2 ** .5))
m = len(pvals)
order = sorted(range(m), key=lambda i: pvals[i])
qs, run = [0] * m, 1.0
for rank in range(m, 0, -1):
    run = min(run, pvals[order[rank - 1]] * m / rank)
    qs[order[rank - 1]] = run
bad_q = sum(abs(q - float(r['q_bh'])) > max(1e-300, .01 * q) for q, r in zip(qs, t4))
print(f'{len(t4)} ROR rows rechecked; mismatches {bad}; BH q mismatches {bad_q}; signals {sum(r["signal"] == "True" for r in t4)}')

section("11. L'Oreal record audit (hair-care hair-loss reports received on or after Dec 29 2023)")
with open(os.path.join(P.OUT, 'loreal_reports_audit.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['report_number', 'first_received', 'report_type', 'version', 'event_date', 'age', 'sex', 'loreal_brands', 'loreal_products', 'other_products', 'reactions'])
    for r in sorted(lor, key=lambda r: r['date']):
        pt = r['x'].get('patient', {})
        w.writerow([r['x']['report_number'], r['date'], r['type'], r['x']['report_version'], r['x'].get('event_date', ''), pt.get('age', ''), pt.get('gender', ''), '; '.join(sorted(r['brands'] & P.LOREAL)),
                    '; '.join(n for n in r['names'] if P.brand(n) in P.LOREAL), '; '.join(n for n in r['names'] if P.brand(n) not in P.LOREAL), '; '.join(sorted(set(r['pts'])))])
print('Written', len(lor), 'rows to out/loreal_reports_audit.csv')
names = C(n for r in lor for n in r['names'] if P.brand(n) in P.LOREAL)
print('Distinct L\'Oreal product names', len(names), '| reports with top product', names.most_common(1), f'= {100 * names.most_common(1)[0][1] / len(lor):.1f}%', '| top 5 names cover (mentions)', sum(v for _, v in names.most_common(5)), 'of', sum(names.values()))
print('Distinct names by brand key', sorted(C(P.brand(n) for n in names).items()))
for b in ('loreal', 'biolage_matrix', 'garnier', 'loreal_other'):
    print(b, [(n, v) for n, v in names.most_common() if P.brand(n) == b][:14])
print('Non-L\'Oreal suspect products in these reports', C(n for r in lor for n in r['names'] if P.brand(n) not in P.LOREAL).most_common(10))
brand_only = C()
for r in lor:
    brand_only['+'.join(sorted(r['brands'] & P.LOREAL))] += 1
print('Reports by exact brand combination', brand_only.most_common(10))
excl = C(sorted(r['brands'] & P.LOREAL)[0] if len(r['brands'] & P.LOREAL) == 1 else 'multiple' for r in lor)
print('Exclusive brand assignment', excl.most_common())
days = C(r['date'] for r in lor)
print('Top 5 receipt days cover', sum(v for _, v in days.most_common(5)), 'of', len(lor), f'({100 * sum(v for _, v in days.most_common(5)) / len(lor):.1f}%)')
for d, _ in days.most_common(4):
    print(' ', d, C(b for r in lor if r['date'] == d for b in r['brands'] & P.LOREAL).most_common(5))
print('By brand and half-year', sorted(C((r['date'][:4] + ('H1' if r['date'][4:6] <= '06' else 'H2'), b) for r in lor for b in r['brands'] & P.LOREAL).items()))
idg = collections.defaultdict(list)
for r in lor:
    idg[keyf(r)].append(r)
big = [g for g in idg.values() if len(g) > 1]
print('Identical groups', len(big), 'covering', sum(map(len, big)), '| same receipt day within group', sum(len({r['date'] for r in g}) == 1 for g in big), '| spans different days', sum(len({r['date'] for r in g}) > 1 for g in big), '| largest', sorted(map(len, big))[-3:])
print('  fields in those groups (age,sex,date):', C((bool(k[2]), bool(k[3]), bool(k[4])) for k, g in idg.items() if len(g) > 1).most_common())
print('  example', [(k[0][:2], sorted(k[1])[:2], k[2], k[3], len(g), sorted({r['date'] for r in g})[:3]) for k, g in sorted(idg.items(), key=lambda kv: -len(kv[1]))[:2]])
others = [r for r in post if r['group'] == 'other']
print('Possible missed L\'Oreal products in other group', [(n, c) for n, c in C(n for r in others for n in r['names']).most_common() if re.search(r'kiehl|dercos|vichy|aesop|carol|softsheen|carson|mizani|wow|pulp|oreal|lancome|roche|acidic|genesis|hydrate|bain|resistance|densifique|nutritive|all soft|extreme length|strength cure', n)][:15])
print('Other group top products', C(n for r in others for n in r['names']).most_common(15))

section('12. Missing data by report stream')
def miss(label, s):
    if not s:
        return
    a = sum(not r['x'].get('patient', {}).get('age') for r in s); g = sum(not r['x'].get('patient', {}).get('gender') for r in s); e = sum(not r['x'].get('event_date') for r in s); o = sum(not r['x'].get('outcomes') for r in s)
    print(f'{label:<46} n={len(s):<6} age {100 * a / len(s):5.1f}%  sex {100 * g / len(s):5.1f}%  event date {100 * e / len(s):5.1f}%  outcome {100 * o / len(s):5.1f}%')
miss('Hair-care hair-loss, before: direct', [r for r in pre if r['type'] == 'Direct'])
miss('Hair-care hair-loss, before: pre-MoCRA', [r for r in pre if r['type'] == 'Pre-MoCRA'])
miss('Hair-care hair-loss, after: direct', [r for r in post if r['type'] == 'Direct'])
miss('Hair-care hair-loss, after: expedited', [r for r in post if r['type'] == EXP])
miss("  expedited, L'Oreal brands", [r for r in post if r['type'] == EXP and r['group'] == 'loreal_group'])
miss('  expedited, Olaplex', [r for r in post if r['type'] == EXP and r['group'] == 'olaplex'])
miss('  expedited, OGX', [r for r in post if r['type'] == EXP and r['group'] == 'ogx'])
miss('  expedited, other brands', [r for r in post if r['type'] == EXP and r['group'] == 'other'])
miss('All non-talc, after: direct', [r for r in bg if r['after'] and r['type'] == 'Direct'])
miss('All non-talc, after: expedited', [r for r in bg if r['after'] and r['type'] == EXP])
miss("  non-hair-loss expedited naming L'Oreal brands", [r for r in bg if r['after'] and r['type'] == EXP and r['group'] == 'loreal_group' and not r['hl']])
miss('  non-hair expedited (Nair, skin care, other)', [r for r in bg if r['after'] and r['type'] == EXP and not r['cats'] & HAIR])
miss('Talc expedited', [r for r in rows if r['talc'] and r['type'] == EXP])
print('Raw values: gender', C(r['x'].get('patient', {}).get('gender', '(absent)') for r in post).most_common(), '| age_unit', C(r['x'].get('patient', {}).get('age_unit', '(absent)') for r in post).most_common())
print('Report has no patient block', sum('patient' not in r['x'] for r in post), 'after;', sum('patient' not in r['x'] for r in pre), 'before')
sex = lambda s: pc(sum(r['x'].get('patient', {}).get('gender') == 'Female' for r in s), sum(bool(r['x'].get('patient', {}).get('gender')) for r in s))
yrs = lambda s: [float(r['x']['patient']['age']) for r in s if r['x'].get('patient', {}).get('age') and r['x']['patient'].get('age_unit') == 'Year(s)']
print('Female among sex known: before', sex(pre), '| after', sex(post), '| median age known: before', statistics.median(yrs(pre)), f'(n={len(yrs(pre))})', '| after', statistics.median(yrs(post)), f'(n={len(yrs(post))})')
print('Version 1 share: before', pc(sum(r['x']['report_version'] == '1' for r in pre), len(pre)), '| after', pc(sum(r['x']['report_version'] == '1' for r in post), len(post)))
