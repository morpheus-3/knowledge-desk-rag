import hashlib
import io
import re
import threading
import uuid
import numpy as np
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer, ENGLISH_STOP_WORDS
from sklearn.metrics.pairwise import cosine_similarity
from fastapi import HTTPException
from .store import db

MAX_DOCUMENTS = 20
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
INDEX_LOCK = threading.RLock()
_index = None

QUERY_FILLERS = {"tell", "explain", "describe", "name", "list", "please", "give", "know", "mention", "does", "did", "information", "document", "documents", "pdf", "pdfs"}

def normalize_text(text):
    """Restore common boundaries lost by handwritten-note OCR/PDF extraction."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d)|(\d)([A-Za-z])", lambda m: " ".join(v for v in m.groups() if v), text)
    return re.sub(r"[ \t]+", " ", text).strip()

def query_terms(question):
    return {term for term in re.findall(r"\w+", question.lower()) if len(term)>2 and term not in ENGLISH_STOP_WORDS and term not in QUERY_FILLERS}

def term_count(term, text):
    words = re.findall(r"\w+", text.lower())
    variants = [term]
    if len(term)>=5 and term.endswith("s"):
        variants.append(term[:-1])
    # Long keywords (or a plural's stem) can occur in OCR-joined words.
    return sum(any(v==word or (len(v)>=5 or v!=term) and v in word for v in variants) for word in words)

def term_matches(term,text):
    return term_count(term,text)>0

def invalidate():
    global _index
    with INDEX_LOCK:
        _index = None

def chunks(text, size=1100, overlap=180):
    text = re.sub(r"[ \t]+", " ", text).strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind(". ", start+size//2, end), text.rfind("\n", start+size//2, end))
            if boundary > start:
                end = boundary+1
        part = text[start:end].strip()
        if len(part) >= 15:
            yield part
        if end == len(text):
            break
        start = max(start+1, end-overlap)

def parse_pdf(name, content):
    name = name.replace("\\", "/").split("/")[-1][:140]
    if not name.lower().endswith(".pdf") or not content.startswith(b"%PDF-"):
        raise HTTPException(422, f"{name}: upload a valid PDF file.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{name}: maximum file size is 20 MB.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise HTTPException(422, f"{name}: password-protected PDFs are not supported. Upload an unlocked copy.")
        if not 1 <= len(reader.pages) <= 500:
            raise HTTPException(422, f"{name}: PDFs must contain 1–500 pages.")
        entries = []
        characters = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            characters += len(text)
            if characters > 2_000_000:
                raise HTTPException(413, f"{name}: extracted text exceeds two million characters.")
            entries.extend((number, part) for part in chunks(text))
        if not entries:
            raise HTTPException(422, f"{name}: no readable text found. Scanned PDFs need OCR before upload.")
        return {"id": str(uuid.uuid4()), "name": name, "digest": hashlib.sha256(content).hexdigest(), "pages": len(reader.pages), "bytes": len(content), "pdf": content, "chunks": entries}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, f"{name}: this PDF could not be read.")

def ingest(files):
    if not 1 <= len(files) <= MAX_DOCUMENTS:
        raise HTTPException(422, "Choose between 1 and 20 PDFs.")
    if sum(len(content) for _, content in files) > MAX_BATCH_BYTES:
        raise HTTPException(413, "Combined upload size must be at most 100 MB.")
    parsed = [parse_pdf(name, content) for name, content in files]
    imported, duplicates = [], []
    with INDEX_LOCK, db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        known = {row[0] for row in connection.execute("SELECT digest FROM documents")}
        unique = {}
        for document in parsed:
            if document["digest"] in known or document["digest"] in unique:
                duplicates.append(document["name"])
            else:
                unique[document["digest"]] = document
        if len(known)+len(unique) > MAX_DOCUMENTS:
            raise HTTPException(409, "Your library can contain up to 20 PDFs. Remove a document before adding more.")
        for document in unique.values():
            connection.execute("INSERT INTO documents(id,name,digest,pages,bytes,pdf) VALUES(:id,:name,:digest,:pages,:bytes,:pdf)", document)
            connection.executemany("INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)", [(document["id"], page, text) for page, text in document["chunks"]])
            imported.append({key: document[key] for key in ["id", "name", "pages", "bytes"]})
        invalidate()
    return {"imported": imported, "duplicates": duplicates}

def retrieve(question, document_ids=None, limit=6):
    global _index
    with INDEX_LOCK:
        if _index is None:
            with db() as connection:
                rows = [dict(row) for row in connection.execute("SELECT c.id,c.document_id,c.page,c.text,d.name FROM chunks c JOIN documents d ON c.document_id=d.id ORDER BY c.id")]
            if not rows:
                return []
            texts = [normalize_text(row["text"]) for row in rows]
            word = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b\w+\b", stop_words="english", max_features=60000)
            try:
                word_matrix = word.fit_transform(texts)
            except ValueError:
                word = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=60000)
                word_matrix = word.fit_transform(texts)
            character = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=60000, sublinear_tf=True)
            char_matrix = character.fit_transform(texts)
            _index = (rows, word, word_matrix, character, char_matrix)
        rows, word, word_matrix, character, char_matrix = _index
        if re.search(r"summari[sz]|overview|main takeaways|common themes|key (?:points|takeaways|findings)", question.lower()):
            selected = [row for row in rows if not document_ids or row["document_id"] in document_ids]
            by_document = {}
            for row in selected:
                if row["document_id"] not in by_document or (len(by_document[row["document_id"]]["text"])<250 and len(row["text"])>250):
                    by_document[row["document_id"]] = row
            return [{**row,"score":1.0,"citation":i+1} for i,row in enumerate(list(by_document.values())[:20])]
        terms = query_terms(question)
        if not terms:
            return []
        search = " ".join(sorted(terms))
        word_scores = cosine_similarity(word.transform([search]), word_matrix)[0]
        scores = .55 * word_scores + .2 * cosine_similarity(character.transform([search]), char_matrix)[0]
        matches = []
        for index,row in enumerate(rows):
            normalized = normalize_text(row["text"])
            counts = [term_count(term,normalized) for term in terms]
            body_coverage = sum(count>0 for count in counts)/len(terms)
            title_coverage = sum(term_matches(term, normalize_text(row["name"])) for term in terms)/len(terms)
            required_coverage = 2/3 if len(terms)>=3 else .5
            matches.append(body_coverage >= required_coverage or title_coverage >= required_coverage)
            scores[index] += .25*body_coverage + .18*title_coverage + .1*sum(np.log1p(min(count,8)) for count in counts)/len(terms)
        hits = []
        for index in np.argsort(-scores):
            row = rows[index]
            if document_ids and row["document_id"] not in document_ids:
                continue
            if scores[index] < .055 or not matches[index]:
                continue
            # Avoid filling context with nearly identical overlapping chunks.
            if any(hit["document_id"]==row["document_id"] and hit["page"]==row["page"] and row["text"][:100] in hit["text"] for hit in hits):
                continue
            hits.append({**row, "text": normalize_text(row["text"]), "score": round(float(scores[index]), 4), "citation": len(hits)+1})
            if len(hits) == limit:
                break
        return hits
