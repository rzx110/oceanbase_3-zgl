// Original, deterministic evaluation corpus. One independently authored note per source.
const items = [
  [
    "browser-gpu",
    "Browser GPU inference",
    "WebGPU runs neural network tensor operations on a local graphics adapter. Request a GPU adapter before creating a GPU device. FP16 execution requires the shader-f16 feature. If the adapter is unavailable, fall back to WebAssembly on the CPU.",
  ],
  [
    "wasm-fallback",
    "CPU fallback",
    "WebAssembly provides offline CPU inference when WebGPU is unavailable. Run inference in a dedicated Web Worker to keep the interface responsive. Cross-origin isolation enables SharedArrayBuffer and parallel WASM threads.",
  ],
  [
    "model-quantization",
    "Quantization tradeoffs",
    "INT8 quantization stores most neural network weights with eight bits rather than FP32 thirty-two bits. It reduces model download size and memory. Compare retrieval recall and normalized discounted cumulative gain before and after compression.",
  ],
  [
    "embedding-batches",
    "Batching embeddings",
    "Encoding several documents in one batch amortizes inference overhead. Pad sequences within a batch and choose a modest batch size to fit GPU memory. Measure documents per second with the same corpus and model.",
  ],
  [
    "pooling",
    "Sentence representations",
    "Mean pooling averages token embeddings with the attention mask. L2 normalization makes sentence vectors unit length. Use the same model revision, pooling strategy and vector dimension for both documents and queries.",
  ],
  [
    "hybrid-rrf",
    "Hybrid retrieval",
    "A hybrid search combines full-text keyword matches with vector similarity. Reciprocal rank fusion sums one divided by a rank constant plus each rank. Execute both retrieval branches and fusion inside seekdb rather than merging lists in the browser.",
  ],
  [
    "index-refresh",
    "Index visibility",
    "After inserting new document vectors, refresh the vector index before searching. Group writes into batches and refresh once after the batch. This reduces repeated refresh overhead and makes incremental imports immediately searchable.",
  ],
  [
    "collections",
    "Knowledge base isolation",
    "Each knowledge base uses its own seekdb collection. A query must use the selected collection ID. Disable collection switching during a pending write and ignore stale responses from a previous session to prevent cross-project contamination.",
  ],
  [
    "persistent-db",
    "Persistent storage",
    "A local database keeps original documents, vectors, source IDs and metadata on disk. Closing the application and reopening the same database directory must preserve the imported collection and its search results. Browser local storage is not a database substitute.",
  ],
  [
    "idempotent",
    "Idempotent imports",
    "Use stable document IDs and upsert to replace an existing document. Reimporting an unchanged file should not increase the number of stored chunks. Remove obsolete chunks when a replacement file becomes shorter.",
  ],
  [
    "pdf-pages",
    "PDF page citations",
    "Parse every physical page of a PDF, including blank pages. Store a digest of the complete PDF bytes and a one-based physical page number. A citation should identify the document revision and exact source page.",
  ],
  [
    "csv-rows",
    "CSV preservation",
    "Decode CSV bytes with strict UTF-8 and accept a leading byte order mark. Preserve column order and field strings. Validate row width and primary key uniqueness before returning any rows.",
  ],
  [
    "source-citations",
    "Evidence provenance",
    "A search result should retain the original source name and chunk number. Display the actual stored text rather than a generated answer. Provenance lets a researcher verify each finding against its source.",
  ],
  [
    "offline",
    "Offline privacy",
    "Download a public model once and serve its weights from the local computer. Both document embeddings and query embeddings are computed in the browser. Do not send text to a hosted inference API.",
  ],
  [
    "model-cache",
    "Model caching",
    "Keep a single initialized inference pipeline in a worker and reuse it across requests. Cache immutable model files by a pinned revision. Report cold loading separately from warm requests rather than mixing these timings.",
  ],
  [
    "perf-median",
    "Performance experiments",
    "Repeat an experiment several times under the same device, browser, dataset and configuration. Preserve raw latency samples and report the median. Warmup runs must be separate from measured runs.",
  ],
  [
    "recall",
    "Retrieval evaluation",
    "Recall at K is the number of relevant sources returned in the first K results divided by all relevant sources. Normalized discounted cumulative gain rewards relevant sources near the top of the ranking. Use fixed relevance judgments.",
  ],
  [
    "chunking",
    "Document chunking",
    "Split long text at paragraph or word boundaries into bounded chunks before embedding. Keep source filenames and chunk numbers with every vector. Truncating a whole book to the model token limit loses later sections.",
  ],
  [
    "browser-isolation",
    "Browser isolation headers",
    "Serve Cross-Origin-Opener-Policy same-origin and Cross-Origin-Embedder-Policy require-corp to enable cross-origin isolation. Host model and runtime assets on the same origin. These headers support multi-threaded WASM execution.",
  ],
  [
    "input-validation",
    "Vector validation",
    "Reject vectors with the wrong dimension, non-finite values or incorrect normalization. Validate the entire import payload before the first database write. Only the browser is responsible for calculating embedding vectors.",
  ],
  [
    "git-history",
    "Git submission",
    "Submit the HTTPS repository URL, branch and complete forty-character commit SHA. Preserve the frozen baseline commit in the repository history. Push the commit before requesting official evaluation.",
  ],
  [
    "sql-parameters",
    "SQL parameter safety",
    "Pass user strings as bound SQL parameters instead of concatenating them into SQL text. Validate collection identifiers separately. Restrict a local application API to the loopback interface and reject cross-origin writes.",
  ],
  [
    "retry",
    "Retry strategy",
    "Retry transient connection failures with a bounded backoff. Preserve a failed database directory and inspect server logs when initialization fails. Do not report a successful import until storage and indexing have completed.",
  ],
  [
    "gpu-memory",
    "GPU memory limits",
    "A large batch can exceed available graphics memory. Reduce batch size or switch to the WASM path on a device without a compatible GPU. Dispose the old inference session before changing precision or backend.",
  ],
  [
    "cache-http",
    "HTTP cache control",
    "Immutable model assets can use a long-lived HTTP cache policy because their revision is pinned. Application HTML should be revalidated so a deployed interface receives current source code. Cached bytes do not imply cached inference results.",
  ],
  [
    "tea",
    "Tea preparation",
    "Steep green tea leaves in hot water below boiling temperature. Remove the leaves after a short infusion to avoid excessive bitterness. Store dry tea in an airtight container away from strong odors.",
  ],
  [
    "coffee",
    "Coffee brewing",
    "Grind roasted coffee beans before brewing. Water temperature, grind size and extraction time influence espresso flavor. Clean the grinder and coffee machine regularly.",
  ],
  [
    "gardening",
    "Indoor gardening",
    "Place herbs near a bright window. Water the soil when the upper layer becomes dry. Excess standing water can damage roots, while sufficient drainage helps plants grow.",
  ],
  [
    "cycling",
    "Bicycle maintenance",
    "Check tire pressure before a bicycle ride. Lubricate the chain and inspect brake pads regularly. A helmet and working lights improve safety on the road.",
  ],
  [
    "bread",
    "Bread fermentation",
    "Bread dough rises as yeast ferments sugars and releases carbon dioxide. Temperature and fermentation time affect flavor and texture. Let the baked loaf cool before slicing.",
  ],
  [
    "stars",
    "Night sky observations",
    "Observe stars from a dark location away from city lights. A star chart helps identify constellations. Let your eyes adjust to darkness before looking through a telescope.",
  ],
  [
    "music",
    "Piano practice",
    "Practice difficult musical passages slowly with a metronome. Gradually increase tempo while maintaining consistent rhythm. Short focused practice sessions help develop accuracy.",
  ],
];
export const documents = items.map(([id, title, text]) => ({
  id,
  source: `${id}.md`,
  chunk: 0,
  text: `${title}\n${text}`,
}));
export const queries = [
  [
    "How do we run the model if no GPU adapter is available?",
    ["wasm-fallback", "browser-gpu"],
  ],
  ["How can eight bit weights reduce the model size?", ["model-quantization"]],
  ["How can we encode multiple notes together faster?", ["embedding-batches"]],
  ["How are sentence vectors pooled and normalized?", ["pooling"]],
  [
    "Where should reciprocal rank fusion combine keywords and vector matches?",
    ["hybrid-rrf"],
  ],
  ["How do newly inserted documents become searchable?", ["index-refresh"]],
  [
    "How do we prevent searches from leaking into another knowledge base?",
    ["collections"],
  ],
  [
    "Will my imported documents survive restarting the application?",
    ["persistent-db"],
  ],
  ["How can importing the same file avoid duplicate chunks?", ["idempotent"]],
  ["What identifies an exact physical page of a revised PDF?", ["pdf-pages"]],
  [
    "How should we preserve CSV columns and validate duplicate keys?",
    ["csv-rows"],
  ],
  [
    "How can I check the original source of a search finding?",
    ["source-citations"],
  ],
  ["Can text be embedded without a hosted inference service?", ["offline"]],
  ["How can repeated queries reuse an already loaded model?", ["model-cache"]],
  ["How should repeated benchmark samples be summarized?", ["perf-median"]],
  ["Which ranking metric rewards relevant sources near the top?", ["recall"]],
  [
    "Why split a long document before sending it to the embedding model?",
    ["chunking"],
  ],
  ["What headers enable parallel WebAssembly threads?", ["browser-isolation"]],
].map(([text, relevant], i) => ({ id: `q${i + 1}`, text, relevant }));
