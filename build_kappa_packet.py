"""Build the blinded reviewer packet and the separate answer key for the kappa validation. Run once.

Run:  python build_kappa_packet.py     (needs openpyxl for the workbook; nothing else in this project does)
Reproduces the frozen seed-2026 sample from audit.py and stops if it differs from the sheet on disk.
Reviewer files go to review/kappa_packet/. Labels, strata and weights go to review/kappa_key_DO_NOT_SHARE/ only.
Reads the frozen dictionary and never changes it. Refuses to replace an existing key with different contents.
"""
import collections, csv, hashlib, importlib.util, json, os, random, zipfile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('pipeline', os.path.join(HERE, 'pipeline.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PACKET = os.path.join(HERE, 'review', 'kappa_packet')
KEYDIR = os.path.join(HERE, 'review', 'kappa_key_DO_NOT_SHARE')
SEED = 2026
SIZES = {'hair_care': 80, 'hair_dye_bleach': 50, 'hair_relaxer_straightener': 40, 'unclassified': 30, 'skin_care': 20}
MULTI = 'multiple_rule_names'

CODEBOOK = [
    ('deodorant', 'Underarm deodorants and antiperspirants'),
    ('depilatory', 'Hair removal creams, waxes and strips; razorless shave products. Not shaving cream used with a razor'),
    ('eye_lash_brow', 'Mascara, eyeliner, eyeshadow, lash and brow products, lash serums, eye creams'),
    ('feminine_hygiene', 'Feminine washes, wipes, douches and other intimate-area products'),
    ('fragrance', 'Perfume, cologne, body mist'),
    ('hair_care', 'Shampoo, conditioner, styling products, hair oils, hair masks, scalp products. Not hair color or chemical straightening'),
    ('hair_dye_bleach', 'Hair dyes, bleach, lighteners, developers, hair color kits and their parts'),
    ('hair_relaxer_straightener', 'Relaxers, chemical straighteners, keratin or smoothing salon treatments, perms, texturizers. Not everyday shampoo or conditioner'),
    ('lip', 'Lipstick, gloss, lip balm, lip plumpers'),
    ('makeup', 'Foundation, concealer, blush, bronzer, primer, face powder'),
    ('nail', 'Polish, gels, acrylics, cuticle and nail treatments'),
    ('oral', 'Toothpaste, mouthwash, whitening strips, denture products'),
    ('skin_care', 'Face and body creams, lotions, cleansers, soaps, sunscreen, body wash, shaving cream'),
    ('skin_lightening', 'Skin lightening, bleaching or fade creams'),
    ('supplement_or_drug', 'Pills, gummies, supplements, and drugs such as minoxidil'),
    ('talc_powder', 'Baby or body powders of the kind historically made with talc'),
    ('tattoo_henna', 'Tattoo inks, permanent makeup pigments, black henna, microblading'),
]
NONE_FIT = ('unclassified', 'Not one of the 17 categories: not enough information, or none of them fit')
STEPS = [
    'Work alone. Do not look at the study code, results or anyone else\'s ratings, and do not discuss names with anyone until you are finished.',
    'Open the Review sheet. Fill in only the yellow cells. Column C is required, and column D is optional.',
    'For each product name, choose what kind of product it is from the dropdown in column C. Use the Categories sheet for definitions.',
    'Judge the product by its main intended use.',
    'Names were cleaned by computer: lower case, no punctuation. Some are misspelled, cut off or run together.',
    'A web search of up to about a minute is fine for an unfamiliar name. Do not guess from the brand alone if the brand sells many kinds of product.',
    'If two categories fit, choose the main use and add a short note in column D.',
    'If you cannot tell, choose unclassified. It is the only option outside the 17 categories.',
    'Do not sort the sheet, or add, delete or edit IDs or names.',
    'Enter your initials and the date, save the workbook, and return the whole file.',
]


def dictionary_hash():
    rules = [[k, p.pattern] for k, p in P.NOT_HAIR + P.CATEGORIES] + [[k, p.pattern] for k, p in P.BRANDS]
    return hashlib.sha256(json.dumps(rules).encode()).hexdigest()


def reconstruct():
    """audit.py's sampling, step for step, so stratum membership and frame sizes are known rather than inferred."""
    with zipfile.ZipFile(P.ZIP) as z:
        reports = json.loads(z.read(z.namelist()[0]))['results']
    rows = []
    for x in reports:
        names = sorted({P.norm(q.get('product_name', '')) for q in x.get('products', []) if q.get('role') != 'Concomitant'})
        rows.append((names, {P.classify(n) for n in names}))
    mentions_all = {n for names, _ in rows for n in names}
    coll = {n for n in mentions_all if sum(bool(p.search(n)) for _, p in P.CATEGORIES) > 1}
    nontalc = [names for names, cats in rows if 'talc_powder' not in cats]
    pool = sorted({n for names in nontalc for n in names})
    rng = random.Random(SEED)
    sample = [n for k, v in SIZES.items() for n in rng.sample([n for n in pool if P.classify(n) == k and n not in coll], v)]
    sample += rng.sample(sorted(n for n in pool if n in coll), 30)
    rng.shuffle(sample)
    with open(os.path.join(HERE, 'out', 'validation_stratified_for_reviewer.csv'), newline='', encoding='utf-8') as f:
        assert sample == [r['product_name'] for r in csv.DictReader(f)], 'sample differs from the frozen sheet'
    m = collections.Counter(n for names in nontalc for n in names)
    stratum = lambda n: MULTI if n in coll else P.classify(n)
    frame_n, frame_m = collections.Counter(), collections.Counter()
    for n in pool:
        frame_n[stratum(n)] += 1
        frame_m[stratum(n)] += m[n]
    return sample, m, stratum, frame_n, frame_m, len(pool), sum(m.values())


def write_workbook(rows, path):
    arial = lambda **k: Font(name='Arial', size=k.pop('size', 11), **k)
    head = PatternFill('solid', fgColor='1F3864')
    fill = PatternFill('solid', fgColor='FFF2CC')
    thin = Side(style='thin', color='BFBFBF')
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical='top')
    wb = Workbook()
    wb.properties.creator = wb.properties.lastModifiedBy = ''
    wb.properties.title = 'Product classification review'

    ins = wb.active
    ins.title = 'Instructions'
    ins.column_dimensions['A'].width = 5
    ins.column_dimensions['B'].width = 62
    ins.column_dimensions['C'].width = 28
    ins['A1'] = 'Product classification: reviewer instructions'
    ins['A1'].font = arial(size=14, bold=True)
    ins['A2'] = f'{len(rows)} product names. Allow about 1 hour. You can stop and return to it.'
    ins['A2'].font = arial()
    r = 4
    for i, step in enumerate(STEPS, 1):
        ins.cell(r, 1, i).font = arial()
        ins.cell(r, 1).alignment = Alignment(vertical='top')
        ins.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        ins.cell(r, 2, step).font = arial()
        ins.cell(r, 2).alignment = wrap
        ins.row_dimensions[r].height = 32 if len(step) > 85 else 17
        r += 1
    r += 1
    ins.cell(r, 1, 'Example of a completed row (made up, not part of the review)').font = arial(bold=True)
    r += 1
    for c, v in enumerate(('ID', 'Product name', 'Your category'), 1):
        ins.cell(r, c, v).font = arial(bold=True, color='FFFFFF')
        ins.cell(r, c).fill = head
    r += 1
    for c, v in enumerate(('EX', 'example brand volumizing shampoo', 'hair_care'), 1):
        ins.cell(r, c, v).font = arial()
        ins.cell(r, c).border = box

    cat = wb.create_sheet('Categories')
    cat.column_dimensions['A'].width = 30
    cat.column_dimensions['B'].width = 95
    for c, v in enumerate(('Category', 'Use for'), 1):
        cat.cell(1, c, v).font = arial(bold=True, color='FFFFFF')
        cat.cell(1, c).fill = head
    for i, (code, text) in enumerate(CODEBOOK + [NONE_FIT], 2):
        cat.cell(i, 1, code).font = arial()
        cat.cell(i, 2, text).font = arial()
        cat.cell(i, 2).alignment = wrap
        cat.cell(i, 1).border = cat.cell(i, 2).border = box
    cat.freeze_panes = 'A2'

    rev = wb.create_sheet('Review')
    for col, width in zip('ABCD', (10, 72, 30, 40)):
        rev.column_dimensions[col].width = width
    rev['A1'] = 'Fill in the yellow cells only. Column C is required, and column D is optional.'
    rev['A1'].font = arial(bold=True)
    rev['A2'], rev['C2'] = 'Reviewer initials:', 'Date finished:'
    rev['A2'].font = rev['C2'].font = arial()
    for ref in ('B2', 'D2'):
        rev[ref].fill = fill
        rev[ref].border = box
        rev[ref].font = arial()
        rev[ref].protection = Protection(locked=False)
    for c, v in enumerate(('ID', 'Product name', 'Your category', 'Notes (optional)'), 1):
        rev.cell(4, c, v).font = arial(bold=True, color='FFFFFF')
        rev.cell(4, c).fill = head
    for i, row in enumerate(rows, 5):
        rev.cell(i, 1, row['id']).font = arial()
        rev.cell(i, 2, row['product_name']).font = arial()
        rev.cell(i, 2).alignment = wrap
        for c in (3, 4):
            rev.cell(i, c).fill = fill
            rev.cell(i, c).font = arial()
            rev.cell(i, c).protection = Protection(locked=False)
        for c in range(1, 5):
            rev.cell(i, c).border = box
    last = 4 + len(rows)
    dv = DataValidation(type='list', formula1=f'=Categories!$A$2:$A${len(CODEBOOK) + 2}', allow_blank=True, showErrorMessage=True,
                        errorStyle='stop', errorTitle='Not a category', error='Choose a category from the dropdown list.')
    rev.add_data_validation(dv)
    dv.add(f'C5:C{last}')
    rev.freeze_panes = 'A5'
    rev.protection.sheet = True  # no password: stops accidental sorting or edits to IDs and names
    rev.protection.formatColumns = False
    wb.active = 0
    wb.save(path)


def write_readme(path, n):
    table = ''.join(f'| `{c}` | {t} |\n' for c, t in CODEBOOK + [NONE_FIT])
    steps = ''.join(f'{i}. {s}\n' for i, s in enumerate(STEPS, 1))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'# Product Classification: Reviewer Instructions\n\n{n} product names. Allow about 1 hour. You can stop and return to it.\n\n'
                f'Use `kappa_reviewer_workbook.xlsx`. It holds these instructions, the categories, and a dropdown for each name.\n\n{steps}\n'
                f'If you cannot use the workbook, fill in `human_category` in `kappa_reviewer_sheet.csv` with a code exactly as written below, and keep `id` and `product_name` unchanged.\n\n'
                f'## Categories\n\n| Category | Use for |\n|---|---|\n{table}')


def main():
    sample, m, stratum, frame_n, frame_m, pool_n, pool_m = reconstruct()
    n_h = collections.Counter(stratum(n) for n in sample)
    sample_m = collections.Counter()
    for n in sample:
        sample_m[stratum(n)] += m[n]
    key = [{'id': f'KV-{i:03d}', 'product_name': n, 'dictionary_category': P.classify(n), 'stratum': stratum(n),
            'nontalc_mentions': m[n]} for i, n in enumerate(sample, 1)]
    os.makedirs(KEYDIR, exist_ok=True)
    os.makedirs(PACKET, exist_ok=True)
    keyfile = os.path.join(KEYDIR, 'kappa_key.csv')
    text = 'id,product_name,dictionary_category,stratum,nontalc_mentions\n' + ''.join(
        f"{k['id']},{k['product_name']},{k['dictionary_category']},{k['stratum']},{k['nontalc_mentions']}\n" for k in key)
    if os.path.exists(keyfile):
        assert open(keyfile, encoding='utf-8').read() == text, 'an existing key differs: refusing to overwrite it'
    with open(keyfile, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    with open(os.path.join(KEYDIR, 'kappa_strata.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['stratum', 'frame_names', 'frame_nontalc_mentions', 'sampled_names', 'sampled_nontalc_mentions'])
        for s, v in frame_n.most_common():
            w.writerow([s, v, frame_m[s], n_h[s], sample_m[s]])
    manifest = {'seed': SEED, 'dictionary_sha256': dictionary_hash(), 'frozen_on': '2026-11-05', 'names': len(sample),
                'sample_sha256': hashlib.sha256('\n'.join(sample).encode()).hexdigest(), 'frame_names': pool_n, 'frame_nontalc_mentions': pool_m,
                'categories': [c for c, _ in CODEBOOK], 'fallback': NONE_FIT[0]}
    with open(os.path.join(KEYDIR, 'kappa_manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)

    with open(os.path.join(PACKET, 'kappa_reviewer_sheet.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['id', 'product_name', 'human_category', 'notes'])
        w.writerows([k['id'], k['product_name'], '', ''] for k in key)
    write_workbook(key, os.path.join(PACKET, 'kappa_reviewer_workbook.xlsx'))
    write_readme(os.path.join(PACKET, 'README_FOR_REVIEWER.md'), len(key))
    print(f'{len(key)} names, {len(n_h)} sampled strata, key in {os.path.relpath(KEYDIR, HERE)}')


if __name__ == '__main__':
    main()
