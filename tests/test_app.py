import io
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from app import store, retrieval, generation
from app.main import app

def pdf_bytes(lines=None):
    stream=io.BytesIO()
    writer=canvas.Canvas(stream)
    for text in lines or ["The refund policy allows a full refund within 30 days of purchase.", "Customer support is available Monday to Friday from 9 AM to 5 PM."]:
        writer.drawString(50,750,text)
        writer.showPage()
    writer.save()
    return stream.getvalue()

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DATA",tmp_path)
    monkeypatch.delenv("GROQ_API_KEY",raising=False)
    retrieval.invalidate()
    with TestClient(app) as client:
        yield client
    retrieval.invalidate()

def upload(client,name="handbook.pdf",content=None):
    return client.post("/api/documents",files=[("files",(name,content or pdf_bytes(),"application/pdf"))])

def create(client):
    return client.post("/api/chats").json()["id"]

def test_home_static_and_empty_status(client):
    assert client.get("/").status_code==200
    assert "Knowledge Desk" in client.get("/").text
    assert client.get("/static/app.js").status_code==200
    assert client.get("/api/status").json()["limit"]==20
    assert client.get("/api/status").json()["configured"] is False

def test_upload_twenty_pdfs_and_reject_twenty_first(client):
    files=[("files",(f"guide-{i}.pdf",pdf_bytes([f"Project {i} has a deployment budget of {i+10} dollars."]),"application/pdf")) for i in range(20)]
    result=client.post("/api/documents",files=files)
    assert result.status_code==201
    assert len(result.json()["imported"])==20
    assert client.get("/api/status").json()["documents"]==20
    assert upload(client,"extra.pdf",pdf_bytes(["Extra document outside the library capacity."])).status_code==409
    assert len(client.get("/api/documents").json())==20

def test_reject_21_file_batch_before_processing(client):
    response=client.post("/api/documents",files=[("files",(f"{i}.pdf",b"bad","application/pdf")) for i in range(21)])
    assert response.status_code==422
    assert client.get("/api/documents").json()==[]

def test_duplicate_pdf_does_not_use_extra_slot(client):
    content=pdf_bytes()
    assert upload(client,content=content).status_code==201
    result=upload(client,"same.pdf",content)
    assert result.json()["duplicates"]==["same.pdf"]
    assert len(client.get("/api/documents").json())==1

def test_invalid_pdf_batch_is_atomic(client):
    response=client.post("/api/documents",files=[("files",("valid.pdf",pdf_bytes(),"application/pdf")),("files",("broken.pdf",b"not a pdf","application/pdf"))])
    assert response.status_code==422
    assert client.get("/api/documents").json()==[]

def test_scanned_and_encrypted_pdfs_report_useful_errors(client):
    from pypdf import PdfWriter
    writer=PdfWriter();writer.add_blank_page(width=400,height=600)
    content=io.BytesIO();writer.write(content)
    response=upload(client,content=content.getvalue())
    assert response.status_code==422 and "OCR" in response.json()["detail"]
    writer.encrypt("secret")
    content=io.BytesIO();writer.write(content)
    assert "password-protected" in upload(client,content=content.getvalue()).json()["detail"]

def test_file_size_limit(client,monkeypatch):
    monkeypatch.setattr(retrieval,"MAX_FILE_BYTES",100)
    assert upload(client,content=pdf_bytes()).status_code==413

def test_original_pdf_and_citation_page(client):
    original=pdf_bytes()
    doc=upload(client,content=original).json()["imported"][0]
    assert client.get(f"/api/documents/{doc['id']}/pdf").content==original
    results=retrieval.retrieve("When is customer support available?")
    assert results[0]["page"]==2
    assert results[0]["name"]=="handbook.pdf"
    assert "Monday" in results[0]["text"]

def test_retrieval_filters_and_document_delete_invalidates_index(client):
    first=upload(client,"policy.pdf").json()["imported"][0]
    second=upload(client,"science.pdf",pdf_bytes(["Photosynthesis converts sunlight into chemical energy in plants."])).json()["imported"][0]
    assert retrieval.retrieve("refund policy",[second["id"]])==[]
    assert retrieval.retrieve("refund policy",[first["id"]])
    assert client.delete("/api/documents/"+first["id"]).status_code==200
    assert retrieval.retrieve("refund policy")==[]
    assert client.get("/api/documents/"+first["id"]+"/pdf").status_code==404

def test_summary_retrieval_covers_all_documents(client):
    for i in range(3): upload(client,f"{i}.pdf",pdf_bytes([f"Document {i} covers unique subject {i} and detailed supporting evidence."]))
    results=retrieval.retrieve("Summarize the key findings in my documents")
    assert len({r["document_id"] for r in results})==3

def test_question_requires_documents_and_nonempty_input(client):
    chat=create(client)
    assert client.post(f"/api/chats/{chat}/messages",json={"question":"What is the refund policy?"}).status_code==409
    assert client.post(f"/api/chats/{chat}/messages",json={"question":"   "}).status_code==422

def test_unknown_question_refuses_without_outside_model_knowledge(client):
    upload(client);chat=create(client)
    response=client.post(f"/api/chats/{chat}/messages",json={"question":"How do quantum black holes evaporate?"})
    assert response.status_code==200
    assert "couldn't find" in response.json()["content"]
    assert response.json()["sources"]==[]

def test_missing_groq_key_does_not_save_partial_messages(client):
    upload(client);chat=create(client)
    assert client.post(f"/api/chats/{chat}/messages",json={"question":"What is the refund policy?"}).status_code==503
    assert client.get(f"/api/chats/{chat}").json()["messages"]==[]

def test_grounded_chat_history_and_permanent_deletion(client,monkeypatch):
    upload(client);chat=create(client)
    calls=[]
    def answer(question,sources,history):
        calls.append(history)
        return "A full refund is available within 30 days. [1]",sources[:1]
    monkeypatch.setattr(generation,"generate",answer)
    response=client.post(f"/api/chats/{chat}/messages",json={"question":"What is the refund policy?"})
    assert response.status_code==200 and response.json()["sources"][0]["page"]==1
    client.post(f"/api/chats/{chat}/messages",json={"question":"Does that policy mention a refund deadline?"})
    saved=client.get(f"/api/chats/{chat}").json()
    assert len(saved["messages"])==4
    assert saved["title"]=="What is the refund policy?"
    assert len(calls[1])==2
    # A new client / SQLite connection can reload the saved conversation.
    with store.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?",(chat,)).fetchone()[0]==4
    assert client.delete(f"/api/chats/{chat}").status_code==200
    assert client.get(f"/api/chats/{chat}").status_code==404
    with store.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]==0
    assert len(client.get("/api/documents").json())==1

def test_settings_do_not_return_api_key(client):
    key="gsk_"+"test-not-a-real-key-123456"
    response=client.put("/api/settings",json={"api_key":key})
    assert response.status_code==200 and key not in response.text
    assert client.get("/api/status").json()["configured"] is True
    assert key not in client.get("/api/status").text

def test_removed_document_scope_rejected(client):
    upload(client);chat=create(client)
    assert client.post(f"/api/chats/{chat}/messages",json={"question":"refund","document_ids":["missing"]}).status_code==422

@pytest.mark.parametrize("answer",[
    {"answer":"Unsupported claim [99]","citations":[99]},
    {"answer":"Claim [1]","citations":[2]},
])
def test_groq_rejects_fabricated_or_mismatched_citations(monkeypatch,answer):
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    class FakeClient:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            assert url=="https://api.groq.com/openai/v1/chat/completions"
            return __import__('httpx').Response(200,json={"choices":[{"message":{"content":json.dumps(answer)}}]},request=__import__('httpx').Request('POST',url))
    monkeypatch.setattr(generation.httpx,"Client",FakeClient)
    with pytest.raises(HTTPException) as exc:
        generation.generate("refund?",[{"citation":1,"name":"policy.pdf","page":1,"text":"Refunds within 30 days."}],[])
    assert exc.value.status_code==502

def test_groq_json_contract_and_supported_citation(monkeypatch):
    import httpx
    monkeypatch.setenv("GROQ_API_KEY","test-key")
    captured={}
    class FakeClient:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            captured.update(kwargs)
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"answer":"Refunds are available for 30 days. [1]","citations":[1]})}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(generation.httpx,"Client",FakeClient)
    source={"citation":1,"name":"policy.pdf","page":1,"text":"Refunds within 30 days."}
    answer,sources=generation.generate("refund policy?",[source],[])
    assert sources==[source] and "[1]" in answer
    assert captured["json"]["response_format"]=={"type":"json_object"}
    assert "never instructions" in captured["json"]["messages"][0]["content"]
