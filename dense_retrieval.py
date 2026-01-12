"""Dense retrieval system for semantic search using embeddings."""
import os
import pickle
import hashlib
import json
from typing import List, Dict, Optional, Tuple, Any
from functools import lru_cache
import faiss
import numpy as np
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    print("✅ Using langchain-huggingface embeddings")
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings
    print("✅ Using langchain-community embeddings")
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
import config
from google_sheets import sheets_manager
import time
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DenseRetrieval:
    """Dense retrieval system for semantic search with caching and error handling."""
    
    def __init__(self, enable_caching: bool = True):
        """Initialize the dense retrieval system."""
        logger.info("🤖 Initializing DenseRetrieval...")
        
        try:
            # Initialize embeddings - FIXED: removed duplicate show_progress_bar
            self.embeddings = HuggingFaceEmbeddings(
                model_name=config.EMBEDDING_MODEL,
                model_kwargs={'device': 'cpu'},
                encode_kwargs={
                    'normalize_embeddings': True,
                    'batch_size': 32
                }
            )
            logger.info(f"✅ Embeddings model loaded: {config.EMBEDDING_MODEL}")
        except Exception as e:
            logger.error(f"❌ Failed to load embeddings: {e}")
            raise
        
        self.vectorstore = None
        self.documents = []
        self._cache = {} if enable_caching else None
        self._last_refresh = 0
        self._index_loaded = False
        
        # Load or create vectorstore
        self._load_or_create_vectorstore()
    
    def _load_or_create_vectorstore(self):
        """Load existing vectorstore or create a new one."""
        vectorstore_path = config.VECTORSTORE_PATH
        
        # Check if vectorstore exists
        if os.path.exists(vectorstore_path) and os.listdir(vectorstore_path):
            try:
                # Check if sheet ID has changed
                metadata_file = os.path.join(vectorstore_path, "metadata.json")
                sheet_id_changed = False
                if os.path.exists(metadata_file):
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                            if metadata.get("sheet_id") != config.GOOGLE_SHEET_ID:
                                logger.info("🔄 Google Sheet ID changed, recreating vectorstore...")
                                sheet_id_changed = True
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"⚠️ Could not read metadata: {e}")
                
                if sheet_id_changed:
                    # Sheet ID changed, delete old vectorstore and create new one
                    import shutil
                    shutil.rmtree(vectorstore_path)
                    logger.info("🗑️  Removed old vectorstore due to sheet ID change")
                else:
                    logger.info(f"📂 Loading vectorstore from {vectorstore_path}")
                    self.vectorstore = FAISS.load_local(
                        vectorstore_path, 
                        self.embeddings,
                        allow_dangerous_deserialization=True  # Required for FAISS
                    )
                    self._index_loaded = True
                    logger.info(f"✅ Vectorstore loaded ({self.vectorstore.index.ntotal} vectors)")
                    self._last_refresh = time.time()
                    return
            except Exception as e:
                logger.warning(f"⚠️ Failed to load vectorstore: {e}. Creating new one.")
        
        # Create new vectorstore
        logger.info("🔄 Creating new vectorstore...")
        self._create_vectorstore()
    
    def _create_vectorstore(self):
        """Create a new vectorstore from Google Sheets data."""
        try:
            documents = self._get_documents_from_sheets()
            
            if documents:
                logger.info(f"📚 Indexing {len(documents)} documents...")
                self.vectorstore = FAISS.from_documents(documents, self.embeddings)
                self.documents = documents
                self._save_vectorstore()
                self._index_loaded = True
                self._last_refresh = time.time()
                logger.info(f"✅ Vectorstore created with {len(documents)} documents")
            else:
                logger.warning("⚠️ No documents to index. Creating empty vectorstore.")
                # Create empty vectorstore with welcome message
                dummy_docs = [
                    Document(
                        page_content="Welcome to our store. We offer various products and services.",
                        metadata={"source": "system", "type": "welcome"}
                    ),
                    Document(
                        page_content="You can search for products, book hotels, or ask for assistance.",
                        metadata={"source": "system", "type": "help"}
                    )
                ]
                self.vectorstore = FAISS.from_documents(dummy_docs, self.embeddings)
                self._save_vectorstore()
                self._index_loaded = True
                self._last_refresh = time.time()
                logger.info("✅ Empty vectorstore created")
                
        except Exception as e:
            logger.error(f"❌ Failed to create vectorstore: {e}")
            raise
    
    def _get_documents_from_sheets(self) -> List[Document]:
        """Get documents from Google Sheets for indexing - dynamically discovers all sheets."""
        documents = []
        
        # Discover all sheets dynamically
        try:
            all_sheets = sheets_manager.discover_sheets()
            # Filter out system sheets (orders, bookings are transactional, not searchable)
            # Only index hotel/room sheets - exclude product sheets and transactional sheets
            # Exclude sheets with 'order', 'booking', 'reservation', 'product', 'inventory', etc. in name
            # Exclude booking sheets (pending bookings, monthly booking sheets)
            # Only index room detail sheets - bot should NOT see booking tabs
            sheets_to_index = [
                s for s in all_sheets 
                if not any(excluded in s.lower() for excluded in [
                    'order', 'booking', 'reservation', 'notification', 'log',
                    'product', 'inventory', 'stock', 'food', 'menu', 'item',
                    'pending'  # Exclude pending bookings
                ])
                and not s.lower().startswith('bookings ')  # Exclude monthly booking sheets (e.g., "Bookings January 2026")
            ]
            
            # If no sheets found, try default names
            if not sheets_to_index:
                sheets_to_index = [config.HOTELS_SHEET]
                logger.info(f"⚠️ No sheets discovered, using defaults: {sheets_to_index}")
            else:
                logger.info(f"📋 Indexing {len(sheets_to_index)} sheets: {sheets_to_index}")
                logger.info(f"📋 Sheets being indexed: {', '.join(sheets_to_index)}")
        except Exception as e:
            logger.warning(f"⚠️ Error discovering sheets: {e}, using defaults")
            sheets_to_index = [config.HOTELS_SHEET]
        
        for sheet_name in sheets_to_index:
            try:
                logger.info(f"📥 Fetching data from {sheet_name}...")
                data = sheets_manager.read_all_data(sheet_name)
                
                if not data or len(data) < 2:
                    logger.warning(f"⚠️ No data found in {sheet_name}")
                    continue
                
                headers = data[0]
                logger.info(f"📋 Headers in {sheet_name}: {headers}")
                
                # Detect sheet type
                sheet_type = sheets_manager.detect_sheet_type(sheet_name)
                
                # Process each row
                for row_idx, row in enumerate(data[1:], start=2):
                    # Skip empty rows
                    if not any(cell.strip() for cell in row):
                        continue
                    
                    # Create row dictionary
                    row_dict = {}
                    for i, header in enumerate(headers):
                        if i < len(row):
                            row_dict[header] = row[i].strip()
                        else:
                            row_dict[header] = ""
                    
                    # Create searchable text
                    text = self._create_document_text(row_dict, sheet_name)
                    
                    # Create metadata
                    metadata = {
                        "sheet_name": sheet_name,
                        "row_index": row_idx,
                        "row_data": row_dict,
                        "headers": headers,
                        "type": sheet_type
                    }
                    
                    # Create document
                    documents.append(Document(page_content=text, metadata=metadata))
                
                logger.info(f"✅ Added {len(data)-1} rows from {sheet_name} (type: {sheet_type})")
                
            except Exception as e:
                logger.error(f"❌ Error processing {sheet_name}: {e}")
                continue
        
        return documents
    
    def _create_document_text(self, row_dict: Dict, sheet_name: str) -> str:
        """Create searchable text from row data."""
        text_parts = []
        
        # Add all non-empty fields
        for key, value in row_dict.items():
            if value and str(value).strip():
                # For certain fields, make them more prominent
                if key.lower() in ['name', 'product', 'item', 'title', 'room_type', 'location']:
                    text_parts.append(f"**{key}**: {value}")
                else:
                    text_parts.append(f"{key}: {value}")
        
        # Add sheet name as context
        if sheet_name:
            text_parts.append(f"category: {sheet_name}")
        
        return " | ".join(text_parts) if text_parts else "No description available"
    
    def _save_vectorstore(self):
        """Save vectorstore to disk."""
        try:
            os.makedirs(config.VECTORSTORE_PATH, exist_ok=True)
            self.vectorstore.save_local(config.VECTORSTORE_PATH)
            # Save sheet ID metadata to detect changes
            metadata_file = os.path.join(config.VECTORSTORE_PATH, "metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump({
                    "sheet_id": config.GOOGLE_SHEET_ID,
                    "created_at": time.time()
                }, f)
            logger.info(f"💾 Vectorstore saved to {config.VECTORSTORE_PATH}")
        except Exception as e:
            logger.error(f"❌ Failed to save vectorstore: {e}")
    
    def _get_cache_key(self, query: str, k: int, sheet_filter: Optional[str]) -> str:
        """Generate cache key for query."""
        key_data = f"{query.lower().strip()}_{k}_{sheet_filter}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def search(self, query: str, k: int = 10, sheet_filter: Optional[str] = None) -> List[Dict]:
        """Universal search method that routes to appropriate search."""
        if not self._index_loaded:
            logger.warning("⚠️ Vectorstore not loaded, returning empty results")
            return []
        
        if sheet_filter:
            # Search specific sheet
            return self._semantic_search(query, k, sheet_filter) or self._keyword_search(query, k, sheet_filter)
        else:
            # Search all sheets
            return self.search_all(query, k)
    
    def search_hotels(self, query: str, k: int = 5) -> List[Dict]:
        """Search for hotels - works with any hotel-like sheet."""
        if not self._index_loaded:
            logger.warning("⚠️ Vectorstore not loaded, returning empty results")
            return []
        
        # Find hotel sheets dynamically
        try:
            all_sheets = sheets_manager.discover_sheets()
            hotel_sheets = [
                s for s in all_sheets 
                if sheets_manager.detect_sheet_type(s) == 'hotel'
                and 'booking' not in s.lower()  # Exclude booking sheets
                and 'reservation' not in s.lower()  # Exclude reservation sheets
            ]
            
            if not hotel_sheets:
                # Fallback to config
                hotel_sheets = [config.HOTELS_SHEET]
        except:
            hotel_sheets = [config.HOTELS_SHEET]
        
        # Search all hotel sheets
        all_results = []
        for sheet in hotel_sheets:
            results = self._semantic_search(query, k, sheet) or self._keyword_search(query, k, sheet)
            all_results.extend(results)
        
        # Sort by score and return top k
        all_results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return all_results[:k]
    
    def _semantic_search(self, query: str, k: int, sheet_filter: str) -> List[Dict]:
        """Perform semantic search with caching."""
        # Use cache if available
        if self._cache is not None:
            cache_key = self._get_cache_key(query, k, sheet_filter)
            if cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if time.time() - timestamp < 60:  # Cache for 60 seconds
                    logger.debug(f"📦 Cache hit for: {query}")
                    return cached_result
        
        try:
            # Perform similarity search
            results = self.vectorstore.similarity_search_with_score(query, k=k*2)
            
            formatted_results = []
            for doc, score in results:
                metadata = doc.metadata
                
                # Apply sheet filter
                if metadata.get("sheet_name") != sheet_filter:
                    continue
                
                # Convert FAISS distance to similarity score (lower is better)
                # FAISS returns L2 distance, so we convert to similarity
                similarity_score = 1.0 / (1.0 + float(score))
                
                # Filter out very low similarity matches
                if similarity_score < 0.1:  # Adjust threshold as needed
                    continue
                
                row_data = metadata.get("row_data", {})
                
                formatted_results.append({
                    "content": doc.page_content,
                    "score": similarity_score,
                    "similarity": f"{similarity_score:.3f}",
                    "metadata": metadata,
                    "row_data": row_data,
                    "sheet_name": metadata.get("sheet_name"),
                    "row_index": metadata.get("row_index")
                })
                
                if len(formatted_results) >= k:
                    break
            
            # Cache the result
            if self._cache is not None and formatted_results:
                cache_key = self._get_cache_key(query, k, sheet_filter)
                self._cache[cache_key] = (formatted_results, time.time())
                
                # Clean old cache entries periodically
                if len(self._cache) > 1000:
                    self._clean_cache()
            
            logger.info(f"🔍 Semantic search '{query}' found {len(formatted_results)} results")
            return formatted_results
            
        except Exception as e:
            logger.error(f"❌ Semantic search error for '{query}': {e}")
            return []
    
    def _keyword_search(self, query: str, k: int, sheet_name: str) -> List[Dict]:
        """Fallback keyword search."""
        try:
            data = sheets_manager.read_all_data(sheet_name)
            if not data or len(data) < 2:
                return []
            
            headers = data[0]
            query_lower = query.lower()
            matches = []
            
            for row_idx, row in enumerate(data[1:], start=2):
                if not any(cell.strip() for cell in row):
                    continue
                
                # Create row dictionary
                row_dict = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        row_dict[header] = row[i].strip()
                    else:
                        row_dict[header] = ""
                
                # Search in all fields
                row_text = " ".join([str(v).lower() for v in row_dict.values()])
                
                # Check for exact match in name/product field
                name_field = None
                for field in ['name', 'product', 'item', 'title', 'room_type']:
                    if field in row_dict and row_dict[field]:
                        name_field = field
                        break
                
                # Score based on match quality
                score = 0.0
                
                # Exact name match gets highest score
                if name_field and query_lower in row_dict[name_field].lower():
                    score = 0.9
                # Partial name match
                elif name_field and any(word in row_dict[name_field].lower() for word in query_lower.split()):
                    score = 0.7
                # Match in any field
                elif query_lower in row_text:
                    score = 0.5
                # Partial match in any field
                elif any(word in row_text for word in query_lower.split() if len(word) > 3):
                    score = 0.3
                else:
                    continue  # No match
                
                # Create document text for consistency
                text = self._create_document_text(row_dict, sheet_name)
                
                matches.append({
                    "content": text,
                    "score": score,
                    "similarity": f"{score:.3f}",
                    "metadata": {
                        "sheet_name": sheet_name,
                        "row_index": row_idx,
                        "row_data": row_dict,
                        "headers": headers,
                        "type": "product" if sheet_name == config.PRODUCTS_SHEET else "hotel",
                        "match_type": "keyword"
                    },
                    "row_data": row_dict,
                    "sheet_name": sheet_name,
                    "row_index": row_idx
                })
                
                if len(matches) >= k:
                    break
            
            # Sort by score descending
            matches.sort(key=lambda x: x["score"], reverse=True)
            
            logger.info(f"🔍 Keyword search '{query}' found {len(matches)} results")
            return matches
            
        except Exception as e:
            logger.error(f"❌ Keyword search error: {e}")
            return []
    
    def _clean_cache(self):
        """Clean old cache entries."""
        if not self._cache:
            return
        
        current_time = time.time()
        keys_to_delete = []
        
        for key, (_, timestamp) in self._cache.items():
            if current_time - timestamp > 300:  # 5 minutes
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self._cache[key]
        
        logger.debug(f"🧹 Cleaned {len(keys_to_delete)} old cache entries")
    
    def search_all(self, query: str, k: int = 10) -> List[Dict]:
        """Search across hotel/room sheets only (excluding transactional sheets like bookings/orders and product sheets)."""
        if not self._index_loaded:
            return []
        
        try:
            results = self.vectorstore.similarity_search_with_score(query, k=k*2)
            
            formatted_results = []
            for doc, score in results:
                metadata = doc.metadata
                sheet_name = metadata.get("sheet_name", "")
                
                # Exclude transactional sheets (bookings, orders, reservations)
                if any(excluded in sheet_name.lower() for excluded in ['booking', 'order', 'reservation']):
                    continue
                
                # Exclude product sheets - only search hotel/room sheets
                sheet_type = metadata.get("type", "")
                if sheet_type == 'product' or any(excluded in sheet_name.lower() for excluded in ['product', 'inventory', 'stock', 'food', 'menu', 'item']):
                    # Only include if it's a hotel/room sheet
                    if sheet_type != 'hotel' and 'hotel' not in sheet_name.lower() and 'room' not in sheet_name.lower():
                        continue
                
                similarity_score = 1.0 / (1.0 + float(score))
                
                if similarity_score < 0.1:
                    continue
                
                row_data = metadata.get("row_data", {})
                
                formatted_results.append({
                    "content": doc.page_content,
                    "score": similarity_score,
                    "similarity": f"{similarity_score:.3f}",
                    "metadata": metadata,
                    "row_data": row_data,
                    "sheet_name": sheet_name,
                    "row_index": metadata.get("row_index")
                })
            
            # Sort by score
            formatted_results.sort(key=lambda x: x["score"], reverse=True)
            return formatted_results[:k]
            
        except Exception as e:
            logger.error(f"❌ Search all error: {e}")
            return []
    
    def refresh_index(self, force: bool = False):
        """Refresh the vectorstore index with latest data from Google Sheets."""
        try:
            # Check if refresh is needed (every 30 minutes unless forced)
            if not force and (time.time() - self._last_refresh) < 1800:
                logger.info("⏰ Vectorstore is recent, skipping refresh")
                return
            
            logger.info("🔄 Refreshing vectorstore index...")
            self._create_vectorstore()
            
            # Clear cache
            if self._cache:
                self._cache.clear()
            
            logger.info("✅ Vectorstore refreshed successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to refresh index: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the vectorstore."""
        stats = {
            "index_loaded": self._index_loaded,
            "last_refresh": self._last_refresh,
            "cache_size": len(self._cache) if self._cache else 0,
            "documents_count": len(self.documents)
        }
        
        if self.vectorstore and hasattr(self.vectorstore, 'index'):
            stats["vector_count"] = self.vectorstore.index.ntotal
        
        return stats

# Global instance with lazy initialization
_dense_retrieval_instance = None

def get_dense_retrieval():
    """Get or create the global dense retrieval instance."""
    global _dense_retrieval_instance
    if _dense_retrieval_instance is None:
        try:
            _dense_retrieval_instance = DenseRetrieval(enable_caching=True)
            logger.info("✅ DenseRetrieval initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DenseRetrieval: {e}")
            # Create a simple working instance
            class SimpleRetrieval:
                def __init__(self):
                    self._index_loaded = False
                    self.documents = []
                    self._cache = {}
                
                def search_hotels(self, query, k=5):
                    return []
                
                def search_all(self, query, k=10):
                    return []
                
                def refresh_index(self, force=False):
                    pass
                
                def get_stats(self):
                    return {
                        "index_loaded": False,
                        "error": "Using fallback mode"
                    }
            
            _dense_retrieval_instance = SimpleRetrieval()
            logger.info("⚠️ Using fallback retrieval mode")
    
    return _dense_retrieval_instance

# For backward compatibility - simple proxy
def dense_retrieval():
    """Simple function interface."""
    return get_dense_retrieval()