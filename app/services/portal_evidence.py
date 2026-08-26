"""Evidence-based application-portal detection; no navigation or filling."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.models.portal_evidence import PortalEvidence


def detect_portal_evidence(url: str, html: str = "") -> PortalEvidence:
    parsed=urlparse(url); host=parsed.netloc.lower(); query=parse_qs(parsed.query); body=html.lower()
    signals=[]
    if "greenhouse.io" in host: signals.append("GREENHOUSE_HOST")
    if "gh_jid" in query: signals.append("QUERY_PARAM_GH_JID")
    if re.search(r"<(?:iframe)[^>]+(?:src|data-src)=[^>]*greenhouse\.io", body): signals.append("GREENHOUSE_IFRAME")
    if re.search(r"<form[^>]+action=[^>]*greenhouse\.io", body): signals.append("GREENHOUSE_FORM_ACTION")
    if re.search(r"<(?:script)[^>]+src=[^>]*greenhouse", body): signals.append("GREENHOUSE_SCRIPT")
    if re.search(r"(?:data-ats|data-provider|data-portal)=[\"']greenhouse", body): signals.append("GREENHOUSE_DATA_ATTRIBUTE")
    if re.search(r"(?:iframe|form|script|href)=[^>]*greenhouse\.io", body): signals.append("GREENHOUSE_EMBED_OR_LINK")
    strong={"GREENHOUSE_HOST","GREENHOUSE_IFRAME","GREENHOUSE_FORM_ACTION","GREENHOUSE_DATA_ATTRIBUTE"}
    if any(item in strong for item in signals):
        return PortalEvidence("GREENHOUSE", "HIGH", signals, "GREENHOUSE_HOST" not in signals)
    if "QUERY_PARAM_GH_JID" in signals and "GREENHOUSE_EMBED_OR_LINK" in signals:
        return PortalEvidence("GREENHOUSE", "HIGH", signals, True)
    if "QUERY_PARAM_GH_JID" in signals:
        return PortalEvidence("GREENHOUSE", "MEDIUM", signals, True)
    if "lever.co" in host or re.search(r"lever-application|jobs\.lever\.co", body):
        return PortalEvidence("LEVER", "HIGH" if "lever.co" in host else "MEDIUM", ["LEVER_HOST_OR_DOM"], False)
    return PortalEvidence()
