"""Deterministic contract prototype, deliberately separate from production workflows."""
from collections import defaultdict
import hashlib
import json
import re

BIO_FIELDS = ('study_id','biological_sample_id','biological_replicate_id','species',
              'assembly','reference_id','reference_fasta_sha256','annotation_sha256',
              'accession_cultivar_ecotype','tissue','developmental_stage','treatment',
              'condition','assay','protocol','coordinate_offsets')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def model_cuts(start, end, offsets):
    """ChromBPNet target +4/-4 intervals, then BED's last base is end-1."""
    plus, minus = offsets
    cuts = (start + 4 - plus, end - 4 - minus - 1)
    if cuts[0] < 0 or cuts[1] < cuts[0]:
        raise ValueError('invalid shifted interval')
    return cuts


def form_groups(libraries, cells, fragments):
    library_map = {}; merge_identities = {}
    for library in libraries:
        for field in ('study_id','biological_sample_id','biological_replicate_id','library_id','assembly','reference_id'):
            if not library.get(field) or library[field] in ('UNKNOWN','UNSPECIFIED'):
                raise ValueError('explicit biological/reference identity required')
        lid = library['library_id']
        if lid in library_map: raise ValueError('duplicate library ID')
        identity = {key: library[key] for key in BIO_FIELDS}
        if not re.fullmatch('[0-9a-f]{64}', library['reference_fasta_sha256']):
            raise ValueError('reference checksum required')
        merge = library['technical_merge_group']
        if merge is not None:
            merge_key = (library['study_id'], merge)
            if merge_key in merge_identities and merge_identities[merge_key] != identity:
                raise ValueError('incompatible technical merge including replicate/reference identity')
            merge_identities[merge_key] = identity
        library_map[lid] = library
    cell_map = {}
    groups = {}
    for cell in cells:
        key = (cell['library_id'],cell['barcode'])
        if key in cell_map: raise ValueError('duplicate cell identity')
        if key[0] not in library_map: raise ValueError('unknown library')
        if cell['atac_include'] and not cell['atac_present']: raise ValueError('included absent ATAC')
        if cell['rna_include'] and not cell['rna_present']: raise ValueError('included absent RNA')
        library = library_map[key[0]]
        identity = {field:library[field] for field in BIO_FIELDS}
        identity.update(cell_type=cell['harmonized_label'],
                        library_scope={'merge':library['technical_merge_group']} if library['technical_merge_group'] else {'library':key[0]})
        task_id = 'genesis-task-v1-' + hashlib.sha256(canonical(identity).encode()).hexdigest()
        cell_map[key] = (cell,task_id)
        if cell['atac_include']:
            group=groups.setdefault(task_id, {'task_id':task_id,'identity':identity,'cells':[], 'fragments':[], 'usable_fragments':0,'qc_thresholds':'UNSPECIFIED'})
            group['cells'].append(cell)
    seen=set()
    for fragment in fragments:
        key=(fragment['library_id'],fragment['barcode'])
        if key not in cell_map: raise ValueError('unknown barcode')
        start,end,support=fragment['start'],fragment['end'],fragment['support']
        if any(type(v) is not int for v in (start,end,support)) or start<0 or end<=start or support<1:
            raise ValueError('invalid fragment coordinates/support')
        duplicate_key=(*key,fragment['chrom'],start,end)
        if duplicate_key in seen: raise ValueError('duplicate unique-fragment row')
        seen.add(duplicate_key)
        cell,task_id=cell_map[key]
        if cell['atac_include']:
            groups[task_id]['fragments'].append(fragment)
            groups[task_id]['usable_fragments']+=1
    for group in groups.values():
        group['cells'].sort(key=lambda c:(c['library_id'],c['barcode']))
        group['fragments'].sort(key=lambda f:(f['chrom'],f['start'],f['end'],f['library_id'],f['barcode']))
    return sorted(groups.values(),key=lambda g:g['task_id'])
