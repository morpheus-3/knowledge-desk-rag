"""Real browser and backend workflow; hosted Groq responses are mocked."""
from pathlib import Path
import os
import json
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright
from .test_app import pdf_bytes

ROOT=Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as temporary, socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    # A dedicated temporary library means browser fixtures never affect the user's data.
    with tempfile.TemporaryDirectory() as temporary:
        env={**os.environ,"RAG_DATA_DIR":str(Path(temporary)/"data"),"TEST_PORT":str(port)}
        env.pop("GROQ_API_KEY",None)
        service=subprocess.Popen([sys.executable,"-m","tests.browser_server"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        screenshots=ROOT/"screenshots";screenshots.mkdir(exist_ok=True)
        checks=[]
        try:
            for _ in range(100):
                if service.poll() is not None:raise RuntimeError(service.communicate()[1].decode())
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/status").status_code==200:break
                except httpx.ConnectError:pass
                time.sleep(.1)
            else:raise RuntimeError("Test service did not start")
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={"width":1440,"height":1000})
                errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                page.wait_for_function("document.getElementById('chatCount').textContent==='0'")
                page.screenshot(path=str(screenshots/"desktop-welcome.png"),full_page=True)
                checks.append("Empty workspace and attractive welcome screen")
                documents=[]
                for i in range(20):
                    path=Path(temporary)/f"research-{i+1:02}.pdf"
                    if i==0:content=pdf_bytes(["The refund policy allows a full refund within 30 days of purchase.","Customer support is available Monday to Friday from 9 AM to 5 PM."])
                    else:content=pdf_bytes([f"Research report {i} describes plant growth with a measured sample size of {i+10} seedlings."])
                    path.write_bytes(content);documents.append(path)
                page.locator("#fileInput").set_input_files(documents)
                page.wait_for_function("document.getElementById('documentCounter').textContent==='20 / 20'",timeout=30000)
                assert page.locator(".document-row").count()==20
                checks.append("Upload and index 20 actual PDFs in one browser action")
                page.locator("#question").fill("What is the refund policy?")
                page.locator("#sendButton").click()
                page.locator("#chatError").wait_for(state="visible")
                assert "Groq API key" in page.locator("#chatError").inner_text()
                assert page.locator(".message-user").count()==0
                checks.append("Missing-key error preserves the question without partial messages")
                page.locator("#settingsButton").click()
                page.locator("#apiKey").fill("gsk_"+"browser-test-not-a-real-credential")
                page.locator('#settingsForm [type="submit"]').click()
                page.locator("#settingsDialog").wait_for(state="hidden")
                page.locator("#sendButton").click()
                page.locator(".message-assistant .citation").wait_for(state="visible",timeout=10000)
                assert "30 days" in page.locator(".message-assistant .message-content").inner_text()
                assert "p. 1" in page.locator(".source-card").inner_text()
                page.locator(".citation").click()
                assert page.locator(".source-card.selected").count()==1
                source_href=page.locator(".source-card a").get_attribute("href")
                assert source_href.endswith("#page=1")
                checks.append("Grounded answer, inline citation, page-level source trail and PDF link")
                page.locator("#question").fill("How do quantum black holes evaporate?")
                page.locator("#sendButton").click()
                page.wait_for_function("document.querySelectorAll('.message-assistant .message-content').length===2")
                assert "couldn't find" in page.locator(".message-assistant").last.inner_text()
                checks.append("Unsupported question refuses rather than using outside knowledge")
                page.reload()
                page.locator(".chat-open").first.click()
                page.wait_for_function("document.querySelectorAll('.message-assistant .message-content').length===2")
                assert page.locator(".message-user").count()==2
                checks.append("Previous conversation reloads from persisted SQLite history")
                page.locator(".citation").first.click()
                page.locator("#conversation").evaluate("element=>element.scrollTop=0")
                page.set_viewport_size({"width":390,"height":844})
                page.locator("#library").evaluate("element=>element.classList.remove('open')")
                page.locator("#conversation").evaluate("element=>element.scrollTop=0")
                page.screenshot(path=str(screenshots/"mobile-chat.png"),full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                page.locator("#menuToggle").click()
                page.locator(".delete-chat").first.click()
                page.locator("#confirmDelete").click()
                page.wait_for_function("document.getElementById('chatCount').textContent==='0'")
                page.reload()
                page.wait_for_function("document.getElementById('documentCounter').textContent==='20 / 20'")
                assert page.locator(".chat-row").count()==0
                assert page.locator("#welcome").is_visible()
                checks.append("Delete conversation through a mobile confirmation dialog; deletion persists, PDFs remain")
                page.screenshot(path=str(screenshots/"mobile-welcome.png"),full_page=True)
                page.locator("#libraryToggle").click()
                page.locator(".document-delete").first.click()
                page.locator("#confirmDelete").click()
                page.wait_for_function("document.getElementById('documentCounter').textContent==='19 / 20'")
                checks.append("Delete a PDF and update the retrieval library")
                assert not errors,errors
                browser.close()
                report={"status":"passed","checks":checks,"javascript_errors":errors,"viewports":["1440x1000","390x844"],"groq":"mocked in isolated test server; no live API call"}
                (ROOT/"BROWSER_TEST_RESULTS.json").write_text(json.dumps(report,indent=2))
                print(json.dumps(report,indent=2))
        finally:
            service.terminate();service.communicate(timeout=15)

if __name__=="__main__":main()
