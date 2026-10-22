"""Known-answer contract checks; no test framework or real-data dependency."""
import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('contract', Path(__file__).with_name('contract.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixture = json.loads(Path(__file__).with_name('fixture.json').read_text())
groups = module.form_groups(**fixture)
observed = {(g['identity']['biological_replicate_id'], g['identity']['cell_type']): g['usable_fragments'] for g in groups}
assert observed == {('r1','A'):3, ('r1','B'):1, ('r1','C'):1, ('r2','A'):1, ('r2','B'):2, ('r2','C'):1}, observed
assert sum(g['usable_fragments'] for g in groups) == 9
assert sum(sum(f['support'] for f in g['fragments']) for g in groups) == 12
assert len({g['task_id'] for g in groups}) == 6
assert module.model_cuts(10,30,(4,-5)) == (10,30)  # reverse interval endpoint is exclusive
assert module.model_cuts(10,30,(0,0)) == (14,25)
assert module.model_cuts(10,30,(4,-4)) == (10,29)
shuffled=copy.deepcopy(fixture)
for key in shuffled: shuffled[key].reverse()
assert module.form_groups(**shuffled)==groups
checks=['six exact replicate/cell-type counts','support does not inflate molecule counts','library-scoped barcodes','input-order invariance','explicit Tn5 model conversions']
for label, mutate in [
 ('unknown barcode', lambda f: f['fragments'][0].update(barcode='unknown')),
 ('invalid interval', lambda f: f['fragments'][0].update(start=-1)),
 ('duplicate cell metadata', lambda f: f['cells'].append(copy.deepcopy(f['cells'][0]))),
 ('duplicate fragment row', lambda f: f['fragments'].append(copy.deepcopy(f['fragments'][0]))),
 ('unknown replicate', lambda f: f['libraries'][0].update(biological_replicate_id='UNSPECIFIED')),
 ('unknown reference', lambda f: f['libraries'][0].update(reference_fasta_sha256='UNSPECIFIED')),
 ('include missing ATAC', lambda f: f['cells'][0].update(atac_present=False)),
]:
 bad=copy.deepcopy(fixture);mutate(bad)
 try: module.form_groups(**bad)
 except ValueError: checks.append(label+' rejected')
 else: raise AssertionError(label)
# Libraries from independent preparations stay separate even within one biological replicate.
extra=copy.deepcopy(fixture);lib=copy.deepcopy(extra['libraries'][0]);lib['library_id']='L3';extra['libraries'].append(lib)
cell=copy.deepcopy(extra['cells'][0]);cell['library_id']='L3';extra['cells'].append(cell)
fragment=copy.deepcopy(extra['fragments'][0]);fragment['library_id']='L3';extra['fragments'].append(fragment)
assert len(module.form_groups(**extra))==7
for lib in extra['libraries']:
 if lib['library_id'] in ('L1','L3'):lib['technical_merge_group']='explicit-tech'
assert len(module.form_groups(**extra))==6
extra['libraries'][-1]['biological_replicate_id']='r2'
try:module.form_groups(**extra)
except ValueError:checks.append('cross-replicate technical merge rejected')
else:raise AssertionError('cross-replicate merge')
(ROOT/'results/contracts/pseudobulk.json').write_text(json.dumps({'checks':checks,'groups':groups},indent=2)+'\n')
print('PASS',len(checks),'contract checks')
