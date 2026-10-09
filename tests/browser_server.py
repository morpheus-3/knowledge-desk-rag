"""Test-only server with a deterministic, explicitly mocked Groq response."""
import os
from app.main import app
from app import generation

original = generation.generate

def fake_answer(question,sources,history):
    if not os.environ.get("GROQ_API_KEY"):
        return original(question,sources,history)
    source=sources[0]
    return f"According to your document, {source['text']} [{source['citation']}]",[source]

generation.generate=fake_answer

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=int(os.environ["TEST_PORT"]),log_level="warning")
