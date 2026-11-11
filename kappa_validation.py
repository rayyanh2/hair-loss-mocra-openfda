"""Compare a completed blinded review with the frozen dictionary labels. Needs genuine human ratings.

Run:  python3 -I kappa_validation.py COMPLETED.xlsx      (or .csv; results go to review/kappa_results/)
      python3 -I kappa_validation.py --selftest          (checks the statistics on published and simulated tables, writes nothing)
Matches on ID, and stops on any missing, duplicated, altered or invalid row. Stops if the dictionary has changed since the key
was made. Never edits the dictionary or the key. Disagreements go to a separate adjudication file and do not change kappa.
"""
import collections, csv, hashlib, importlib.util, json, math, os, random, re, sys, xml.etree.ElementTree as ET, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KEYDIR = os.path.join(HERE, 'review', 'kappa_key_DO_NOT_SHARE')
Z = 1.959964
BOOT, SEED = 10000, 2026
SMALL = 10  # categories with fewer names than this get an instability flag


def wilson(k, n):
    if not n:
        return 0.0, 0.0
    p, d = k / n, 1 + Z * Z / n
    mid, half = (p + Z * Z / (2 * n)) / d, Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def agreement(pairs, weights=None):
    """Observed agreement, chance agreement and kappa from (dictionary, human) pairs. Weights give the design-weighted table."""
    weights = weights or [1.0] * len(pairs)
    total = sum(weights)
    row, col, po = collections.Counter(), collections.Counter(), 0.0
    for (a, b), w in zip(pairs, weights):
        row[a] += w / total
        col[b] += w / total
        po += w / total * (a == b)
    pe = sum(row[k] * col[k] for k in row)
    return po, pe, ((po - pe) / (1 - pe) if pe < 1 else float('nan'))


def fleiss_se(pairs):
    """Large-sample SE of kappa (Fleiss, Cohen and Everitt, 1969). Assumes simple random sampling."""
    n = len(pairs)
    p = collections.Counter(pairs)
    row, col = collections.Counter(), collections.Counter()
    for (a, b), c in p.items():
        row[a] += c / n
        col[b] += c / n
    po, pe, k = agreement(pairs)
    diag = sum(c / n * (1 - (row[a] + col[a]) * (1 - k)) ** 2 for (a, b), c in p.items() if a == b)
    off = (1 - k) ** 2 * sum(c / n * (col[a] + row[b]) ** 2 for (a, b), c in p.items() if a != b)
    return math.sqrt(max(0.0, diag + off - (k - pe * (1 - k)) ** 2) / (n * (1 - pe) ** 2))


def name_weights(rows, strata):
    size = collections.Counter(r['stratum'] for r in rows)
    return [strata[r['stratum']]['frame_names'] / size[r['stratum']] for r in rows]


def mention_weights(rows, strata):
    """Each name counts by its mentions, calibrated so each stratum sums to its known total mentions."""
    seen = collections.Counter()
    for r in rows:
        seen[r['stratum']] += r['mentions']
    return [strata[r['stratum']]['frame_nontalc_mentions'] * r['mentions'] / seen[r['stratum']] for r in rows]


def percentile(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def bootstrap(rows, strata):
    """Stratified bootstrap: resample within strata so every replicate keeps the design. No finite-population correction, so slightly wide."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r['stratum']].append(r)
    rng = random.Random(SEED)
    out = collections.defaultdict(list)
    for _ in range(BOOT):
        s = [rng.choice(g) for g in groups.values() for _ in g]
        pairs = [(r['dictionary'], r['human']) for r in s]
        out['kappa'].append(agreement(pairs)[2])
        for name, weigh in (('names', name_weights), ('mentions', mention_weights)):
            po, _, k = agreement(pairs, weigh(s, strata))
            out['po_' + name].append(po)
            out['kappa_' + name].append(k)
    return {k: (percentile(v, .025), percentile(v, .975)) for k, v in out.items()}


def xlsx_rows(path):
    """Rows of the Review sheet as lists of strings, standard library only, so openpyxl is not needed here."""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    rel = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns)]
        target = next(s.get(rel) for s in ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets', ns) if s.get('name') == 'Review')
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        part = next(r.get('Target') for r in rels if r.get('Id') == target).lstrip('/')
        sheet = ET.fromstring(z.read(part if part.startswith('xl/') else 'xl/' + part))
    out = []
    for row in sheet.find('m:sheetData', ns):
        cells = {}
        for c in row:
            v, kind = c.find('m:v', ns), c.get('t')
            text = ''.join(t.text or '' for t in c.iter('{%s}t' % ns['m'])) if kind == 'inlineStr' else (v.text if v is not None else '')
            cells[re.match('[A-Z]+', c.get('r'))[0]] = shared[int(text)] if kind == 's' and text != '' else text
        out.append([cells.get(col, '') or '' for col in 'ABCD'])
    return out


def read_ratings(path):
    if path.lower().endswith('.xlsx'):
        rows = xlsx_rows(path)
        start = next(i for i, r in enumerate(rows) if r[0].strip() == 'ID')
        return [dict(id=r[0].strip(), product_name=r[1].strip(), human=r[2].strip(), notes=r[3].strip()) for r in rows[start + 1:] if any(x.strip() for x in r)]
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [dict(id=r['id'].strip(), product_name=r['product_name'].strip(), human=r['human_category'].strip(), notes=(r.get('notes') or '').strip()) for r in csv.DictReader(f)]


def load_key():
    """Key, strata and permitted labels, after confirming the dictionary still gives every key label."""
    spec = importlib.util.spec_from_file_location('pipeline', os.path.join(HERE, 'pipeline.py'))
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)
    manifest = json.load(open(os.path.join(KEYDIR, 'kappa_manifest.json')))
    rules = [[k, p.pattern] for k, p in P.NOT_HAIR + P.CATEGORIES] + [[k, p.pattern] for k, p in P.BRANDS]
    if hashlib.sha256(json.dumps(rules).encode()).hexdigest() != manifest['dictionary_sha256']:
        sys.exit('STOP: the dictionary changed after the key was frozen, so kappa would not be valid.')
    with open(os.path.join(KEYDIR, 'kappa_key.csv'), newline='', encoding='utf-8') as f:
        key = {r['id']: r for r in csv.DictReader(f)}
    wrong = [k for k, r in key.items() if P.classify(r['product_name']) != r['dictionary_category']]
    if wrong:
        sys.exit(f'STOP: {len(wrong)} key labels no longer match the dictionary, for example {wrong[:5]}.')
    with open(os.path.join(KEYDIR, 'kappa_strata.csv'), newline='') as f:
        strata = {r['stratum']: {k: int(v) for k, v in r.items() if k != 'stratum'} for r in csv.DictReader(f)}
    return key, strata, manifest


def merge(ratings, key, allowed):
    """Join on ID. Any defect stops the run: a partial or altered sheet must not produce a kappa."""
    ids = [r['id'] for r in ratings]
    problems = []
    dup = [k for k, c in collections.Counter(ids).items() if c > 1]
    if dup:
        problems.append(f'duplicated IDs: {dup[:10]}')
    if set(ids) - set(key):
        problems.append(f'unknown IDs: {sorted(set(ids) - set(key))[:10]}')
    if set(key) - set(ids):
        problems.append(f'{len(set(key) - set(ids))} IDs missing, for example {sorted(set(key) - set(ids))[:10]}')
    blank = [r['id'] for r in ratings if not r['human']]
    if blank:
        problems.append(f'{len(blank)} names not classified, for example {blank[:10]}')
    bad = [(r['id'], r['human']) for r in ratings if r['human'] and r['human'] not in allowed]
    if bad:
        problems.append(f'invalid categories: {bad[:10]}')
    edited = [r['id'] for r in ratings if r['id'] in key and r['product_name'] != key[r['id']]['product_name']]
    if edited:
        problems.append(f'product name differs from the key: {edited[:10]}')
    if problems:
        sys.exit('STOP: the rating file cannot be scored.\n- ' + '\n- '.join(problems))
    return [dict(id=r['id'], name=r['product_name'], human=r['human'], notes=r['notes'], dictionary=key[r['id']]['dictionary_category'],
                 stratum=key[r['id']]['stratum'], mentions=int(key[r['id']]['nontalc_mentions'])) for r in ratings]


def category_rows(rows):
    """One-vs-rest agreement per label, including any label only the reviewer used."""
    pairs = [(r['dictionary'], r['human']) for r in rows]
    out = []
    for c in sorted({x for p in pairs for x in p}):
        both = sum(a == c and b == c for a, b in pairs)
        d = sum(a == c for a, _ in pairs)
        h = sum(b == c for _, b in pairs)
        k = agreement([(a == c, b == c) for a, b in pairs])[2]
        lo, hi = wilson(both, d)
        out.append(dict(category=c, dictionary_n=d, human_n=h, both=both, positive_agreement=2 * both / (d + h), dictionary_precision=both / d if d else None,
                        precision_ci=(lo, hi) if d else None, human_recall=both / h if h else None, kappa_one_vs_rest=k, small=min(d, h) < SMALL))
    return out


def write_csv(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def num(v, d=3):
    return '' if v is None else f'{v:.{d}f}'


def report(rows, strata, manifest, out, source):
    os.makedirs(out, exist_ok=True)
    pairs = [(r['dictionary'], r['human']) for r in rows]
    n = len(rows)
    po, pe, k = agreement(pairs)
    agree = sum(a == b for a, b in pairs)
    se = fleiss_se(pairs)
    lo, hi = wilson(agree, n)
    boot = bootstrap(rows, strata)
    wn, wm = name_weights(rows, strata), mention_weights(rows, strata)
    po_n, _, k_n = agreement(pairs, wn)
    po_m, _, k_m = agreement(pairs, wm)
    neff = sum(wn) ** 2 / sum(w * w for w in wn)
    deff = n / neff
    wlo, whi = wilson(po_n * neff, neff)

    labels = sorted({x for p in pairs for x in p})
    cells = collections.Counter(pairs)
    write_csv(os.path.join(out, 'confusion_matrix.csv'), ['dictionary \\ human'] + labels + ['total'],
              [[a] + [cells[a, b] for b in labels] + [sum(cells[a, b] for b in labels)] for a in labels] + [['total'] + [sum(cells[a, b] for a in labels) for b in labels] + [n]])
    cats = category_rows(rows)
    write_csv(os.path.join(out, 'agreement_by_category.csv'),
              ['category', 'dictionary_n', 'human_n', 'both', 'positive_agreement', 'dictionary_precision', 'precision_ci_low', 'precision_ci_high', 'human_recall', 'kappa_one_vs_rest', 'small_n_flag'],
              [[c['category'], c['dictionary_n'], c['human_n'], c['both'], num(c['positive_agreement']), num(c['dictionary_precision']), num(c['precision_ci'][0]) if c['precision_ci'] else '',
                num(c['precision_ci'][1]) if c['precision_ci'] else '', num(c['human_recall']), num(c['kappa_one_vs_rest']), 'yes' if c['small'] else ''] for c in cats])
    by = collections.defaultdict(list)
    for r in rows:
        by[r['stratum']].append(r)
    srows = []
    for s, g in sorted(by.items(), key=lambda kv: -strata[kv[0]]['frame_names']):
        a = sum(r['dictionary'] == r['human'] for r in g)
        l, h = wilson(a, len(g))
        srows.append([s, strata[s]['frame_names'], len(g), f'{100 * len(g) / strata[s]["frame_names"]:.1f}', f'{strata[s]["frame_names"] / len(g):.1f}', a, f'{100 * a / len(g):.1f}', f'{100 * l:.1f}', f'{100 * h:.1f}'])
    write_csv(os.path.join(out, 'agreement_by_stratum.csv'), ['stratum', 'frame_names', 'sampled', 'sampling_percent', 'weight', 'agreed', 'agreement_percent', 'ci_low', 'ci_high'], srows)
    dis = sorted((r for r in rows if r['dictionary'] != r['human']), key=lambda r: (r['dictionary'], r['human'], r['id']))
    write_csv(os.path.join(out, 'disagreements_for_adjudication.csv'),
              ['id', 'product_name', 'dictionary_category', 'human_category', 'stratum', 'nontalc_mentions', 'reviewer_notes', 'adjudicated_category', 'adjudicator', 'adjudication_notes'],
              [[r['id'], r['name'], r['dictionary'], r['human'], r['stratum'], r['mentions'], r['notes'], '', '', ''] for r in dis])

    zero = [s[0] for s in srows if s[5] == s[2]]
    covered = sum(strata[s]['frame_names'] for s in by)
    results = (f'The dictionary and reviewer agreed on {agree} of {n} names ({100 * po:.1f}%; κ = {k:.2f} [95% CI, {boot["kappa"][0]:.2f}-{boot["kappa"][1]:.2f}]); '
               f'weighted to stratum sizes, agreement was {100 * po_n:.1f}% (95% CI, {100 * wlo:.1f}-{100 * whi:.1f}) and κ was {k_n:.2f} (95% CI, {boot["kappa_names"][0]:.2f}-{boot["kappa_names"][1]:.2f}).')
    short = f'κ = {k:.2f} [95% CI, {boot["kappa"][0]:.2f}-{boot["kappa"][1]:.2f}]; weighted agreement, {100 * po_n:.1f}%'
    lines = [f'# Kappa validation results\n\nSource: {os.path.basename(source)}. Dictionary hash {manifest["dictionary_sha256"][:12]}, unchanged since the key was frozen.\n',
             '## Sample (unweighted): describes these 250 names only\n',
             f'- Agreement: {agree} of {n}, {100 * po:.1f}% (Wilson 95% CI, {100 * lo:.1f}-{100 * hi:.1f})',
             f'- Chance agreement: {100 * pe:.1f}%',
             f'- Cohen κ: {k:.3f}. Stratified bootstrap 95% CI, {boot["kappa"][0]:.3f}-{boot["kappa"][1]:.3f} (reported). Analytic SE {se:.3f}, 95% CI, {k - Z * se:.3f}-{k + Z * se:.3f} (assumes simple random sampling)\n',
             '## Weighted to the sampled strata\n',
             f'Covers {covered:,} of {manifest["frame_names"]:,} names in nontalc reports. Kish design effect {deff:.2f}, effective sample size {neff:.0f}.\n',
             '| Target | Agreement | κ (stratified bootstrap 95% CI) |', '|---|---|---|',
             f'| Names (primary) | {100 * po_n:.1f}% (Wilson on effective n, {100 * wlo:.1f}-{100 * whi:.1f}) | {k_n:.3f} ({boot["kappa_names"][0]:.3f}-{boot["kappa_names"][1]:.3f}) |',
             f'| Report mentions (secondary) | {100 * po_m:.1f}% (bootstrap, {100 * boot["po_mentions"][0]:.1f}-{100 * boot["po_mentions"][1]:.1f}) | {k_m:.3f} ({boot["kappa_mentions"][0]:.3f}-{boot["kappa_mentions"][1]:.3f}) |\n',
             f'Weighting changed agreement by {100 * (po_n - po):+.1f} points and κ by {k_n - k:+.3f}.\n',
             '## Agreement by stratum\n', '| Stratum | Sampled of frame | Weight | Agreed | Agreement (95% CI) |', '|---|---|---|---|---|']
    lines += [f'| {s[0]} | {s[2]} of {s[1]} | {s[4]} | {s[5]} | {s[6]}% ({s[7]}-{s[8]}) |' for s in srows]
    lines += ['\n## Agreement by category\n', '| Category | Dictionary n | Human n | Both | Positive agreement | One-vs-rest κ | Note |', '|---|---|---|---|---|---|---|']
    lines += [f"| {c['category']} | {c['dictionary_n']} | {c['human_n']} | {c['both']} | {100 * c['positive_agreement']:.1f}% | {c['kappa_one_vs_rest']:.2f} | {'Too few to interpret' if c['small'] else ''} |" for c in cats]
    lines += [f'\n## Disagreements\n\n{len(dis)} names are in `disagreements_for_adjudication.csv`. Adjudication does not change the κ above, and the dictionary stays frozen.\n', '## Sentences\n',
              f'**Results:** {results}\n', f'**Compact, for the Methods parenthesis:** ({short})\n', '## Check before reporting\n']
    if agree == n:
        lines.append('- Agreement is 100%. Confirm the ratings are independent and were not copied from dictionary output.')
    if zero:
        lines.append(f'- No disagreements in: {", ".join(zero)}. Those strata add no bootstrap variance, so weighted κ intervals may be too narrow.')
    lines.append('- In the five single-category strata, the dictionary label is constant, so stratum agreement is the precision of that label. κ reflects differences across strata.')
    lines.append('- Weighted results describe the sampled strata only. They say nothing about strata with no sampled names, or about talc-only names.')
    text = '\n'.join(lines) + '\n'
    with open(os.path.join(out, 'kappa_summary.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)


def selftest():
    close = lambda a, b, t=1e-3: abs(a - b) < t
    expand = lambda t: [(i, j) for (i, j), c in t.items() for _ in range(c)]
    cohen = expand({('y', 'y'): 20, ('y', 'n'): 5, ('n', 'y'): 10, ('n', 'n'): 15})
    assert close(agreement(cohen)[2], .4), 'worked two-rater example gives kappa 0.40'
    fleiss = {('a', 'a'): 75, ('a', 'b'): 1, ('a', 'c'): 4, ('b', 'a'): 5, ('b', 'b'): 4, ('b', 'c'): 1, ('c', 'c'): 10}
    pairs = expand(fleiss)
    assert close(agreement(pairs)[2], .676, 2e-3), 'three-category table gives kappa 0.676'
    assert agreement([('a', 'a'), ('b', 'b')])[2] == 1
    assert close(wilson(8, 10)[0], .4902) and close(wilson(8, 10)[1], .9433), 'Wilson 8/10'
    rng = random.Random(1)
    draws = []
    for _ in range(4000):
        draws.append(agreement([rng.choice(pairs) for _ in pairs])[2])
    mean = sum(draws) / len(draws)
    sd = math.sqrt(sum((x - mean) ** 2 for x in draws) / len(draws))
    assert abs(fleiss_se(pairs) / sd - 1) < .06, (fleiss_se(pairs), sd)
    two = [dict(stratum='x', dictionary='a', human='a', mentions=1)] * 9 + [dict(stratum='x', dictionary='a', human='b', mentions=1)] + [dict(stratum='y', dictionary='b', human='b', mentions=3)] * 10
    st = {'x': {'frame_names': 100, 'frame_nontalc_mentions': 100}, 'y': {'frame_names': 900, 'frame_nontalc_mentions': 300}}
    pr = [(r['dictionary'], r['human']) for r in two]
    assert close(agreement(pr, name_weights(two, st))[0], (100 * .9 + 900) / 1000), 'name-weighted agreement'
    assert close(agreement(pr, mention_weights(two, st))[0], (100 * .9 + 300) / 400), 'mention-weighted agreement'
    print(f'Self-test passed. Analytic SE {fleiss_se(pairs):.4f} vs simulated SD {sd:.4f}.')


def main():
    if '--selftest' in sys.argv:
        return selftest()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else os.path.join(HERE, 'review', 'kappa_results')
    args = [a for a in args if a != out]
    key, strata, manifest = load_key()
    rows = merge(read_ratings(args[0]), key, set(manifest['categories']) | {manifest['fallback']})
    report(rows, strata, manifest, out, args[0])


if __name__ == '__main__':
    main()
