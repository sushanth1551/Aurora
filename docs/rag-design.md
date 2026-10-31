# GraphRAG and vector retrieval design

## Status
The `rag/` modules define future boundaries only. No document index, embedding
provider, vector database, graph retriever, or grounded answer generation is
operational.

## Ingestion
1. Preserve canonical source and retrieval metadata.
2. Normalize text without discarding section or source boundaries.
3. Chunk with bounded size/overlap and stable chunk IDs.
4. Generate embeddings through an injected provider.
5. Store vector plus source ID, URL, timestamp, and access metadata.
6. Extract graph entities/claims and link them back to the same source.

## Retrieval
Parse a question into entities, relationships, and semantic concepts. Retrieve
bounded graph neighbors and vector passages, apply access filters, deduplicate,
and rerank where justified. The synthesis layer should use only retrieved
evidence, cite it, and disclose missing or contradictory context.

## Quality and safety
Treat indexed documents as untrusted data, not instructions. Limit retrieval
size, redact sensitive data according to policy, evaluate recall/precision with
curated queries, track embedding/index versions, and never interpret empty
retrieval as proof that a claim is false.
