"""Optional inspection only: uv run --with onnx==1.17.0 python scripts/inspect-models.py."""
import json
from collections import Counter
from pathlib import Path
import onnx

root = Path(__file__).resolve().parent.parent
result = {}
for file in (root / '.local/models/Xenova/all-MiniLM-L6-v2/onnx').glob('*.onnx'):
    model = onnx.load(str(file))
    result[file.name] = {
        'producer': model.producer_name,
        'producer_version': model.producer_version,
        'operators': dict(Counter(node.op_type for node in model.graph.node)),
        'initializer_types': dict(Counter(onnx.TensorProto.DataType.Name(t.data_type) for t in model.graph.initializer)),
    }
(root / 'evidence/quantization-inspection.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, indent=2))
