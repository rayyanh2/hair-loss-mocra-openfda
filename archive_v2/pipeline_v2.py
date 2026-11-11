"""FDA cosmetic adverse event reports (openFDA bulk file): hair-loss / MoCRA pipeline.

Run:  python3 -I pipeline.py      (standard library only; writes CSVs to out/)
      python3 -I pipeline.py --dedup   (sensitivity analysis: drops candidate duplicates, writes to out_dedup/)
      python3 -I pipeline.py --kappa reviewed.csv   (Cohen's kappa: reviewer_category vs this script's category)
Edit CATEGORIES / BRANDS / REACTIONS to change the dictionaries, then re-run.
"""
import collections, csv, functools, json, math, os, re, sys, unicodedata, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.path.join(HERE, 'data', 'cosmetic-event.json.zip')
DEDUP = '--dedup' in sys.argv
OUT = os.path.join(HERE, 'out_dedup' if DEDUP else 'out')
URL = 'https://download.open.fda.gov/cosmetic/event/cosmetic-event-0001-of-0001.json.zip'
MANDATORY = {'Expedited (15-Day)'}  # MoCRA 15-business-day serious reports; the 6 'Non-Expedited (Periodic)' reports are not
MOCRA = '20231229'  # serious adverse event reporting enforceable from this receipt date

# Product categories, matched against norm(product_name). First match wins, so order matters.
CATEGORIES = [
    ('talc_powder', r'talc|shower to shower|baby powder|johnson.*powder|gold bond powder|^gold bond$|cashmere bouquet|asbestos'),
    ('tattoo_henna', r'tattoo|\bink\b|pigments?\b|black henna|microblad'),
    ('oral', r'tooth|mouth ?wash|teeth|whitening strip|dental|denture|listerine|breath strip'),
    ('supplement_or_drug', r'vitamin d\b|gumm|capsule|tablet|softgel|supplement|minoxidil|rogaine|fish oil|multivitamin|^biotin( \d+ ?(mcg|mg))?$'),
    ('depilatory', r'\bnair\b|\bveet\b|depilat|hair remov|sugar wax|wax strip|waxing'),
    ('hair_relaxer_straightener', r'relaxer|straighten|keratin (treatment|complex)|smoothing (kit|treatment)|brazill?ian blowout|texturi[sz]er|\bperm\b|permanent wave|\blye\b'),
    ('skin_lightening', r'skin (lighten|bleach|whiten)|lightening cream|bleaching cream|whitening cream|fade cream'),
    ('hair_dye_bleach', r'\bdye|hair colou?r|colou?rant|bleach|lightener|\bhenna|just for men|nutrisse|excellence|feria|bigen|clairol|natural instincts|root touch|permanent (colou?r|powder|creme|cream)|semi permanent'),
    ('eye_lash_brow', r'mascara|eye ?liner|\blash|\bbrows?\b|eyebrow|eye ?shadow|eye cream|eye makeup|revitalash|cils booster'),
    ('hair_care', r'shampoo|conditioner|\bpoo\b|\bhair|scalp|curl|co ?wash|leave ?in|bond (maintenance|smoother|building|repair)|bonding'
                  r'|\bwen\b|chaz dean|sixthirteen|monat|\bdeva|olaplex|redken|kerastase|pureology|\bogx\b|pantene|tress?em+e|biolage|\bmatrix\b'
                  r'|fructis|elvive|shu uemura|softsheen|amika|briogeo|living proof|it s a 10|maui moisture|cantu|aussie|keranique'),
    ('nail', r'\bnail|polish|lacquer|manicure|acrylic|cuticle'),
    ('lip', r'\blip|chapstick|\beos\b|carmex|blistex'),
    ('deodorant', r'deodorant|antiperspirant|old spice|^secret\b'),
    ('feminine_hygiene', r'feminine|vaginal|summer s eve|douche|intimate|\bvag|monistat'),
    ('fragrance', r'perfume|fragrance|cologne|eau de|body mist|body spray|parfum'),
    ('makeup', r'foundation|concealer|make ?up|blush|bronzer|primer|highlighter|\b[bc]c cream|powder'),
    ('skin_care', r'cream|lotion|moistur|serum|cleans|wash|soap|scrub|peel|retinol|vitamin c|sunscreen|\bspf\b|\boil\b|balm|butter|\bgel\b'
                  r'|toner|mask|wipe|jergens|neutrogena|cerave|cetaphil|aveeno|olay|lubriderm|nivea|vaseline|proactiv|\broc\b|\bdove\b'
                  r'|eucerin|\bskin|face|facial|body|huggies|diaper|bath|shav|aloe|\btan\b|tanning|\bbar\b'),
]
# Brands for the hair-loss timeline (any product naming the brand).
BRANDS = [
    ('wen', r'\bwen\b|chaz dean|sixthirteen'), ('monat', r'monat'), ('devacurl', r'\bdeva'),
    ('olaplex', r'olaplex|\bno ?\d+ bond|number \d+ bond'), ('redken', r'redken'), ('kerastase', r'kerastase'),
    ('pureology', r'pureology'), ('ogx', r'\bogx\b|organix'), ('loreal', r'\bl ?oreal'), ('garnier', r'garnier|fructis'),
    ('brazilian_blowout', r'brazill?ian blowout'), ('biolage_matrix', r'biolage|\bmatrix\b'), ('dove', r'\bdove\b'),
    ('loreal_other', r'shu uemura|mizani|color wow|pulp riot|dark (and )?lovely'),
]
# Figure 1 groups: single-brand surges first (in priority order), then L'Oréal Groupe hair brands (per loreal.com brand list, Oct 2026).
SURGE_BRANDS = ('wen', 'monat', 'devacurl', 'olaplex', 'ogx')
LOREAL = {'redken', 'kerastase', 'pureology', 'loreal', 'biolage_matrix', 'garnier', 'loreal_other'}
HAIR = {'hair_care', 'hair_dye_bleach', 'hair_relaxer_straightener'}
# Reaction groups, matched against lower-cased MedDRA preferred terms; a report can fall in several.
REACTIONS = [
    ('hair_loss', r'alopecia|hypotrichosis|madarosis|hair thinning'),
    ('hair_damage', r'trichorrhexis|trichoclasis|hair (texture|disorder|colour|color|growth|injury|breakage)'),
    ('burn', r'\bburns?\b'),  # chemical/thermal burns, not "burning sensation"
    ('allergic_dermatitis', r'dermatitis|eczema|hypersensitivity|urticaria|angioedema|anaphyla|sensiti[sz]ation'),
    ('irritation_rash', r'pruritus|rash|erythema|irritation|burning sensation|exfoliation|blister'),
    ('eye', r'\beye|ocular|conjunctiv|vision|lacrimation|eyelid|keratitis|blindness|corneal'),
    ('infection', r'infection|cellulitis|abscess|folliculitis'),
    ('malignancy', r'cancer|carcinoma|mesothelioma|neoplasm malignant|malignan|leukaemia|lymphoma|sarcoma|melanoma'),
]
CATEGORIES = [(k, re.compile(p)) for k, p in CATEGORIES]
BRANDS = [(k, re.compile(p)) for k, p in BRANDS]
REACTIONS = [(k, re.compile(p)) for k, p in REACTIONS]


def norm(name):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()).strip()


@functools.lru_cache(maxsize=None)
def classify(n):
    return next((k for k, p in CATEGORIES if p.search(n)), 'unclassified')


@functools.lru_cache(maxsize=None)
def brand(n):
    return next((k for k, p in BRANDS if p.search(n)), '')


def brand_group(brands):
    return next((b for b in SURGE_BRANDS if b in brands), 'loreal_group' if brands & LOREAL else 'other')


@functools.lru_cache(maxsize=None)
def groups(pt):
    return {k for k, p in REACTIONS if p.search(pt.lower())}


def ror(a, b, c, d):
    """Reporting odds ratio, 95% CI and two-sided Wald p."""
    if 0 in (a, b, c, d):
        a, b, c, d = a + .5, b + .5, c + .5, d + .5  # Haldane correction
    est, se = a * d / (b * c), math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    return est, est * math.exp(-1.96 * se), est * math.exp(1.96 * se), math.erfc(abs(math.log(est) / se) / math.sqrt(2))


def bh(ps):
    """Benjamini-Hochberg q-values."""
    q, prev = [0.0] * len(ps), 1.0
    for rank, i in zip(range(len(ps), 0, -1), sorted(range(len(ps)), key=ps.__getitem__, reverse=True)):
        prev = min(prev, ps[i] * len(ps) / rank)
        q[i] = prev
    return q


def kappa(pairs):
    """Cohen's kappa for (rater 1, rater 2) label pairs."""
    n = len(pairs)
    c1, c2 = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    po, pe = sum(a == b for a, b in pairs) / n, sum(c1[k] * c2[k] for k in c1) / n / n
    return (po - pe) / (1 - pe)


def ror_table(bg, cats):
    """ROR per category x reaction group against all other reports in bg, with BH q-values and a signal flag."""
    E = collections.Counter(c for r in bg for c in r['cats'])
    O = collections.Counter(g for r in bg for g in r['groups'])
    J = collections.Counter((c, g) for r in bg for c in r['cats'] for g in r['groups'])
    t = []
    for c in cats:
        for g, _ in REACTIONS:
            a = J[c, g]
            b, cc, d = E[c] - a, O[g] - a, len(bg) - E[c] - O[g] + a
            t.append([c, g, a, b, cc, d, *ror(a, b, cc, d)])
    for row, q in zip(t, bh([row[-1] for row in t])):
        row += [q, row[2] >= 3 and row[7] > 1 and q < .05]
    return t


def write_ror(name, t):
    write(name, ['category', 'reaction_group', 'a', 'b', 'c', 'd', 'ror', 'ci_low', 'ci_high', 'p', 'q_bh', 'signal'],
          [[*r[:6], *(f'{v:.3g}' for v in r[6:11]), r[11]] for r in t])


def figure1_svg(t6):
    """Figure 1 for print (vector, Arial, 7 x 5 in): A, hair-care hair-loss reports by year stacked by brand group;
    B, share of each year's reports that were mandatory (expedited) reports."""
    n, mand = collections.Counter(), collections.Counter()
    for (y, g, t), c in t6.items():
        if y >= '2004':
            n[int(y), g] += c
            mand[int(y)] += c * (t in MANDATORY)
    years = range(2004, max(y for y, _ in n) + 1)
    peak = {g: max(years, key=lambda y: n[y, g]) for _, g in n}
    order = sorted(peak, key=lambda g: -1 if g == 'loreal_group' else 9999 if g == 'other' else peak[g])
    W, H, L, R, T, B, T2, B2 = 504, 360, 44, 496, 40, 216, 250, 326  # page, panel A and panel B boxes, in points
    tot = lambda y: sum(n[y, g] for g in order)
    ymax = math.ceil(max(map(tot, years)) / 400) * 400
    band = (R - L) / len(years)
    bw = band * .7
    X = lambda y: L + (y - years[0]) * band + (band - bw) / 2
    Y = lambda v: B - v / ymax * (B - T)
    fill = {'loreal_group': '#2f6db5', 'other': '#d9d9d9'}
    label = {'wen': 'WEN', 'monat': 'Monat', 'devacurl': 'DevaCurl', 'olaplex': 'Olaplex', 'ogx': 'OGX'}
    mx = L + (2024 - years[0]) * band
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}pt" height="{H}pt" viewBox="0 0 {W} {H}" font-family="Arial" font-size="8">',
         f'<rect x="{mx:.1f}" y="{T}" width="{R - mx:.1f}" height="{B2 - T}" fill="#efefef"/>',
         f'<text x="0" y="{T - 6}" font-size="10" font-weight="bold">A</text><text x="0" y="{T2 - 6}" font-size="10" font-weight="bold">B</text>',
         f'<text x="{(mx + R) / 2:.1f}" y="{T + 10}" text-anchor="middle">Mandatory</text>'
         f'<text x="{(mx + R) / 2:.1f}" y="{T + 19}" text-anchor="middle">reporting</text>'
         f'<text x="{(mx + R) / 2:.1f}" y="{T + 31}" text-anchor="middle" font-size="6.5">2025: Jan-Aug</text>']
    for v in range(0, ymax + 1, 400):
        s.append(f'<line x1="{L}" x2="{R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#bfbfbf" stroke-width=".5"/>'
                 f'<text x="{L - 4}" y="{Y(v) + 3:.1f}" text-anchor="end">{v:,}</text>')
    for y in years:
        base = 0
        for g in order:
            if n[y, g]:
                s.append(f'<rect x="{X(y):.1f}" y="{Y(base + n[y, g]):.1f}" width="{bw:.1f}" height="{Y(base) - Y(base + n[y, g]):.1f}" '
                         f'fill="{fill.get(g, "#7f7f7f")}" stroke="white" stroke-width=".4"/>')
                base += n[y, g]
        if y % 2 == 0:
            s.append(f'<text x="{X(y) + bw / 2:.1f}" y="{B2 + 11}" text-anchor="middle">{y}</text>')
    for g in [g for g in label if g in peak]:
        x, top = X(peak[g]) + bw / 2, Y(tot(peak[g]))
        s.append(f'<text x="{x:.1f}" y="{top - 12:.1f}" text-anchor="middle">{label[g]}</text>'
                 f'<text x="{x:.1f}" y="{top - 3:.1f}" text-anchor="middle" font-weight="bold">{n[peak[g], g]:,}</text>')
    for i, (color, text) in enumerate([('#2f6db5', 'L’Oréal-owned brands'), ('#7f7f7f', 'Single brands, labeled at peak'),
                                       ('#d9d9d9', 'Other hair-care products')]):
        s.append(f'<rect x="{L + i * 150}" y="8" width="8" height="8" fill="{color}"/><text x="{L + i * 150 + 12}" y="15">{text}</text>')
    # Panel B: share of each year's reports filed as mandatory reports.
    Y2 = lambda p: B2 - p / 100 * (B2 - T2)
    share = {y: 100 * mand[y] / tot(y) for y in years}
    for p in (0, 50, 100):
        s.append(f'<line x1="{L}" x2="{R}" y1="{Y2(p):.1f}" y2="{Y2(p):.1f}" stroke="#bfbfbf" stroke-width=".5"/>'
                 f'<text x="{L - 4}" y="{Y2(p) + 3:.1f}" text-anchor="end">{p}%</text>')
    pts = ' '.join(f'{X(y) + bw / 2:.1f},{Y2(share[y]):.1f}' for y in years)
    s.append(f'<polyline points="{pts}" fill="none" stroke="black" stroke-width="1"/>')
    for y in years:
        s.append(f'<circle cx="{X(y) + bw / 2:.1f}" cy="{Y2(share[y]):.1f}" r="1.8"/>')
        if share[y]:
            s.append(f'<text x="{X(y) + bw / 2:.1f}" y="{Y2(share[y]) - 4:.1f}" text-anchor="middle" font-weight="bold">{share[y]:.0f}%</text>')
    s += [f'<text x="{L}" y="{T2 - 6}">Reports labeled expedited, % of each year</text>',
          f'<line x1="{L}" x2="{R}" y1="{B}" y2="{B}" stroke="black" stroke-width=".6"/>',
          f'<line x1="{L}" x2="{R}" y1="{B2}" y2="{B2}" stroke="black" stroke-width=".6"/>',
          f'<text transform="translate(12,{(T + B) / 2}) rotate(-90)" text-anchor="middle">Hair loss reports, No.</text>',
          f'<text transform="translate(12,{(T2 + B2) / 2}) rotate(-90)" text-anchor="middle">Reports, %</text>',
          f'<text x="{(L + R) / 2}" y="{H - 6}" text-anchor="middle">Year FDA received report</text></svg>']
    return '\n'.join(s)


def pct(k, n):
    return round(100 * k / n, 1) if n else ''


def write(name, header, rows):
    with open(os.path.join(OUT, name), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def selftest():
    assert abs(ror(10, 20, 30, 40)[0] - 2 / 3) < 1e-9
    assert [round(q, 6) for q in bh([.01, .04, .03, .005])] == [.02, .04, .04, .02]
    for name, cat in [('J and J Baby Powder', 'talc_powder'), ('WEN Sweet Almond Mint Cleansing Conditioner', 'hair_care'),
                      ('Black Henna Tattoo', 'tattoo_henna'), ('Nair Body Cream', 'depilatory'), ('Dove Dry Shampoo', 'hair_care'),
                      ('Bigen Permanent Powder #59', 'hair_dye_bleach'), ('Kérastase Genesis Serum', 'hair_care'),
                      ('Gold Bond Ultimate Lotion', 'skin_care'), ('Brown Sugar Body Scrub', 'skin_care'),
                      ('Unspecified Asbestos Containing Products', 'talc_powder'), ('Listerine Ready Tabs', 'oral')]:
        assert classify(norm(name)) == cat, (name, classify(norm(name)))
    assert brand(norm('No.4 Bond Maintenance Shampoo')) == 'olaplex'
    assert [brand_group(b) for b in ({'redken', 'olaplex'}, {'kerastase'}, {'dove'})] == ['olaplex', 'loreal_group', 'other']
    assert groups('Burning Sensation') == {'irritation_rash'} and groups('Chemical Burn Of Skin') == {'burn'}
    assert kappa([('a', 'a'), ('b', 'b')]) == 1 and kappa([('a', 'a'), ('b', 'b'), ('a', 'b'), ('b', 'a')]) == 0


def main():
    selftest()
    if '--kappa' in sys.argv:
        with open(sys.argv[sys.argv.index('--kappa') + 1], newline='', encoding='utf-8') as f:
            pairs = [(classify(norm(r['product_name'])), r['reviewer_category'].strip()) for r in csv.DictReader(f) if r['reviewer_category'].strip()]
        print(f"{len(pairs)} names reviewed; agreement {pct(sum(a == b for a, b in pairs), len(pairs))}%; Cohen's kappa {kappa(pairs):.2f}")
        return
    if not os.path.exists(ZIP):
        os.makedirs(os.path.dirname(ZIP), exist_ok=True)
        urllib.request.urlretrieve(URL, ZIP)
    with zipfile.ZipFile(ZIP) as z:
        data = json.loads(z.read(z.namelist()[0]))
    reports = data['results']
    assert len(reports) == data['meta']['results']['total'], 'incomplete download: delete data/ and re-run'
    os.makedirs(OUT, exist_ok=True)

    rows, mentions = [], collections.Counter()
    for x in reports:
        names = {norm(p.get('product_name', '')) for p in x.get('products', []) if p.get('role') != 'Concomitant'}
        cats = {classify(n) for n in names}
        rows.append(dict(x=x, names=names, year=x['initial_received_date'][:4], type=x['report_type'], talc='talc_powder' in cats,
                         cats=cats, brands={brand(n) for n in names} - {''}, pts=set(x.get('reactions', [])),
                         groups=set().union(*map(groups, x.get('reactions', [])))))
        mentions.update(names)
    years, types = sorted({r['year'] for r in rows}), sorted({r['type'] for r in rows})

    # Candidate duplicates: non-talc reports with identical suspect products, reactions, age, sex and event date.
    dup = collections.defaultdict(list)
    for r in rows:
        if not r['talc'] and r['x'].get('event_date'):
            pt = r['x'].get('patient', {})
            dup[frozenset(r['names']), frozenset(r['pts']), pt.get('age'), pt.get('gender'), r['x']['event_date']].append(r)
    dups = [g for g in dup.values() if len(g) > 1]
    write('duplicates_candidates.csv', ['group', 'report_number', 'report_type', 'received', 'event_date', 'products', 'reactions'],
          [[i, r['x']['report_number'], r['type'], r['x']['initial_received_date'], r['x']['event_date'],
            '; '.join(sorted(r['names'])), '; '.join(sorted(r['pts']))] for i, g in enumerate(dups, 1) for r in g])
    if DEDUP:  # keep the first report of each candidate-duplicate group
        extra = {r['x']['report_number'] for g in dups for r in g[1:]}
        rows = [r for r in rows if r['x']['report_number'] not in extra]

    # Table 1: report type by year, split by whether a talc/powder product is named.
    t1 = collections.Counter((r['year'], r['type'], r['talc']) for r in rows)
    write('table1_report_types_by_year.csv', ['year', 'report_type', 'reports', 'talc_product', 'non_talc'],
          [[y, t, t1[y, t, True] + t1[y, t, False], t1[y, t, True], t1[y, t, False]]
           for y in years for t in types if t1[y, t, True] + t1[y, t, False]])

    # Table 2: what non-talc mandatory (MoCRA) reports contain.
    m = [r for r in rows if r['type'] in MANDATORY and not r['talc']]
    t2 = [['reaction_group', k, c, pct(c, len(m))] for k, c in collections.Counter(g for r in m for g in r['groups']).most_common()]
    t2 += [['reaction_pt', k, c, pct(c, len(m))] for k, c in collections.Counter(p for r in m for p in r['pts']).most_common(30)]
    t2 += [['product_category', k, c, pct(c, len(m))] for k, c in collections.Counter(k for r in m for k in r['cats']).most_common()]
    write('table2_nontalc_mandatory_profile.csv', ['kind', 'name', 'reports', 'pct_of_nontalc_mandatory'], t2)

    # Table 3: hair-loss reports per year by brand (brand-surge timeline).
    hl = [r for r in rows if 'hair_loss' in r['groups']]
    t3 = collections.Counter((b, r['year']) for r in hl for b in r['brands'])
    t3.update(('any_hair_care', r['year']) for r in hl if 'hair_care' in r['cats'])
    write('table3_hairloss_by_brand_year.csv', ['brand'] + years + ['total'],
          [[b] + [t3[b, y] for y in years] + [sum(t3[b, y] for y in years)] for b in [k for k, _ in BRANDS] + ['any_hair_care']])

    # Table 4: reporting odds ratios, product category x reaction group, background = all non-talc reports.
    bg = [r for r in rows if not r['talc']]
    t4 = ror_table(bg, [k for k, _ in CATEGORIES[1:]] + ['unclassified'])
    write_ror('table4_ror.csv', t4)
    # Table 4b (sensitivity): hair product categories compared only with reports naming other hair products.
    t4b = ror_table([r for r in bg if r['cats'] & HAIR], sorted(HAIR))
    write_ror('table4b_ror_within_hair.csv', t4b)

    # Table 7: hair care x hair loss before vs after mandatory reporting (by first receipt date).
    t7 = []
    for period, sub in (('before', [r for r in bg if r['x']['initial_received_date'] < MOCRA]),
                        ('after', [r for r in bg if r['x']['initial_received_date'] >= MOCRA])):
        hc = [r for r in sub if 'hair_care' in r['cats'] and 'hair_loss' in r['groups']]
        vs_all = next(row for row in ror_table(sub, ['hair_care']) if row[1] == 'hair_loss')
        vs_hair = next(row for row in ror_table([r for r in sub if r['cats'] & HAIR], ['hair_care']) if row[1] == 'hair_loss')
        t7.append([period, len(hc), sum(r['type'] in MANDATORY for r in hc), sum(r['type'] == 'Direct' for r in hc),
                   *(f'{v:.3g}' for v in vs_all[6:9]), *(f'{v:.3g}' for v in vs_hair[6:9])])
    write('table7_before_after_mocra.csv', ['period', 'hair_care_hair_loss_reports', 'mandatory', 'direct', 'ror_vs_all', 'ci_low',
                                            'ci_high', 'ror_vs_hair', 'ci_low', 'ci_high'], t7)

    # Table 5: data completeness by report type.
    t5 = []
    for t in types:
        s = [r['x'] for r in rows if r['type'] == t]
        t5.append([t, len(s)] + [pct(sum(not f(x) for x in s), len(s)) for f in (
            lambda x: x.get('patient', {}).get('age'), lambda x: x.get('patient', {}).get('gender'),
            lambda x: x.get('event_date'), lambda x: x.get('outcomes'))])
    write('table5_completeness.csv', ['report_type', 'reports', 'pct_missing_age', 'pct_missing_sex', 'pct_missing_event_date', 'pct_missing_outcome'], t5)

    # Table 6 (Figure 1 data): hair-loss reports naming a hair-care product, by year, brand group and report type.
    fig = [r for r in hl if 'hair_care' in r['cats']]
    t6 = collections.Counter((r['year'], brand_group(r['brands']), r['type']) for r in fig)
    write('table6_fig1_hairloss_by_group.csv', ['year', 'brand_group', 'report_type', 'reports'], [[*k, c] for k, c in sorted(t6.items())])
    svg = figure1_svg(t6)
    with open(os.path.join(OUT, 'figure1.svg'), 'w', encoding='utf-8') as f:
        f.write(svg)
    with open(os.path.join(OUT, 'figure1.html'), 'w', encoding='utf-8') as f:  # page sized to the figure, for PDF export
        f.write(f'<!doctype html><meta charset="utf-8"><style>@page{{size:7in 5in;margin:0}}body{{margin:0}}svg{{display:block}}</style>{svg}')


    # Every distinct (normalized) suspect product name with its category, for checking the dictionary.
    write('products_classified.csv', ['product_name_normalized', 'category', 'brand', 'reports'],
          [[n, classify(n), brand(n), c] for n, c in mentions.most_common()])

    nt_mentions = collections.Counter(classify(n) for r in bg for n in r['names'])
    mand = [r for r in rows if r['type'] in MANDATORY]
    lor = [r for r in fig if brand_group(r['brands']) == 'loreal_group' and r['year'] >= '2024']
    surge = [r for r in fig if brand_group(r['brands']) in SURGE_BRANDS[:4]]
    print(f"openFDA cosmetic events, last_updated {data['meta']['last_updated']}: {len(rows)} reports, received "
          f"{min(x['initial_received_date'] for x in reports)}-{max(x['initial_received_date'] for x in reports)}")
    print(f"Mandatory (MoCRA) reports: {len(mand)}; naming a talc/powder product: {len(mand) - len(m)} ({pct(len(mand) - len(m), len(mand))}%)")
    tm = [r for r in mand if r['talc']]
    print(f"Talc mandatory reports listing a malignancy: {sum('malignancy' in r['groups'] for r in tm)} of {len(tm)} "
          f"({pct(sum('malignancy' in r['groups'] for r in tm), len(tm))}%)")
    print(f"Non-talc mandatory: {len(m)}; with hair loss: {sum('hair_loss' in r['groups'] for r in m)} "
          f"({pct(sum('hair_loss' in r['groups'] for r in m), len(m))}%)")
    print(f"Unclassified suspect products in non-talc reports: {pct(nt_mentions['unclassified'], sum(nt_mentions.values()))}% of product mentions")
    print(f"Hair-care hair-loss reports: {len(fig)}; L'Oreal brands 2024-25: {sum(r['type'] in MANDATORY for r in lor)} of {len(lor)} mandatory; "
          f"WEN/Monat/DevaCurl/Olaplex: {sum(r['type'] == 'Direct' for r in surge)} of {len(surge)} Direct")
    print(f"Candidate duplicates: {sum(len(g) - 1 for g in dups)} extra reports in {len(dups)} groups "
          f"({sum(len(g) - 1 for g in dups if 'hair_loss' in g[0]['groups'])} with hair loss; "
          f"{sum(len({r['type'] for r in g}) > 1 for g in dups)} groups mix report types)")
    print('Within hair products (table4b):', '; '.join(f'{r[0]} x {r[1]} ROR {r[6]:.1f} ({r[7]:.1f}-{r[8]:.1f})'
                                                       for r in t4b if r[0] == 'hair_care' and r[1] in ('hair_loss', 'hair_damage')))
    print('Before/after MoCRA (table7):', t7)
    print('Signals (a>=3, CI low>1, q<.05):')
    for r in sorted((r for r in t4 if r[-1]), key=lambda r: -r[6]):
        print(f'  {r[0]:<26} {r[1]:<20} n={r[2]:<5} ROR {r[6]:.1f} ({r[7]:.1f}-{r[8]:.1f})')
    print(f'CSV tables written to {OUT}')


if __name__ == '__main__':
    main()
