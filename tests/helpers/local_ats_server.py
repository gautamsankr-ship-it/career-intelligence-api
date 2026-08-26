import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class LocalATS:
    """Local-only deterministic Greenhouse-like fixture for browser tests."""
    def __init__(self, review_variant="matching"):
        self.data={"visits":[],"submit":0,"session":"local-session","navigation":[],"uploads":{},"review_clicks":0}
        self.review_variant=review_variant
        self.redirect_url=""

    @staticmethod
    def page_html(stage, review_variant="matching"):
        if review_variant == "external_client_navigation" and stage == "1":
            return '<main data-portal="greenhouse"><h1>Loading application</h1><script>window.location.href="https://untrusted.example.invalid/apply";</script></main>'
        if review_variant == "portal_mismatch" and stage == "1":
            return '<main data-portal="lever"><form><label for="first">First name</label><input id="first"></form></main>'
        marker='<main data-portal="greenhouse" class="greenhouse application_form">'
        if stage == "1":
            return marker+'<form action="/greenhouse" method="get"><input type="hidden" name="stage" value="2"><label for="first">First name</label><input id="first" name="first" required><label for="last">Last name</label><input id="last" name="last" required><label for="email">Email address</label><input id="email" name="email" required><label for="phone">Phone</label><input id="phone" name="phone"><button type="submit">Continue</button></form></main>'
        if stage == "2":
            return marker+'<form action="/greenhouse" method="get"><input type="hidden" name="stage" value="3"><label for="notice">Notice period</label><input id="notice" name="notice" required><label for="auth">Authorized to work in the UK?</label><select id="auth" name="authorization" required><option>Yes</option><option>No</option></select><label for="sponsor">Visa sponsorship</label><select id="sponsor" name="sponsorship" required><option>Yes</option><option>No</option></select><button type="submit">Continue</button></form></main>'
        if stage == "3":
            script="<script>for(const id of ['cv','cover'])document.getElementById(id).addEventListener('change',e=>fetch('/observe',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({field:id,filename:e.target.files[0]?.name||''})}));</script>"
            return marker+'<form action="/greenhouse" method="get"><input type="hidden" name="stage" value="4"><label for="cv">Resume</label><input id="cv" type="file" required><label for="cover">Cover Letter</label><input id="cover" type="file"><button type="submit">Review</button></form>'+script+'</main>'
        if stage == "4":
            screening="Visa sponsorship: No"
            documents="Test_Candidate_Resume.pdf Test_Candidate_Cover_Letter.pdf"
            controls='<button type="submit">Submit Application</button>'
            if review_variant == "screening_mismatch": screening="Visa sponsorship: Yes"
            elif review_variant == "document_mismatch": documents="Wrong_Resume.pdf Test_Candidate_Cover_Letter.pdf"
            elif review_variant == "ambiguous": controls='<button type="submit">Submit Application</button><button type="submit">Submit</button>'
            elif review_variant == "missing": controls=""
            elif review_variant == "ordinary": return marker+'<form><button>Submit Application</button></form></main>'
            elif review_variant == "captcha": return marker+'<h1>CAPTCHA required</h1></main>'
            elif review_variant == "login": return marker+'<h1>Sign in to continue</h1></main>'
            elif review_variant == "mfa": return marker+'<h1>Multi-factor verification code</h1></main>'
            elif review_variant == "account": return marker+'<h1>Create an account</h1></main>'
            elif review_variant == "unexpected_success": return '<main data-portal="greenhouse"><h1>Thank you for applying</h1><p>Application received</p></main>'
            return marker+'<h1>Review your application</h1><section class="application-review">Candidate: Test Candidate test.candidate@example.invalid +447000000000. Notice period: 1 month. Authorized to work in the UK?: Yes. '+screening+'. Documents: '+documents+'</section><form action="/greenhouse" method="get"><input type="hidden" name="stage" value="success">'+controls+'</form></main>'
        if review_variant == "failure": return '<main data-portal="greenhouse"><h1>Application could not be submitted</h1><p>Please correct the highlighted errors</p></main>'
        if review_variant == "uncertain": return '<main data-portal="greenhouse"><h1>Processing</h1><p>Please wait.</p></main>'
        return '<main data-portal="greenhouse"><h1>Thank you for applying</h1><p>Application received</p></main>'

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                parsed=urlparse(self.path)
                if parsed.path != "/greenhouse":
                    self.send_response(404); self.end_headers(); return
                stage=parse_qs(parsed.query).get("stage", ["1"])[0]
                if stage == "1" and outer.review_variant == "loop" and outer.redirect_url:
                    self.send_response(302); self.send_header("Location",outer.redirect_url); self.end_headers(); return
                if stage == "1" and outer.review_variant == "external_redirect":
                    self.send_response(302); self.send_header("Location","https://untrusted.example.invalid/apply"); self.end_headers(); return
                cookie=self.headers.get("Cookie", "")
                if stage != "1" and "ats_test_session=local-session" not in cookie:
                    self.send_response(409); self.end_headers(); return
                outer.data["visits"].append(stage); outer.data["navigation"].append(self.path)
                if stage == "2":
                    outer.data["page1_values"]={key: values[0] for key,values in parse_qs(parsed.query).items() if key in {"first","last","email","phone"}}
                if stage == "3":
                    outer.data["page2_values"]={key: values[0] for key,values in parse_qs(parsed.query).items() if key in {"notice","authorization","sponsorship"}}
                self.send_response(200)
                if stage == "1" and "ats_test_session=local-session" not in cookie:
                    self.send_header("Set-Cookie", "ats_test_session=local-session; Path=/")
                self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
                if stage == "success": outer.data["submit"] += 1
                if stage == "4": outer.data["review_clicks"] += 1
                self.wfile.write(outer.page_html(stage, outer.review_variant).encode())

            def do_POST(self):
                if self.path != "/observe":
                    self.send_response(404); self.end_headers(); return
                try:
                    payload=json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
                    if payload.get("field") in {"cv","cover"}: outer.data["uploads"][payload["field"]]=payload.get("filename", "")
                    self.send_response(204); self.end_headers()
                except (ValueError, UnicodeDecodeError):
                    self.send_response(400); self.end_headers()
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/greenhouse?stage=1"

    def close(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()


class LocalEmployerWrapper:
    """Separate localhost employer origin with evidence-marked Greenhouse Apply link."""
    def __init__(self, ats_url, variant="matching"):
        self.ats_url=ats_url; self.variant=variant; self.data={"visits":[]}

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                outer.data["visits"].append(self.path)
                if self.path.split("?",1)[0] != "/careers/finance-manager": self.send_response(404); self.end_headers(); return
                if outer.variant == "dead": self.send_response(410); self.end_headers(); self.wfile.write(b"Application closed"); return
                if outer.variant == "no_evidence": body='<main><h1>Test Company — Finance Manager</h1><a href="/apply">Apply for this job</a></main>'
                elif outer.variant == "conflict": body=f'<main data-portal="greenhouse"><div data-portal="workday"></div><a data-portal="greenhouse" href="{outer.ats_url}">Apply for this job</a></main>'
                elif outer.variant == "multiple": body=f'<main data-portal="greenhouse"><a data-portal="greenhouse" href="{outer.ats_url}">Apply for this job</a><a data-portal="greenhouse" href="{outer.ats_url}">Apply now</a></main>'
                elif outer.variant in {"captcha","login","mfa","account"}:
                    text={"captcha":"CAPTCHA required","login":"Sign in to continue","mfa":"Multi-factor verification code","account":"Create an account"}[outer.variant]
                    body=f'<main data-portal="greenhouse"><h1>{text}</h1><a data-portal="greenhouse" href="{outer.ats_url}">Apply for this job</a></main>'
                elif outer.variant == "external": body='<main data-portal="greenhouse"><a data-portal="greenhouse" href="https://untrusted.example.invalid/apply">Apply for this job</a></main>'
                else:
                    returned='<p>Application routing returned to careers.</p>' if outer.variant == "loop" and len(outer.data["visits"]) > 1 else ''
                    body=f'<main data-portal="greenhouse"><h1>Test Company — Finance Manager</h1><p>London, United Kingdom</p><p>Responsibilities and qualifications.</p>{returned}<a data-portal="greenhouse" href="{outer.ats_url}">Apply for this job</a></main>'
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/careers/finance-manager?gh_jid=12345"

    def close(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()


class LocalGreenhouseIframeWrapper:
    """Employer wrapper page embedding zero or more Greenhouse-style iframes.

    Distinct from LocalFrameSurface (Task 21.8C.1's generic Page/Frame smoke
    fixture): this fixture carries real portal-evidence markers so the
    trusted-frame selector (Task 21.8C.2) can be exercised deterministically.
    """
    GREENHOUSE_FORM='<main data-portal="greenhouse" class="greenhouse application_form"><form><label for="first">First name</label><input id="first" name="first" required></form></main>'
    CAPTCHA_FRAME='<main><h1>CAPTCHA required</h1><p>Verify you are human.</p></main>'
    UNRELATED_FRAME='<main><p>Ad content</p></main>'

    def __init__(self, variant="single"):
        self.variant=variant
        self.data={"visits":[]}

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                path=urlparse(self.path).path
                outer.data["visits"].append(path)
                body=outer._body(path)
                if body is None: self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/careers"

    def _body(self, path):
        if path == "/careers":
            frames={
                "single":'<iframe src="/app_a"></iframe>',
                "with_unrelated":'<iframe src="/ad"></iframe><iframe src="/app_a"></iframe>',
                "none":'<iframe src="/ad"></iframe>',
                "ambiguous":'<iframe src="/app_a"></iframe><iframe src="/app_b"></iframe>',
                "with_captcha":'<iframe src="/captcha"></iframe><iframe src="/app_a"></iframe>',
            }[self.variant]
            return f'<main><h1>Careers</h1>{frames}</main>'
        if path in {"/app_a", "/app_b"}: return self.GREENHOUSE_FORM
        if path == "/captcha": return self.CAPTCHA_FRAME
        if path == "/ad": return self.UNRELATED_FRAME
        return None

    def close(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()


class LocalGreenhouseIframeEmployer:
    """Employer wrapper page embedding a full multi-stage LocalATS instance as a
    trusted cross-origin iframe, alongside one unrelated iframe.

    Unlike LocalGreenhouseIframeWrapper (Task 21.8C.2's single-stage selection
    fixture), this reuses the existing LocalATS server verbatim as the iframe
    content source so the Task 21.8C.3 E2E test exercises the real multi-page
    Greenhouse fixture through a real trusted Frame.
    """
    def __init__(self, ats_url):
        self.ats_url=ats_url
        self.data={"visits":[]}

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                path=urlparse(self.path).path
                outer.data["visits"].append(path)
                if path == "/careers":
                    body=f'<main><h1>Test Company — Finance Manager</h1><iframe src="/ad"></iframe><iframe src="{outer.ats_url}"></iframe></main>'
                elif path == "/ad":
                    body='<main><p>Ad content</p></main>'
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/careers"

    def close(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()


class LocalFrameSurface:
    """Tiny localhost Page/Frame smoke fixture; it is not an ATS topology."""
    def __init__(self):
        self.data={"visits":[]}

    def start(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                parsed=urlparse(self.path); outer.data["visits"].append(parsed.path+"?"+parsed.query)
                if parsed.path == "/outer":
                    body=(f'<main><button id="remove" onclick="document.getElementById(\'application-frame\').remove()">Remove</button>'
                          f'<iframe id="application-frame" src="/inner?stage=1"></iframe></main>')
                elif parsed.path == "/inner" and parse_qs(parsed.query).get("stage", ["1"])[0] == "1":
                    body='''<main data-portal="greenhouse" class="greenhouse application_form"><form action="/inner" method="get">
                    <input type="hidden" name="stage" value="2"><label for="first">First name</label><input id="first" name="first" required>
                    <label for="auth">Authorized to work in the UK?</label><select id="auth" name="auth"><option>Yes</option><option>No</option></select>
                    <label for="cv">Resume</label><input id="cv" type="file" required><button type="submit">Continue</button></form></main>'''
                elif parsed.path == "/inner":
                    body='<main data-portal="greenhouse"><h1>Frame stage 2</h1></main>'
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/outer"

    def close(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
