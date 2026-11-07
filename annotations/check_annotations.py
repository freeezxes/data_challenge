# -*- coding: utf-8 -*-
"""Integrity checks for authored annotation data, not a quality evaluator."""
import json,hashlib,decimal,pathlib
root=pathlib.Path(__file__).resolve().parents[1]
x=json.loads((root/'public/annotations.json').read_text())
rs=json.loads((root/'public/dataset.json').read_text())['data']['records']
records={r['conversation_id']:r for r in rs}
assert len(x['annotations'])==len(records)==65
assert len({a['conversation_id'] for a in x['annotations']})==65
assert {a['conversation_id'] for a in x['annotations']}==set(records)
assert x['provenance']['official_ground_truth'] is False
assert x['provenance']['human_reviewed'] is False
assert x['provenance']['source_dataset_sha256']==hashlib.sha256((root/'public/dataset.json').read_bytes()).hexdigest()
assert sum(c['weight'] for c in x['criteria'])==100
D=decimal.Decimal
for a in x['annotations']:
 r=records[a['conversation_id']]
 assert a['transcript_sha256']==hashlib.sha256(json.dumps(r['transcript'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 assert len(a['criteria'])==8
 earned=D(0);applicable=D(0)
 for c,v in zip(x['criteria'],a['criteria']):
  assert c['id']==v['criterion_id']
  assert v['rationale'] and v['evidence']
  assert bool(v['review_note'])==v['review_required']
  if v['value']=='N/A':
   assert c['allow_na']
   assert v['raw_points'] is None
  else:
   applicable+=D(c['weight'])
   if c['kind']=='scale':
    assert v['value'] in c['scale']
    points=D(v['value'])/D(c['max'])*D(c['weight'])
   else:
    assert v['value'] in ['Да','Нет']
    points=D(c['weight']) if v['value']=='Да' else D(0)
   assert abs(D(str(v['raw_points']))-points)<D('0.000001')
   earned+=points
  for e in v['evidence']:
   m=r['transcript'][e['message_index']-1]
   assert all(e[k]==m[k] for k in ['speaker','timestamp'])
   assert e['quote']==m['text']
  if v['inspected_range']:
   start,end=v['inspected_range'];assert 1<=start<=end<=len(r['transcript'])
 score=(earned/applicable*100).quantize(D('0.01'),rounding=decimal.ROUND_HALF_UP) if applicable else None
 assert D(str(a['total_score']))==score
 assert a['critical_flag']==(a['criteria'][7]['value']=='Да')
 assert a['review_required']==any(v['review_required'] for v in a['criteria'])
 assert a['applicable_weight']==int(applicable)
 assert 0<=a['total_score']<=100
 first_op=next(i+1 for i,m in enumerate(r['transcript']) if m['speaker']=='operator')
 assert a['criteria'][0]['evidence'][0]['message_index']==first_op
# Known structural edge cases: exclusions, normalization, independent critical flag.
byid={a['conversation_id']:a for a in x['annotations']}
assert byid['p1-ru-025']['criteria'][3]['value']=='N/A'
assert byid['p1-en-009']['criteria'][6]['value']=='N/A'
assert byid['p1-ru-023']['critical_flag'] and byid['p1-ru-023']['total_score']>0
assert byid['p1-ru-001']['total_score']==92.31
assert x['counts']['annotated']==65
assert x['counts']['critical']==sum(a['critical_flag'] for a in x['annotations'])
print('OK: 65 dialogs; 520 criterion decisions; exact source quotes, hashes, allowed values, N/A and independent score arithmetic verified.')
print('Integrity checks do not establish semantic agreement with organizers.')
