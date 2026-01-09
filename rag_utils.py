import os
import time
import chromadb
# from chromadb.utils import embedding_functions
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.environ.get("CHROMA_PATH", "chroma_store")
if not os.path.isabs(CHROMA_PATH):
      CHROMA_PATH = os.path.join(os.getcwd(), CHROMA_PATH)

CHROMA_COLLECTION = "executive_memory_mistral"
# Mistral Config
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = os.getenv("MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions")
MISTRAL_EMBED_URL = "https://api.mistral.ai/v1/embeddings"
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")

SYSTEM_PROMPT = "You are Seva-Sakha, an executive assistant for the CEO. Be concise and action oriented. Use the provided context to answer questions accurately."

# Global state
memory_collection = None

class MistralEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:
        if not MISTRAL_API_KEY:
            print("Error: MISTRAL_API_KEY not found.")
            return []
        
        # Mistral embedding API expects 'input' as list of strings
        # Remove newlines as recommended for some models, though Mistral is robust.
        # We process in batches if needed, but for now simple pass-through.
        headers = {
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "model": "mistral-embed",
            "input": input
        }
        
        try:
            resp = requests.post(MISTRAL_EMBED_URL, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                # Ensure correct order
                embeddings = [item['embedding'] for item in data]
                return embeddings
            else:
                print(f"Mistral Embed Error: {resp.status_code} - {resp.text}")
                return []
        except Exception as e:
            print(f"Embedding failed: {e}")
            return []

def init_chroma():
    global memory_collection
    print("Initializing Chroma at:", CHROMA_PATH)
    
    # Disable telemetry to avoid startup errors
    from chromadb.config import Settings
    chroma_client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False)
    )
    
    embedding_fn = MistralEmbeddingFunction()
    memory_collection = chroma_client.get_or_create_collection(name=CHROMA_COLLECTION, embedding_function=embedding_fn)
    print("Chroma collection ready:", memory_collection.name)

def init_llm():
    # Deprecated: Local LLM is replaced by Mistral API
    print("Using Mistral API for LLM.")

def chunk_text(text: str, chunk_size:int=900, overlap:int=200) -> list[str]:
    s = (text or "").strip()
    if not s: return []
    chunks=[]; start=0; L=len(s)
    while start < L:
        end = min(start+chunk_size, L)
        chunks.append(s[start:end])
        if end==L: break
        start = max(0, end-overlap)
    return chunks

def index_into_memory(source_type: str, title: str, full_text: str, extra_meta: dict[str,any] = None) -> str:
    if memory_collection is None:
        return "Memory not initialized."
        
    full_text = (full_text or "").strip()
    if not full_text: return "Nothing to index."
    chunks = chunk_text(full_text)
    if not chunks: return "No non-empty chunks."
    
    now_iso = datetime.now(timezone.utc).isoformat()
    base_meta = {"source_type": source_type, "title": title or "", "created_at": now_iso}
    if extra_meta: 
        # Ensure all meta values are strings, ints, or floats for Chroma
        clean_extra = {k: str(v) if v is not None else "" for k,v in extra_meta.items()}
        base_meta.update(clean_extra)
    
    ids=[]; metadatas=[]; documents=[]
    import uuid
    
    for i,chunk in enumerate(chunks):
        # Use UUID to prevent collisions if multiple items indexed same second
        unique_id = f"{source_type}_{uuid.uuid4().hex[:8]}_{i}"
        ids.append(unique_id)
        m = base_meta.copy(); m["chunk_index"] = i
        metadatas.append(m)
        documents.append(chunk)
        
    try:
        print(f"Indexing {len(chunks)} chunks for {source_type}: {title}")
        memory_collection.add(documents=documents, metadatas=metadatas, ids=ids)
        return f"✅ Indexed {len(chunks)} chunks of {source_type} '{title}' into memory."
    except Exception as e:
        print(f"Indexing Error: {e}")
        return f"❌ Indexing failed: {e}"

def safe_call_llm(messages: list[dict[str,str]], max_new_tokens:int=400, temperature:float=0.2) -> str:
    if not MISTRAL_API_KEY:
        return "❌ Error: MISTRAL_API_KEY not found in .env"

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_new_tokens
    }
    
    try:
        print(f"Calling Mistral Chat API: {MISTRAL_MODEL}")
        response = requests.post(MISTRAL_API_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            return content
        else:
            return f"❌ Mistral API Error: {response.status_code} - {response.text}"
            
    except Exception as e:
        return f"❌ LLM Call Failed: {str(e)}"

def ask_seva_sakha(query: str, scope: str="all") -> str:
    q = (query or "").strip()
    if not q: return "Please enter a question."
    
    where = None
    if scope and scope != "all": 
        where = {"source_type": scope}
    
    print(f"Querying memory with scope: {scope}, where filter: {where}")
    
    try:
        # Increase n_results to find more potential matches
        res = memory_collection.query(query_texts=[q], n_results=8, where=where)
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        
        print(f"Found {len(docs)} documents in memory for query.")
    except Exception as e:
        print(f"Search error: {e}")
        return f"Memory search failed: {e}"
        
    if not docs:
        return f"No relevant memory found for '{scope}' scope. Please ensure you have indexed data in this category."
        
    ctx = ""
    for i, (d, m) in enumerate(zip(docs, metas), 1):
        s_type = m.get('source_type', 'unknown')
        s_title = m.get('title', 'unknown')
        ctx += f"--- Result {i} (Category: {s_type}, Title: {s_title}) ---\n{d}\n\n"
        
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Based on the following context, please answer the question: {q}\n\nContext:\n{ctx}\n\nAnswer:"}
    ]
    return safe_call_llm(messages, max_new_tokens=500)
