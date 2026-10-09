from .test_app import client,upload,create,pdf_bytes
from app import retrieval

def test_fused_ocr_keywords_and_plural_forms(client):
    upload(client,"Vedic Age.pdf",pdf_bytes(["VedicLiteratureVedasorSamhitaRigvedaSamvedaYajurvedaAtharvavedaFourVedas."]))
    upload(client,"Buddhism.pdf",pdf_bytes(["BuddhistLiteratureHinyanaBuddhismMahayanaBuddhismFourNobleTruthsEightfoldPath."]))
    results=retrieval.retrieve("name vedas")
    assert results and results[0]["name"]=="Vedic Age.pdf"
    assert retrieval.retrieve("tell me about buddhism")[0]["name"]=="Buddhism.pdf"
    assert "Vedic Literature" in results[0]["text"]
    assert retrieval.retrieve("How do quantum black holes evaporate?")==[]

def test_greeting_is_not_treated_as_missing_document_fact(client):
    chat=create(client)
    result=client.post(f"/api/chats/{chat}/messages",json={"question":"hi"})
    assert result.status_code==200 and result.json()["content"].startswith("Hi!")
    assert result.json()["sources"]==[]

def test_watermark_only_text_has_quality_warning(client):
    upload(client,"scan.pdf",pdf_bytes(["Course notes watermark 1234567890."]))
    document=client.get("/api/documents").json()[0]
    assert document["limited_text"] is True
    assert document["text_characters"]<150
