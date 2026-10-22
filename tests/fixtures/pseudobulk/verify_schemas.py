import json
from pathlib import Path
import jsonschema
root=Path(__file__).resolve().parents[2]
fixture=json.loads((root/'fixtures/pseudobulk/fixture.json').read_text())
counts={}
for key,schema_name in [('libraries','sample-metadata-schema.yaml'),('cells','scatac-metadata-schema.yaml')]:
 schema=json.loads((root/'design'/schema_name).read_text());jsonschema.Draft202012Validator.check_schema(schema);validator=jsonschema.Draft202012Validator(schema)
 for record in fixture[key]:validator.validate(record)
 counts[key]=len(fixture[key])
(root/'results/contracts/metadata-schema.json').write_text(json.dumps(counts,indent=2)+'\n')
print('PASS',counts)
